"""Proper checks of every saved video before it counts as a verified permanent copy.

A saved copy is only "verified" when it has picture and sound (unless the owner
confirms the source genuinely has none), the sound is not silent for the whole
length, the picture and sound last as long as each other (and as the source when
its length is known), and the file the players receive decodes from start to end.

Problems are written in plain words ("Problem: no sound - ...") and an import
with a problem is downloaded again automatically a limited number of times.
"""
import json
import re
import shutil
import subprocess
import tempfile
import threading
import time
import uuid

import admin_store as store

OK, PROBLEM, ACCEPTED, CHECKING, PENDING = "ok", "problem", "accepted", "checking", "pending"
PENDING_RETRY = 1800      # A check that couldn't finish (low space, busy disk) runs again after this.
GOOD = (OK, ACCEPTED)
SILENT_DB = -60.0          # Loudest moment quieter than this for the whole file = no audible sound.
QUIET_LUFS = -27.0         # Typical phone videos sit near -14 to -16 LUFS; below this, raise it in the playback copy.
MAX_DOWNLOADS = 3          # First download plus two automatic re-downloads.
RETRY_DELAY = 600          # Seconds between automatic re-downloads.
STALE_SECONDS = 3 * 3600
CHECK_LOCK = threading.Lock()
RUN_LOCK = threading.Lock()
WAKE = threading.Event()
SILENCE_CODES = ("no-sound", "silent")

MEDIA_COLUMNS = {"check_status": "TEXT", "check_code": "TEXT NOT NULL DEFAULT ''",
                 "check_reason": "TEXT NOT NULL DEFAULT ''", "check_details": "TEXT", "check_updated": "REAL",
                 "silent_ok": "INTEGER NOT NULL DEFAULT 0"}
JOB_COLUMNS = {"download_attempts": "INTEGER NOT NULL DEFAULT 0", "return_status": "TEXT"}


class Problem(ValueError):
    def __init__(self, code, reason, details=None):
        super().__init__(reason)
        self.code, self.reason, self.details = code, reason, details or {}


def init(c):
    for table, wanted in (("media", MEDIA_COLUMNS), ("jobs", JOB_COLUMNS)):
        have = {r[1] for r in c.execute(f"PRAGMA table_info({table})")}
        for name, definition in wanted.items():
            if name not in have:
                c.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")
    c.execute("""CREATE TABLE IF NOT EXISTS check_runs (id TEXT PRIMARY KEY, started REAL NOT NULL,
                 finished REAL, total INTEGER NOT NULL, automatic INTEGER NOT NULL DEFAULT 0, release TEXT)""")
    # Run the full check over every saved copy once after installing this release.
    if not c.execute("SELECT 1 FROM check_runs WHERE automatic=1 AND release=?", (store.RELEASE,)).fetchone():
        total = c.execute("SELECT count(*) FROM media").fetchone()[0]
        c.execute("UPDATE media SET check_status=NULL")
        c.execute("INSERT INTO check_runs VALUES(?,?,?,?,1,?)",
                  (uuid.uuid4().hex, time.time(), None if total else time.time(), total, store.RELEASE))


def _nice(cmd):
    return (["nice", "-n", "10"] + cmd) if shutil.which("nice") else cmd


def clock(seconds):
    seconds = int(round(seconds or 0))
    return f"{seconds // 3600}:{seconds // 60 % 60:02d}:{seconds % 60:02d}" if seconds >= 3600 else f"{seconds // 60}:{seconds % 60:02d}"


def margin(duration):
    return max(2.0, 0.05 * (duration or 0))


