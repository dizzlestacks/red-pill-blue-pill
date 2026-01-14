// videos.js — V4 (KEEP_THUMBNAILS) FINAL DROP-IN
// - Thumbnail-first (iframes only load on tap) => iPhone scroll safe
// - Stable key mapping (fixes index drift / wrong loads)
// - Tags hidden on cards (still searchable)
// - Featured section hidden (compat)
// - De-duped categories (case + whitespace)
// - All / Pinned / Playlists / Trending / New views
// - Share + Open buttons
// - Optional "I'm Feeling Lucky" (button id="luckyBtn")
// - Marks video watched after 5 minutes from playback start (localStorage)
// - FIX: Title is ABOVE thumbnail
// - FIX: Playlists are NOT shown on main page (All), only via Playlists tab

(() => {
  "use strict";

  /* ============================
     DOM REFERENCES
     ============================ */
  const els = {
    loading: document.getElementById("loading"),

    // Controls
    search: document.getElementById("searchInput"),
    category: document.getElementById("categoryFilter"),
    sort: document.getElementById("sortFilter"),
    clearFilters: document.getElementById("clearFilters"),
    resultCount: document.getElementById("resultCount"),

    // Tabs
    tabAll: document.getElementById("tab-all"),
    tabPinned: document.getElementById("tab-pinned"),
    tabPlaylists: document.getElementById("tab-playlists"),
    tabTrending: document.getElementById("tab-trending"),
    tabNew: document.getElementById("tab-new"),

    // Sections
    featured: document.getElementById("featured"),
    featuredContainer: document.getElementById("featuredContainer"),

    playlistBox: document.getElementById("playlistBox"),
    playlistToggle: document.getElementById("playlistToggle"),
    playlistContainer: document.getElementById("playlistContainer"),
    playlistGrid: document.getElementById("playlistGrid"),
    videoGrid: document.getElementById("videoGrid"),

    // Popup
    popupOverlay: document.getElementById("popupOverlay"),
    popupMsg: document.getElementById("popupMsg"),
    popupClose: document.getElementById("popupClose"),

    // Optional
    luckyBtn: document.getElementById("luckyBtn")
  };

  /* ============================
     STATE
     ============================ */
  const state = {
    popup: { enabled: false, message: "" },
    videos: [],
    playlists: [],
    view: "all", // "all" | "pinned" | "playlists" | "trending" | "new"
    playlistsOpen: false // kept (even though main page no longer shows playlists)
  };

  const CACHE_KEY = "videosCacheV4";
  const WATCHED_KEY = "videosWatchedV4"; // localStorage: { [key]: { t:number } }

  /* ============================
     HELPERS: SAFE / CACHE
     ============================ */
  function safeJsonParse(str, fallback = null) {
    try { return JSON.parse(str); } catch { return fallback; }
  }

  function readCache() {
    const raw = localStorage.getItem(CACHE_KEY);
    if (!raw) return null;
    return safeJsonParse(raw, null);
  }

  function writeCache(data) {
    try {
      localStorage.setItem(CACHE_KEY, JSON.stringify({ time: Date.now(), data }));
    } catch {
      // ignore quota errors
    }
  }

  function normalizeVideosPayload(d) {
    let popup = { enabled: false, message: "" };
    let videos = [];

    if (Array.isArray(d)) {
      if (d.length === 1 && d[0] && typeof d[0] === "object" && Array.isArray(d[0].videos)) {
        popup = d[0].popup || popup;
        videos = d[0].videos || [];
      } else {
        videos = d;
      }
    } else if (d && typeof d === "object") {
      popup = d.popup || popup;
      videos = Array.isArray(d.videos) ? d.videos : [];
    }

    return { popup, videos };
  }

  function escapeHTML(s) {
    return String(s ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#39;");
  }

  function escapeAttr(s) {
    return String(s ?? "").replaceAll('"', "&quot;");
  }

  /* ============================
     VIDEO ID / CATEGORY
     ============================ */
  function isPlaylist(v) {
    return v.youtube_id && String(v.youtube_id).startsWith("PL");
  }

  function categoryArray(v) {
    if (!v.category) return [];
    return Array.isArray(v.category) ? v.category.filter(Boolean) : [v.category];
  }

  // Stable key to prevent index drift bugs during sort/filter
  function videoKey(v) {
    if (v.youtube_id) return `yt:${String(v.youtube_id)}`;
    if (v.url) return `url:${String(v.url)}`;
    const t = (v.title || "").trim();
    const a = (v.added || "").trim();
    return `meta:${t}::${a}`;
  }

  /* ============================
     WATCHED (5 minutes)
     ============================ */
  function readWatchedMap() {
    return safeJsonParse(localStorage.getItem(WATCHED_KEY) || "{}", {});
  }

  function writeWatchedMap(map) {
    try { localStorage.setItem(WATCHED_KEY, JSON.stringify(map)); } catch {}
  }

  function markWatchedAfter5Min(key) {
    const watched = readWatchedMap();
    if (watched[key]) return;

    setTimeout(() => {
      const fresh = readWatchedMap();
      if (fresh[key]) return;
      fresh[key] = { t: Date.now() };
      writeWatchedMap(fresh);
    }, 5 * 60 * 1000);
  }

  /* ============================
     SHARE / OPEN
     ============================ */
  function shareLink(v) {
    if (v.youtube_id) {
      if (isPlaylist(v)) return `https://www.youtube.com/playlist?list=${v.youtube_id}`;
      return `https://youtu.be/${v.youtube_id}`;
    }
    if (v.url) return v.url;
    return location.href;
  }

  async function shareVideo(v) {
    const url = shareLink(v);
    try {
      await navigator.clipboard.writeText(url);
      alert("Link copied!");
    } catch {
      prompt("Copy link:", url);
    }
  }

  /* ============================
     THUMBNAIL-FIRST EMBEDS
     ============================ */
  function youtubeThumbURL(youtube_id) {
    return `https://img.youtube.com/vi/${encodeURIComponent(youtube_id)}/hqdefault.jpg`;
  }

  function thumbHTML(v, key) {
    if (isPlaylist(v)) {
      return `
        <div class="thumb-wrap" data-key="${escapeAttr(key)}" data-type="playlist" role="button" tabindex="0" aria-label="Play playlist">
          <div style="width:100%;height:100%;display:flex;align-items:center;justify-content:center;background:#000;">
            <div style="text-align:center;opacity:.85;font-size:.9rem;">
              ▶ Playlist<br><span style="opacity:.7;font-size:.8rem;">Tap to load</span>
            </div>
          </div>
        </div>
      `;
    }

    if (v.youtube_id) {
      const src = youtubeThumbURL(v.youtube_id);
      return `
        <div class="thumb-wrap" data-key="${escapeAttr(key)}" data-type="video" role="button" tabindex="0" aria-label="Play video">
          <img src="${src}" alt="" loading="lazy" decoding="async">
          <div class="thumb-badge">▶ Tap to play</div>
        </div>
      `;
    }

    if (v.url) {
      return `
        <div class="thumb-wrap" data-key="${escapeAttr(key)}" data-type="direct" role="button" tabindex="0" aria-label="Play video">
          <div style="width:100%;height:100%;display:flex;align-items:center;justify-content:center;background:#000;">
            ▶ Tap to play
          </div>
        </div>
      `;
    }

    return `<div class="thumb-wrap"><div class="meta-line">No source</div></div>`;
  }

  function iframeHTMLFor(v, autoplay = true) {
    if (v.youtube_id) {
      if (isPlaylist(v)) {
        const ap = autoplay ? "&autoplay=1" : "";
        return `<iframe src="https://www.youtube.com/embed/videoseries?list=${encodeURIComponent(v.youtube_id)}${ap}"
          loading="lazy" allow="autoplay; encrypted-media; picture-in-picture" allowfullscreen
          referrerpolicy="strict-origin-when-cross-origin"></iframe>`;
      }
      const ap = autoplay ? "?autoplay=1" : "";
      return `<iframe src="https://www.youtube.com/embed/${encodeURIComponent(v.youtube_id)}${ap}"
        loading="lazy" allow="autoplay; encrypted-media; picture-in-picture" allowfullscreen
        referrerpolicy="strict-origin-when-cross-origin"></iframe>`;
    }

    if (v.url) {
      const auto = autoplay ? " autoplay" : "";
      return `<video src="${escapeAttr(v.url)}" controls${auto} playsinline preload="metadata"></video>`;
    }

    return `<div class="meta-line">No source</div>`;
  }

  function replaceThumbWithPlayer(containerEl, v, key) {
    if (!containerEl || containerEl.dataset.loaded === "1") return;
    containerEl.dataset.loaded = "1";
    containerEl.innerHTML = iframeHTMLFor(v, true);
    markWatchedAfter5Min(key);
  }

  /* ============================
     UI HTML (cards)
     ============================ */
  function catChipHTML(c) {
    const raw = String(c ?? "").trim();
    if (!raw) return "";
    return `<span class="cat-chip" data-cat="${escapeAttr(raw)}">${escapeHTML(raw)}</span>`;
  }

  // FIX 1: Title ABOVE thumbnail
  function cardHTML(v, key) {
    const cats = categoryArray(v).map(catChipHTML).join("");
    const date = v.added ? new Date(v.added) : null;
    const dateStr = date ? date.toLocaleDateString() : "Unknown date";

    return `
      <div class="card" data-key="${escapeAttr(key)}">
        <div class="card-title">${escapeHTML(v.title || "Untitled")}</div>
        ${thumbHTML(v, key)}
        <div class="meta-line">Added: ${escapeHTML(dateStr)}</div>
        <div class="chips">${cats || ""}</div>
        <div class="card-actions">
          <button class="btn" data-open="${escapeAttr(key)}">▶ Open</button>
          <button class="btn secondary" data-share="${escapeAttr(key)}">🔗 Share</button>
        </div>
      </div>
    `;
  }

  function playlistCardHTML(v, key) {
    const date = v.added ? new Date(v.added) : null;
    const dateStr = date ? date.toLocaleDateString() : "Unknown date";

    return `
      <div class="playlist-item" data-key="${escapeAttr(key)}">
        <div class="meta-line" style="margin-bottom:4px;"><strong>${escapeHTML(v.title || "Playlist")}</strong></div>
        <div class="thumb-wrap" data-key="${escapeAttr(key)}" data-type="playlist" style="aspect-ratio:16/9;overflow:hidden;border-radius:8px;background:#000;" role="button" tabindex="0">
          <div style="width:100%;height:100%;display:flex;align-items:center;justify-content:center;">
            ▶ Tap to load playlist
          </div>
        </div>
        <div class="meta-line" style="margin-top:4px;">Added: ${escapeHTML(dateStr)}</div>
        <div style="text-align:center;margin-top:6px;">
          <button class="btn" data-share-pl="${escapeAttr(key)}">🔗 Share</button>
        </div>
      </div>
    `;
  }

  /* ============================
     POPUP
     ============================ */
  function highlightVerses(text) {
    const verseRe = /(^|\s)([1-3]?\s?[A-Za-z]+)\s+(\d{1,3}):(\d{1,3})([^\S\r\n]|$)/;
    return String(text)
      .split("\n")
      .map(line => verseRe.test(line)
        ? `<span class="verse">${escapeHTML(line)}</span>`
        : escapeHTML(line)
      )
      .join("\n");
  }

  function showPopupOnce() {
    if (!state.popup.enabled) return;
    if (localStorage.getItem("popupShown")) return;

    els.popupMsg.innerHTML = highlightVerses(state.popup.message || "");
    els.popupOverlay.style.display = "flex";
    els.popupClose.onclick = () => {
      els.popupOverlay.style.display = "none";
      localStorage.setItem("popupShown", "true");
    };
  }

  /* ============================
     FILTERS
     ============================ */
  function applyFilters() {
    const qRaw = (els.search.value || "").trim();
    const q = qRaw.toLowerCase();

    let sortMode = els.sort.value || "random";
    if (state.view === "new") sortMode = "newest";
    if (state.view === "trending") sortMode = "views";

    const filterCatLabel = els.category.value;
    const filterCat = filterCatLabel ? filterCatLabel.toLowerCase() : "";

    const isTagMode = qRaw.startsWith("#");
    const tagQuery = isTagMode ? qRaw.replace(/^#/, "").toLowerCase() : null;

    let sourceList;
    if (state.view === "playlists") sourceList = state.playlists.slice();
    else sourceList = state.videos.slice();

    let filteredList = sourceList.filter(v => {
      const categories = categoryArray(v).map(c => String(c).trim());
      const matchesCategory = !filterCat || categories.some(c => c.toLowerCase() === filterCat);

      if (!matchesCategory) return false;
      if (!q) return true;

      const title = (v.title || "").toLowerCase();
      const tags = (v.tags || []).map(t => (t || "").toLowerCase());
      const catStrings = categories.map(c => c.toLowerCase());

      if (isTagMode) {
        return tags.some(t => t.includes(tagQuery)) || catStrings.some(c => c.includes(tagQuery));
      }

      return title.includes(q) || tags.some(t => t.includes(q)) || catStrings.some(c => c.includes(q));
    });

    const byDate = x => new Date(x.added || 0).getTime() || 0;
    if (sortMode === "newest") filteredList.sort((a, b) => byDate(b) - byDate(a));
    else if (sortMode === "oldest") filteredList.sort((a, b) => byDate(a) - byDate(b));
    else if (sortMode === "az") filteredList.sort((a, b) => (a.title || "").localeCompare(b.title || ""));
    else if (sortMode === "za") filteredList.sort((a, b) => (b.title || "").localeCompare(a.title || ""));
    else if (sortMode === "views") filteredList.sort((a, b) => (b.views || 0) - (a.views || 0));
    else if (sortMode === "random") filteredList.sort(() => Math.random() - 0.5);

    if (state.view === "new") els.sort.value = "newest";
    else if (state.view === "trending") els.sort.value = "views";

    return { filtered: filteredList, total: sourceList.length };
  }

  /* ============================
     RENDER (throttled)
     ============================ */
  let renderQueued = false;
  function requestRender() {
    if (renderQueued) return;
    renderQueued = true;
    requestAnimationFrame(() => {
      renderQueued = false;
      render();
    });
  }

  function render() {
    if (els.featured) {
      els.featured.style.display = "none";
      if (els.featuredContainer) els.featuredContainer.innerHTML = "";
    }

    const { filtered, total } = applyFilters();
    els.resultCount.textContent = `Showing ${filtered.length} of ${total}`;

    const keyToVideo = new Map();
    state.videos.forEach(v => keyToVideo.set(videoKey(v), v));
    state.playlists.forEach(v => keyToVideo.set(videoKey(v), v));

    // PLAYLISTS VIEW (still works)
    if (state.view === "playlists") {
      els.videoGrid.innerHTML = "";

      if (filtered.length) {
        els.playlistBox.style.display = "";
        els.playlistContainer.style.display = "block";
        els.playlistToggle.textContent = "Hide";
        els.playlistToggle.setAttribute("aria-expanded", "true");

        const playlistKeys = filtered.map(v => videoKey(v));
        els.playlistGrid.innerHTML = playlistKeys
          .map(k => playlistCardHTML(keyToVideo.get(k), k))
          .join("");
      } else {
        els.playlistBox.style.display = "none";
        els.playlistGrid.innerHTML = "";
      }

      els.playlistGrid.querySelectorAll("[data-share-pl]").forEach(el => {
        el.onclick = () => {
          const key = el.getAttribute("data-share-pl");
          const v = keyToVideo.get(key);
          if (v) shareVideo(v);
        };
      });

      els.playlistGrid.querySelectorAll(".thumb-wrap[data-key]").forEach(el => {
        const key = el.getAttribute("data-key");
        const v = keyToVideo.get(key);
        if (!v) return;

        const on = () => replaceThumbWithPlayer(el, v, key);
        el.addEventListener("click", on);
        el.addEventListener("keydown", (e) => {
          if (e.key === "Enter" || e.key === " ") { e.preventDefault(); on(); }
        });
      });

      return;
    }

    // FIX 2: Hide playlists from main page completely
    if (els.playlistBox) els.playlistBox.style.display = "none";
    if (els.playlistGrid) els.playlistGrid.innerHTML = "";

    // NON-PLAYLIST VIEWS
    let listForGrid;
    if (state.view === "pinned") listForGrid = filtered.filter(v => v.pinned && !isPlaylist(v));
    else listForGrid = filtered.filter(v => !isPlaylist(v));

    const gridKeys = listForGrid.map(v => videoKey(v));
    gridKeys.forEach((k, i) => keyToVideo.set(k, listForGrid[i]));

    els.videoGrid.innerHTML = gridKeys
      .map(k => cardHTML(keyToVideo.get(k), k))
      .join("");

    // Category chips -> filter
    els.videoGrid.querySelectorAll(".cat-chip").forEach(el => {
      el.onclick = () => {
        const c = el.getAttribute("data-cat") || "";
        els.category.value = c;
        requestRender();
      };
    });

    // Open + Share buttons
    els.videoGrid.querySelectorAll("[data-open]").forEach(el => {
      el.onclick = () => {
        const key = el.getAttribute("data-open");
        const v = keyToVideo.get(key);
        if (v) window.open(shareLink(v), "_blank", "noopener");
      };
    });

    els.videoGrid.querySelectorAll("[data-share]").forEach(el => {
      el.onclick = () => {
        const key = el.getAttribute("data-share");
        const v = keyToVideo.get(key);
        if (v) shareVideo(v);
      };
    });

    // Thumb tap -> load iframe
    els.videoGrid.querySelectorAll(".thumb-wrap[data-key]").forEach(thumbEl => {
      const key = thumbEl.getAttribute("data-key");
      const v = keyToVideo.get(key);
      if (!v) return;

      const on = () => replaceThumbWithPlayer(thumbEl, v, key);
      thumbEl.addEventListener("click", on);
      thumbEl.addEventListener("keydown", (e) => {
        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); on(); }
      });
    });
  }

  /* ============================
     CATEGORY DROPDOWN (DE-DUPED)
     ============================ */
  function populateCategories() {
    while (els.category.options.length > 1) {
      els.category.remove(1);
    }

    const catsMap = new Map();
    state.videos.forEach(v => {
      categoryArray(v).forEach(raw => {
        if (raw == null) return;
        const val = String(raw).trim();
        if (!val) return;
        const key = val.toLowerCase();
        if (!catsMap.has(key)) catsMap.set(key, val);
      });
    });

    Array.from(catsMap.values())
      .sort((a, b) => a.localeCompare(b))
      .forEach(label => {
        const opt = document.createElement("option");
        opt.value = label;
        opt.textContent = label;
        els.category.appendChild(opt);
      });
  }

  /* ============================
     HYDRATE FROM DATA
     ============================ */
  function hydrateFromData(payload) {
    const { popup, videos } = normalizeVideosPayload(payload);
    state.popup = popup || { enabled: false, message: "" };
    state.videos = Array.isArray(videos) ? videos : [];
    state.playlists = state.videos.filter(isPlaylist);

    populateCategories();
    showPopupOnce();
    render();
  }

  /* ============================
     LOAD JSON (CACHE + NETWORK)
     ============================ */
  function initData() {
    const cached = readCache();
    if (cached && cached.data) {
      hydrateFromData(cached.data);
    }

    fetch("videos.json", { cache: "no-store" })
      .then(r => r.json())
      .then(data => {
        const prev = JSON.stringify(cached ? cached.data : null);
        const next = JSON.stringify(data);
        if (prev !== next) {
          hydrateFromData(data);
          writeCache(data);
        } else if (!cached) {
          hydrateFromData(data);
        }
      })
      .catch(err => {
        console.error("Failed to load videos.json", err);
        if (!state.videos.length) {
          alert("Failed to load videos. Please try again later.");
        }
      })
      .finally(() => {
        if (els.loading) els.loading.classList.add("hide");
      });
  }

  /* ============================
     EVENTS
     ============================ */
  function setActiveTab(view) {
    state.view = view;
    els.tabAll?.classList.toggle("active", view === "all");
    els.tabPinned?.classList.toggle("active", view === "pinned");
    els.tabPlaylists?.classList.toggle("active", view === "playlists");
    els.tabTrending?.classList.toggle("active", view === "trending");
    els.tabNew?.classList.toggle("active", view === "new");
    requestRender();
  }

  function debounce(fn, wait = 90) {
    let t;
    return (...args) => {
      clearTimeout(t);
      t = setTimeout(() => fn(...args), wait);
    };
  }

  function pickRandomFromCurrentFiltered() {
    const { filtered } = applyFilters();
    const pool =
      state.view === "playlists"
        ? filtered
        : filtered.filter(v => !isPlaylist(v) && (state.view !== "pinned" || v.pinned));

    if (!pool.length) {
      alert("No videos match your current filters.");
      return;
    }

    const v = pool[(Math.random() * pool.length) | 0];
    window.open(shareLink(v), "_blank", "noopener");
  }

  function wireEvents() {
    els.search?.addEventListener("input", debounce(requestRender, 80));
    els.category?.addEventListener("change", requestRender);
    els.sort?.addEventListener("change", requestRender);

    els.clearFilters?.addEventListener("click", () => {
      els.search.value = "";
      els.category.value = "";
      els.sort.value = "random";
      setActiveTab("all");
    });

    els.tabAll?.addEventListener("click", () => setActiveTab("all"));
    els.tabPinned?.addEventListener("click", () => setActiveTab("pinned"));
    els.tabPlaylists?.addEventListener("click", () => setActiveTab("playlists"));
    els.tabTrending?.addEventListener("click", () => setActiveTab("trending"));
    els.tabNew?.addEventListener("click", () => setActiveTab("new"));

    // still wired (only relevant in playlists tab)
    els.playlistToggle?.addEventListener("click", () => {
      state.playlistsOpen = !state.playlistsOpen;
      requestRender();
    });

    els.luckyBtn?.addEventListener("click", pickRandomFromCurrentFiltered);

    document.addEventListener("themechange", () => {});
  }

  /* ============================
     BOOT
     ============================ */
  wireEvents();
  initData();

  if (location.hash) {
    window.addEventListener("load", () => {
      const target = document.getElementById(location.hash.slice(1));
      if (target) target.scrollIntoView({ behavior: "smooth", block: "start" });
    });
  }
})();