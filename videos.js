// videos.js — HUD + tabs version
// - Tags hidden on cards (but still searchable)
// - No separate Featured section
// - De-duplicated categories (case + whitespace)
// - Supports All / Pinned / Playlists tabs

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

    // Sections
    // (featured section exists in HTML, but we won't use it anymore)
    featured: document.getElementById("featured"),
    featuredContainer: document.getElementById("featuredContainer"),

    playlistBox: document.getElementById("playlistBox"),
    playlistToggle: document.getElementById("playlistToggle"),
    playlistContainer: document.getElementById("playlistContainer"),
    playlistGrid: document.getElementById("playlistGrid"),
    videoGrid: document.getElementById("videoGrid"),

    // Footer
    footInfo: document.getElementById("footInfo"),

    // Popup
    popupOverlay: document.getElementById("popupOverlay"),
    popupMsg: document.getElementById("popupMsg"),
    popupClose: document.getElementById("popupClose")
  };

  /* ============================
     STATE
     ============================ */
  const state = {
    popup: { enabled: false, message: "" },
    videos: [],
    playlists: [],
    view: "all",        // "all" | "pinned" | "playlists"
    playlistsOpen: false
  };

  const CACHE_KEY = "videosCacheV3";

  /* ============================
     HELPERS
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

  // Accept: Array<Video> | {popup,videos} | [ {popup,videos} ]
  function normalizeVideosPayload(d) {
    let popup = { enabled: false, message: "" };
    let videos = [];

    if (Array.isArray(d)) {
      if (
        d.length === 1 &&
        d[0] &&
        typeof d[0] === "object" &&
        Array.isArray(d[0].videos)
      ) {
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

  function isPlaylist(v) {
    return v.youtube_id && String(v.youtube_id).startsWith("PL");
  }

  function categoryArray(v) {
    if (!v.category) return [];
    return Array.isArray(v.category) ? v.category.filter(Boolean) : [v.category];
  }

  function catChipHTML(c) {
    const label = String(c || "");
    return `<span class="cat-chip" data-cat="${label}">${label}</span>`;
  }

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

  function embedHTML(v) {
    if (v.youtube_id) {
      if (isPlaylist(v)) {
        return `<iframe src="https://www.youtube.com/embed/videoseries?list=${encodeURIComponent(
          v.youtube_id
        )}" style="width:100%;aspect-ratio:16/9;" loading="lazy" allowfullscreen referrerpolicy="strict-origin-when-cross-origin"></iframe>`;
      }
      return `<iframe src="https://www.youtube.com/embed/${encodeURIComponent(
        v.youtube_id
      )}" style="width:100%;aspect-ratio:16/9;" loading="lazy" allowfullscreen referrerpolicy="strict-origin-when-cross-origin"></iframe>`;
    }
    if (v.url) {
      return `<video src="${v.url}" controls style="width:100%;height:auto;" preload="metadata" playsinline></video>`;
    }
    return `<div class="meta">No source</div>`;
  }

  // NOTE: tags are NOT displayed anymore (hidden), but still used internally for search
  function cardHTML(v, idx) {
    const cats = categoryArray(v).map(catChipHTML).join("");
    return `
      <div class="card" data-vid="${idx}">
        <h3>${v.title || "Untitled"}</h3>
        ${embedHTML(v)}
        <div class="meta">Category: ${cats || "—"}</div>
      </div>
    `;
  }

  function playlistCardHTML(v, idx) {
    return `
      <div class="playlist-item" data-pl="${idx}">
        <div class="meta" style="margin-bottom:6px;">${v.title || "Playlist"}</div>
        ${embedHTML(v)}
        <div style="text-align:center;margin-top:6px;">
          <button class="btn" data-share-pl="${idx}">🔗 Share</button>
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
        ? `<span class="verse">${line}</span>`
        : line
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
     FILTER + RENDER
     ============================ */
  function applyFilters() {
    const qRaw = (els.search.value || "").trim();
    const q = qRaw.toLowerCase();
    const sort = els.sort.value || "random";

    const filterCatLabel = els.category.value;
    const filterCat = filterCatLabel ? filterCatLabel.toLowerCase() : "";

    const isTagMode = qRaw.startsWith("#");
    const tagQuery = isTagMode ? qRaw.replace(/^#/, "").toLowerCase() : null;

    let sourceList;
    if (state.view === "playlists") {
      sourceList = state.playlists.slice();
    } else {
      sourceList = state.videos.slice();
    }

    let filtered = sourceList.filter(v => {
      const categories = categoryArray(v).map(c => String(c).trim());
      const matchesCategory =
        !filterCat ||
        categories.some(c => c.toLowerCase() === filterCat);

      if (!matchesCategory) return false;

      if (!q) return true;

      const title = (v.title || "").toLowerCase();
      const tags = (v.tags || []).map(t => (t || "").toLowerCase());
      const catStrings = categories.map(c => c.toLowerCase());

      if (isTagMode) {
        return (
          tags.some(t => t.includes(tagQuery)) ||
          catStrings.some(c => c.includes(tagQuery))
        );
      }

      return (
        title.includes(q) ||
        tags.some(t => t.includes(q)) ||
        catStrings.some(c => c.includes(q))
      );
    });

    const byDate = x => new Date(x.added || 0).getTime() || 0;
    if (sort === "newest") {
      filtered.sort((a, b) => byDate(b) - byDate(a));
    } else if (sort === "oldest") {
      filtered.sort((a, b) => byDate(a) - byDate(b));
    } else if (sort === "az") {
      filtered.sort((a, b) => (a.title || "").localeCompare(b.title || ""));
    } else if (sort === "za") {
      filtered.sort((a, b) => (b.title || "").localeCompare(a.title || ""));
    } else if (sort === "views") {
      filtered.sort((a, b) => (b.views || 0) - (a.views || 0));
    } else if (sort === "random") {
      filtered.sort(() => Math.random() - 0.5);
    }

    return { filtered, total: sourceList.length };
  }

  function render() {
    // Explicitly hide "featured" section – no special featured layout anymore
    if (els.featured) {
      els.featured.style.display = "none";
      if (els.featuredContainer) els.featuredContainer.innerHTML = "";
    }

    const { filtered, total } = applyFilters();
    els.resultCount.textContent = `Showing ${filtered.length} of ${total}`;

    if (state.view === "playlists") {
      // Playlist view: only playlists, in the playlistGrid
      els.videoGrid.innerHTML = "";
      if (filtered.length) {
        els.playlistBox.style.display = "";
        els.playlistContainer.style.display = "block";
        els.playlistToggle.textContent = "Hide";
        els.playlistToggle.setAttribute("aria-expanded", "true");
        els.playlistGrid.innerHTML = filtered
          .map((v, i) => playlistCardHTML(v, i))
          .join("");
      } else {
        els.playlistBox.style.display = "none";
        els.playlistGrid.innerHTML = "";
      }
    } else {
      // Video grid (non-playlist videos)
      const listForGrid =
        state.view === "pinned"
          ? filtered.filter(v => v.pinned && !isPlaylist(v))
          : filtered.filter(v => !isPlaylist(v));

      els.videoGrid.innerHTML = listForGrid
        .map((v, i) => cardHTML(v, i))
        .join("");

      // Playlist panel visible in "all" view only
      if (state.view === "all" && state.playlists.length) {
        els.playlistBox.style.display = "";
        if (state.playlistsOpen) {
          els.playlistContainer.style.display = "block";
          els.playlistToggle.textContent = "Hide";
          els.playlistToggle.setAttribute("aria-expanded", "true");
        } else {
          els.playlistContainer.style.display = "none";
          els.playlistToggle.textContent = "Show";
          els.playlistToggle.setAttribute("aria-expanded", "false");
        }
        els.playlistGrid.innerHTML = state.playlists
          .map((v, i) => playlistCardHTML(v, i))
          .join("");
      } else {
        els.playlistBox.style.display = "none";
        els.playlistGrid.innerHTML = "";
      }
    }

    // Category chip -> filter
    document.querySelectorAll(".cat-chip").forEach(el => {
      el.addEventListener("click", () => {
        const c = el.getAttribute("data-cat") || "";
        els.category.value = c;
        render();
      });
    });

    // Share buttons for grid
    document.querySelectorAll("[data-share]").forEach(el => {
      el.addEventListener("click", () => {
        const idx = Number(el.getAttribute("data-share"));
        const list =
          state.view === "pinned"
            ? state.videos.filter(v => v.pinned && !isPlaylist(v))
            : state.videos.filter(v => !isPlaylist(v));
        const v = list[idx];
        if (v) shareVideo(v);
      });
    });

    // Share buttons for playlists
    document.querySelectorAll("[data-share-pl]").forEach(el => {
      el.addEventListener("click", () => {
        const idx = Number(el.getAttribute("data-share-pl"));
        const v =
          state.view === "playlists"
            ? applyFilters().filtered[idx]
            : state.playlists[idx];
        if (v) shareVideo(v);
      });
    });
  }

  /* ============================
     CATEGORY DROPDOWN (DE-DUPED)
     ============================ */
  function populateCategories() {
    // clear old dynamic options
    while (els.category.options.length > 1) {
      els.category.remove(1);
    }

    const catsMap = new Map(); // key: lowercase, value: first-seen label
    state.videos.forEach(v => {
      categoryArray(v).forEach(raw => {
        if (raw == null) return;
        const val = String(raw).trim();
        if (!val) return;
        const key = val.toLowerCase();
        if (!catsMap.has(key)) {
          catsMap.set(key, val);
        }
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
     EVENT WIRING
     ============================ */
  function setActiveTab(view) {
    state.view = view;
    els.tabAll.classList.toggle("active", view === "all");
    els.tabPinned.classList.toggle("active", view === "pinned");
    els.tabPlaylists.classList.toggle("active", view === "playlists");
    render();
  }

  function wireEvents() {
    els.search?.addEventListener("input", () => render());
    els.category?.addEventListener("change", () => render());
    els.sort?.addEventListener("change", () => render());

    els.clearFilters?.addEventListener("click", () => {
      els.search.value = "";
      els.category.value = "";
      els.sort.value = "random";
      render();
    });

    els.tabAll?.addEventListener("click", () => setActiveTab("all"));
    els.tabPinned?.addEventListener("click", () => setActiveTab("pinned"));
    els.tabPlaylists?.addEventListener("click", () => setActiveTab("playlists"));

    els.playlistToggle?.addEventListener("click", () => {
      state.playlistsOpen = !state.playlistsOpen;
      render();
    });
  }

  /* ============================
     BOOT
     ============================ */
  wireEvents();
  initData();

  // Hash scroll (if you link to #some-id on the page)
  if (location.hash) {
    window.addEventListener("load", () => {
      const target = document.getElementById(location.hash.slice(1));
      if (target) target.scrollIntoView({ behavior: "smooth", block: "start" });
    });
  }
})();