def _float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def analyse_audio(path, stream):
    """Decode the whole first audio track: loudest moment, average level, loudness and real length."""
    timeout = max(300, int(_float(stream.get("duration")) * 2))
    result = subprocess.run(_nice(["ffmpeg", "-nostats", "-hide_banner", "-v", "info", "-i", str(path), "-map", "0:a:0",
                                   "-vn", "-af", "volumedetect,ebur128=framelog=quiet", "-f", "null", "-"]),
                            capture_output=True, text=True, errors="replace", timeout=timeout)
    text = result.stderr
    found = lambda pattern: re.findall(pattern, text)
    max_db = found(r"max_volume: (-?[\d.]+|-inf) dB")
    mean_db = found(r"mean_volume: (-?[\d.]+|-inf) dB")
    samples = found(r"n_samples: (\d+)")
    lufs = found(r"I:\s+(-?[\d.]+|-inf) LUFS")
    if result.returncode != 0 or not samples:
        raise Problem("damaged", "Problem: damaged sound - the audio track can't be decoded.")
    level = lambda values: -120.0 if not values or values[-1] == "-inf" else float(values[-1])
    rate, channels = int(stream.get("sample_rate") or 0), int(stream.get("channels") or 0)
    seconds = int(samples[-1]) / (rate * channels) if rate and channels else _float(stream.get("duration"))
    return {"max_db": level(max_db), "mean_db": level(mean_db), "lufs": level(lufs), "seconds": round(seconds, 2)}


def decode(path, duration):
    """Play the whole file through the decoder. Returns how far it got and how many errors it reported."""
    timeout = max(600, int(duration * 6))
    result = subprocess.run(_nice(["ffmpeg", "-nostats", "-hide_banner", "-v", "error", "-progress", "pipe:1",
                                   "-i", str(path), "-map", "0:v:0", "-map", "0:a:0?", "-f", "null", "-"]),
                            capture_output=True, text=True, errors="replace", timeout=timeout)
    times = [int(t) for t in re.findall(r"out_time_us=(\d+)", result.stdout)]
    errors = [line for line in result.stderr.splitlines() if line.strip()]
    return {"seconds": round(max(times, default=0) / 1e6, 2), "errors": len(errors),
            "ok": result.returncode == 0, "first_error": errors[0][:200] if errors else ""}


def inspect(path, expected=None, full_decode=True, allow_silent=False):
    """Check one file. Returns its details or raises Problem with a plain-language reason."""
    import playback
    try:
        info = playback.probe(path)
    except Exception:
        raise Problem("unreadable", "Problem: unreadable - the file can't be opened as a video.")
    streams = info.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video" and not (s.get("disposition") or {}).get("attached_pic")), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    container = _float(info.get("format", {}).get("duration"))
    details = {"video_codec": video.get("codec_name") if video else None,
               "audio_codec": audio.get("codec_name") if audio else None,
               "audio_profile": audio.get("profile") if audio else None,
               "channels": int(audio.get("channels") or 0) if audio else 0,
               "duration": round(container, 2)}
    if not video:
        raise Problem("no-video", "Problem: no picture - the file has no video track.", details)
    picture = _float(video.get("duration")) or container
    details["video_seconds"] = round(picture, 2)
    if picture <= 0:
        raise Problem("incomplete", "Problem: incomplete - the file has no playable length.", details)
    if audio:
        details.update(analyse_audio(path, audio))
        details["browser_audio"] = audio.get("codec_name") == "aac" and audio.get("profile") in (None, "LC", "HE-AAC", "HE-AACv2")
        if details["max_db"] <= SILENT_DB and not allow_silent:
            raise Problem("silent", "Problem: no sound - the audio track is silent for the whole video.", details)
        details["quiet"] = details["max_db"] > SILENT_DB and details["lufs"] < QUIET_LUFS
        if abs(details["seconds"] - picture) > margin(picture):
            raise Problem("incomplete", f"Problem: incomplete - the sound lasts {clock(details['seconds'])} "
                                        f"but the picture lasts {clock(picture)}.", details)
    elif not allow_silent:
        raise Problem("no-sound", "Problem: no sound - the file has no audio track.", details)
    if expected and abs(container - expected) > margin(expected):
        raise Problem("incomplete", f"Problem: incomplete - the file is {clock(container)} long "
                                    f"but the source is {clock(expected)}.", details)
    if full_decode:
        played = decode(path, container)
        details["decoded_seconds"], details["decode_errors"] = played["seconds"], played["errors"]
        if not played["ok"] or played["seconds"] < picture - margin(picture):
            raise Problem("damaged", f"Problem: damaged - playback stops at {clock(played['seconds'])} "
                                     f"of {clock(picture)}.", details)
        if played["errors"] > 20:
            raise Problem("damaged", f"Problem: damaged - {played['errors']} errors while playing the file.", details)
    return details


