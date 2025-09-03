// /videos.js
/* Minimal, robust, baseline-compatible loader + renderer.
   Why: Supports legacy array JSON, {popup,videos}, and [ {popup,videos} ].
*/

(() => {
  /* ===== DOM ===== */
  const els = {
    pinned: document.getElementById("pinnedGrid"),
    grid: document.getElementById("videoGrid"),
    featured: document.getElementById("featured"),
    fbox: document.getElementById("featuredContainer"),
    playlist: document.getElementById("playlistBox"),
    pbox: document.getElementById("playlistContainer"),
    search: document.getElementById("searchInput"),
    cat: document.getElementById("categoryFilter"),
    sort: document.getElementById("sortFilter"),
  };

  /* ===== State ===== */
  let APP_POPUP = { enabled: false, message: "" };
  let videos = [];

  /* ===== Utils ===== */
  const trimStr = (s) => (typeof s === "string" ? s.trim() : s);
  const isYoutubeId = (s) => typeof s === "string" && /^[\w-]{11}$/.test(s.trim());
  const toArray = (x) => (x == null ? [] : Array.isArray(x) ? x : [x]);

  function normalizeVideo(v) {
    const n = { ...v };
    n.title = trimStr(n.title) || "Untitled";
    const yt = trimStr(n.youtube_id);
    const url = trimStr(n.url);

    // Why: occasionally an 11-char YT id is placed into `url`
    n.youtube_id = isYoutubeId(yt) ? yt : isYoutubeId(url) ? url : yt;
    n.url = isYoutubeId(url) ? undefined : url;

    const cats = toArray(n.category).map(trimStr).filter(Boolean);
    n.category = cats.length ? cats : undefined;

    n.tags = (n.tags || []).map(trimStr).filter(Boolean);
    n.featured = Boolean(n.featured);
    n.pinned = Boolean(n.pinned);
    return n;
  }

  function tagHTML(t) {
    return `<span class="tag" data-tag="${t.replace(/^#/, "")}">#${t.replace(/^#/, "")}</span>`;
  }

  function embed(v) {
    if (v.youtube_id) {
      if (String(v.youtube_id).startsWith("PL")) {
        return `<iframe width="100%" height="315" src="https://www.youtube.com/embed/videoseries?list=${encodeURIComponent(
          v.youtube_id
        )}" frameborder="0" allowfullscreen loading="lazy"></iframe>`;
      }
      return `<iframe width="100%" height="315" src="https://www.youtube.com/embed/${encodeURIComponent(
        v.youtube_id
      )}" frameborder="0" allowfullscreen loading="lazy"></iframe>`;
    }
    if (v.url) return `<video src="${v.url}" controls style="width:100%;height:auto;" preload="metadata"></video>`;
    return `<div class="meta">No source</div>`;
  }

  async function shareVideo(v) {
    const url = v.youtube_id ? `https://youtu.be/${v.youtube_id}` : location.href;
    try {
      await navigator.clipboard.writeText(url);
      alert("Link copied!");
    } catch {
      // Why: Safari / HTTP contexts may block clipboard
      prompt("Copy link:", url);
    }
  }

  function cardHTML(v, idx) {
    const slug = (v.title || "Untitled").toLowerCase().replace(/[^a-z0-9]+/g, "-");
    return `
      <div class="card" id="${slug}">
        <h3>${v.title || "Untitled"}</h3>
        ${embed(v)}
        <div class="meta">${Array.isArray(v.category) ? v.category.join(", ") : v.category || ""}</div>
        <div class="tags">${(v.tags || []).map(tagHTML).join("")}</div>
        <button class="btn" data-share="${idx}">🔗 Share</button>
      </div>
    `;
  }

  /* ===== Popup ===== */
  function maybeShowPopupOnce() {
    if (!APP_POPUP.enabled) return;
    if (!document.referrer.includes("choice.html")) return; // Why: greet only after red-pill
    if (localStorage.getItem("popupShown")) return;
    setTimeout(() => {
      alert(APP_POPUP.message || "👋 Welcome!");
      localStorage.setItem("popupShown", "true");
    }, 600);
  }

  /* ===== Render ===== */
  function render() {
    const q = (els.search.value || "").toLowerCase().trim();
    const cat = els.cat.value;
    const sort = els.sort.value || "random";

    // Featured
    const f = videos.find((v) => v.featured);
    if (f) {
      els.featured.style.display = "";
      els.fbox.innerHTML = embed(f);
    } else {
      els.featured.style.display = "none";
      els.fbox.innerHTML = "";
    }

    // Filter
    let list = videos.filter((v) => {
      const matchesQ =
        !q ||
        (v.title && v.title.toLowerCase().includes(q)) ||
        (v.tags || []).some((t) => (t || "").toLowerCase().includes(q.replace(/^#/, "")));
      const matchesC = !cat || (Array.isArray(v.category) ? v.category.includes(cat) : v.category === cat);
      return matchesQ && matchesC;
    });

    // Sort
    if (sort === "newest") list.sort((a, b) => new Date(b.added || 0) - new Date(a.added || 0));
    else if (sort === "oldest") list.sort((a, b) => new Date(a.added || 0) - new Date(b.added || 0));
    else if (sort === "az") list.sort((a, b) => (a.title || "").localeCompare(b.title || ""));
    else if (sort === "za") list.sort((a, b) => (b.title || "").localeCompare(a.title || ""));
    else if (sort === "views") list.sort((a, b) => (b.views || 0) - (a.views || 0));
    else if (sort === "random") list.sort(() => Math.random() - 0.5);

    // Pinned vs others (exclude featured)
    const pinned = list.filter((v) => v.pinned && !v.featured);
    const others = list.filter((v) => !v.pinned && !v.featured);

    els.pinned.innerHTML = pinned.map(cardHTML).join("");
    els.grid.innerHTML = others.map(cardHTML).join("");

    // Wire tag clicks + share buttons
    document.querySelectorAll(".tag").forEach((el) => {
      el.onclick = () => {
        els.search.value = el.dataset.tag || "";
        render();
      };
    });
    document.querySelectorAll("[data-share]").forEach((el) => {
      el.onclick = () => {
        const idx = Number(el.getAttribute("data-share"));
        if (!Number.isNaN(idx)) shareVideo(videos[idx]);
      };
    });
  }

  /* ===== Load JSON ===== */
  fetch("videos.json", { cache: "no-store" })
    .then((r) => r.json())
    .then((d) => {
      // Shapes: [ { popup, videos } ] | { popup, videos } | Video[]
      if (Array.isArray(d)) {
        if (d.length === 1 && d[0] && typeof d[0] === "object" && Array.isArray(d[0].videos)) {
          APP_POPUP = d[0].popup || { enabled: false, message: "" };
          videos = d[0].videos.map(normalizeVideo);
        } else {
          APP_POPUP = { enabled: false, message: "" };
          videos = d.map(normalizeVideo);
        }
      } else if (d && typeof d === "object") {
        APP_POPUP = d.popup || { enabled: false, message: "" };
        videos = Array.isArray(d.videos) ? d.videos.map(normalizeVideo) : [];
      } else {
        APP_POPUP = { enabled: false, message: "" };
        videos = [];
      }

      // Playlists
      const playlists = videos.filter((v) => v.youtube_id && String(v.youtube_id).startsWith("PL"));
      if (playlists.length) {
        els.playlist.style.display = "";
        els.pbox.innerHTML = playlists
          .map((v) => `<div class="card">${embed(v)}<div class="meta">Playlist</div></div>`)
          .join("");
      }

      // Categories
      const cats = [
        ...new Set(
          videos
            .flatMap((v) => (Array.isArray(v.category) ? v.category : v.category ? [v.category] : []))
            .map((c) => (typeof c === "string" ? c.trim() : c))
            .filter(Boolean)
        ),
      ].sort((a, b) => a.localeCompare(b));
      cats.forEach((c) => {
        const opt = document.createElement("option");
        opt.value = c;
        opt.textContent = c;
        els.cat.appendChild(opt);
      });

      maybeShowPopupOnce();
      render();

      // Hash scroll
      if (location.hash) {
        const target = document.getElementById(location.hash.slice(1));
        if (target) target.scrollIntoView({ behavior: "smooth", block: "start" });
      }
    })
    .catch((err) => {
      console.error("Failed to load videos.json", err);
      alert("Failed to load videos. Please try again later.");
    });

  /* ===== Events ===== */
  els.search.addEventListener("input", render);
  els.cat.addEventListener("change", render);
  els.sort.addEventListener("change", render);
})();