def summary(details, accepted=False):
    if accepted:
        return "Checked: plays start to end. You confirmed the original has no sound."
    sound = "no sound track" if not details.get("audio_codec") else f"sound ({details['audio_codec']})"
    extra = []
    if details.get("audio_codec") and not details.get("browser_audio"):
        extra.append("sound converted for browsers")
    if details.get("quiet"):
        extra.append("quiet sound raised in the playback copy")
    return (f"Checked: picture ({details.get('video_codec')}) and {sound} play start to end, "
            f"{clock(details.get('duration'))} long" + (" - " + ", ".join(extra) if extra else "") + ".")


def _set(key, **values):
    values["check_updated"] = time.time()
    if "check_details" in values and not isinstance(values["check_details"], (str, type(None))):
        values["check_details"] = json.dumps(values["check_details"])
    with store.db() as c:
        c.execute("UPDATE media SET " + ",".join(f"{k}=?" for k in values) + " WHERE source_key=?", (*values.values(), key))


def row(key):
    with store.db() as c:
        r = c.execute("SELECT * FROM media WHERE source_key=?", (key,)).fetchone()
    return dict(r) if r else None


def verify(key, expected=None):
    """Full check of a saved copy, making the browser-friendly playback copy when needed.

    Returns {"state": ok|accepted|problem|pending, "code", "reason"}. "pending" means the file
    is fine so far but its playback copy could not be made yet (for example low disk space).
    """
    import media_worker as worker
    import playback
    with CHECK_LOCK:
        m = row(key)
        if not m:
            return {"state": PROBLEM, "code": "missing", "reason": "Problem: missing - no saved copy is recorded."}
        accepted = bool(m.get("silent_ok"))
        _set(key, check_status=CHECKING)
        try:
            client = worker.storage_client()
            with tempfile.TemporaryDirectory(prefix="waw-check-", dir=store.STATE) as folder:
                try:
                    original = playback._local_original(client, m, folder)
                except FileNotFoundError as e:
                    raise Problem("missing", "Problem: missing - " + str(e)[0].lower() + str(e)[1:])
                details = inspect(original, expected=expected, full_decode=False, allow_silent=accepted)
                _set(key, check_details=details)
                force = details.get("quiet") and "quiet sound raised" not in (m.get("play_reason") or "")
                prepared = playback.prepare(key, force=bool(force))
                if prepared["state"] != playback.READY:
                    if prepared["state"] in (playback.MISSING, playback.UNSUPPORTED):
                        raise Problem("unsupported" if prepared["state"] == playback.UNSUPPORTED else "missing",
                                      "Problem: won't play - " + prepared["reason"], details)
                    _set(key, check_status=PENDING, check_code="", check_reason=prepared["reason"])
                    return {"state": "pending", "code": "", "reason": prepared["reason"]}
                m = row(key)
                play_key = m["play_key"] or m["object_key"]
                if play_key == m["object_key"]:
                    playing = original
                else:
                    playing = playback._local_original(client, {**m, "object_key": play_key, "size": m["play_size"]}, folder)
                checked = inspect(playing, expected=details["duration"], full_decode=True, allow_silent=accepted)
                details.update(decoded_seconds=checked["decoded_seconds"], decode_errors=checked["decode_errors"],
                               playback_audio=checked.get("audio_codec"))
                state = ACCEPTED if accepted else OK
                _set(key, check_status=state, check_code="", check_reason=summary(details, accepted), check_details=details)
                return {"state": state, "code": "", "reason": summary(details, accepted)}
        except Problem as p:
            _set(key, check_status=PROBLEM, check_code=p.code, check_reason=p.reason,
                 check_details=p.details or None)
            return {"state": PROBLEM, "code": p.code, "reason": p.reason}
        except subprocess.TimeoutExpired:
            _set(key, check_status=PENDING, check_code="", check_reason="The check took too long; it will run again.")
            return {"state": "pending", "code": "", "reason": "The check took too long; it will run again."}
        except Exception:
            _set(key, check_status=PENDING, check_code="", check_reason="The data folder couldn't be read during the check; it will run again.")
            return {"state": "pending", "code": "", "reason": "The data folder couldn't be read during the check; it will run again."}


def state(m):
    if not m:
        return {"state": None, "code": "", "reason": ""}
    status = m.get("check_status")
    if status == CHECKING and (m.get("check_updated") or 0) < time.time() - STALE_SECONDS:
        status = None
    return {"state": status, "code": m.get("check_code") or "", "reason": m.get("check_reason") or ""}


# --- what happens to the import ---------------------------------------------------------

def finish_import(job, result):
    """Record the outcome of a check on the job that downloaded the file."""
    if result["state"] in GOOD:
        previous = job.get("return_status")
        store.update_job(job["id"], status="published" if previous == "published" else "imported",
                         reason=("Downloaded again and verified. " if previous else "Verified permanent copy. ") + result["reason"],
                         return_status=None)
        return True
    if result["state"] == "pending":
        store.update_job(job["id"], status="imported", reason="Saved, but not verified yet: " + result["reason"])
        return False
    flag_problem(job, result["reason"])
    return False


def flag_problem(job, reason):
    attempts = max(1, int(job.get("download_attempts") or 0))
    previous = job.get("return_status") or (job["status"] if job["status"] in ("imported", "published") else None)
    if attempts < MAX_DOWNLOADS:
        reason += f" Downloading again automatically (try {attempts + 1} of {MAX_DOWNLOADS}) in about {RETRY_DELAY // 60} minutes."
    else:
        reason += f" Downloaded {attempts} times with the same result. Use Download again"
        reason += (", or Keep without sound if the original really has no sound." if any(
            w in reason for w in ("no sound", "silent")) else ".")
    store.update_job(job["id"], status="problem", reason=reason, download_attempts=attempts, return_status=previous)


def prepare_redownload(job):
    """Turn a problem item into a fresh request to the media service (new request, same settings)."""
    attempts = int(job.get("download_attempts") or 0) + 1
    store.update_job(job["id"], status="queued", upstream_id=None, idempotency="waw-" + uuid.uuid4().hex,
                     download_attempts=attempts, reason=f"Downloading again (try {attempts} of {MAX_DOWNLOADS})")
    return store.get_job(job["id"])


def request_redownload(job):
    """Owner asked for a new download. A published copy keeps playing until a verified one replaces it."""
    if job["status"] in ("queued", "processing", "copying"):
        return False
    previous = job.get("return_status") or (job["status"] if job["status"] in ("imported", "published") else None)
    store.update_job(job["id"], status="problem", download_attempts=0, return_status=previous,
                     reason="Download again requested.")
    with store.db() as c:
        c.execute("UPDATE jobs SET updated=0 WHERE id=?", (job["id"],))
    return True


def accept_silent(job):
    m = row(job["source_key"])
    if not m or m.get("check_code") not in SILENCE_CODES:
        raise ValueError("Only a saved copy with no sound can be kept without sound")
    _set(job["source_key"], silent_ok=1, check_status=None, check_code="",
         check_reason="You confirmed the original has no sound. Checking it plays.")
    WAKE.set()


def start_check(key):
    _set(key, check_status=None)
    WAKE.set()


# --- background checks -------------------------------------------------------------------


def run_all():
    """Owner pressed "Check all saved videos": check every saved copy again, one at a time."""
    with RUN_LOCK, store.db() as c:
        total = c.execute("SELECT count(*) FROM media").fetchone()[0]
        c.execute("UPDATE media SET check_status=NULL")
        c.execute("UPDATE check_runs SET finished=? WHERE finished IS NULL", (time.time(),))
        c.execute("INSERT INTO check_runs VALUES(?,?,?,?,0,?)", (uuid.uuid4().hex, time.time(), None, total, store.RELEASE))
    WAKE.set()
    return report()


def _next():
    with store.db() as c:
        r = c.execute("""SELECT m.source_key, j.status FROM media m LEFT JOIN jobs j ON j.source_key=m.source_key
            WHERE (m.check_status IS NULL OR (m.check_status=? AND COALESCE(m.check_updated,0) < ?)
                   OR (m.check_status=? AND COALESCE(m.check_updated,0) < ?))
              AND COALESCE(j.status,'') NOT IN ('queued','processing','copying')
            ORDER BY m.created LIMIT 1""", (CHECKING, time.time() - STALE_SECONDS,
                                                 PENDING, time.time() - PENDING_RETRY)).fetchone()
    return r["source_key"] if r else None


def check_saved(key):
    """Background check of an already saved copy, then update its import record."""
    result = verify(key)
    with store.db() as c:
        job = c.execute("SELECT * FROM jobs WHERE source_key=?", (key,)).fetchone()
    if not job:
        return result
    job = dict(job)
    if result["state"] in GOOD and job["status"] == "problem":
        finish_import(job, result)
    elif result["state"] in GOOD and job["status"] == "imported":
        finish_import(job, result)  # Shows what was checked instead of an older, vaguer reason.
    elif result["state"] == PROBLEM and job["status"] in ("imported", "published", "problem"):
        if job["status"] == "published":
            # Already public: keep it public and list it for the owner rather than re-downloading on its own.
            store.update_job(job["id"], reason=result["reason"] + " Use Download again in Saved video checks.")
        elif job["status"] == "imported":
            flag_problem(job, result["reason"])
    return result


def backfill_loop():
    while True:
        try:
            key = _next()
            if key:
                check_saved(key)
                continue
            with store.db() as c:
                c.execute("UPDATE check_runs SET finished=? WHERE finished IS NULL", (time.time(),))
        except Exception:
            pass
        WAKE.wait(30)
        WAKE.clear()


def report():
    with store.db() as c:
        run = c.execute("SELECT * FROM check_runs ORDER BY started DESC LIMIT 1").fetchone()
        rows = [dict(r) for r in c.execute("""SELECT m.*, j.id AS job_id, j.title, j.status AS job_status,
                 j.reason AS job_reason, j.url FROM media m LEFT JOIN jobs j ON j.source_key=m.source_key ORDER BY m.created""")]
    counts = {}
    problems = []
    for r in rows:
        st = state(r)["state"] or "waiting"
        counts[st] = counts.get(st, 0) + 1
        if st == PROBLEM:
            problems.append({"job_id": r["job_id"], "title": r["title"] or r["source_key"], "url": r["url"],
                             "code": r["check_code"], "reason": r["check_reason"], "published": r["job_status"] == "published",
                             "status": r["job_status"], "redownloading": r["job_status"] in ("queued", "processing", "copying")
                             or (r["job_status"] == "problem" and (r["job_reason"] or "").startswith(("Download again requested", "Downloading again"))),
                             "can_keep_silent": r["check_code"] in SILENCE_CODES})
    remaining = counts.get("waiting", 0) + counts.get(CHECKING, 0)
    return {"total": len(rows), "checked": len(rows) - remaining, "remaining": remaining,
            "running": bool(remaining), "counts": counts, "problems": problems,
            "run": dict(run) if run else None, "max_downloads": MAX_DOWNLOADS}
