// =========================
// /videos.js  (V4.2 – tabs + scoped categories + SWR cache)
// =========================
"use strict";

/* ===== Grab elements ===== */
const els = {
  // tabs & filters
  tabAll: document.getElementById("tab-all"),
  tabPinned: document.getElementById("tab-pinned"),
  tabPlaylists: document.getElementById("tab-playlists"),
  search: document.getElementById("searchInput"),
  cat: document.getElementById("categoryFilter"),
  sort: document.getElementById("sortFilter"),
  clear: document.getElementById("clearFilters"),
  resultCount: document.getElementById("resultCount"),

  // content
  grid: document.getElementById("videoGrid"),
  featured: document.getElementById("featured"),
  fbox: document.getElementById("featuredContainer"),

  // popup
  popupOverlay: document.getElementById("popupOverlay"),
  popupMsg: document.getElementById("popupMsg"),
  popupClose: document.getElementById("popupClose"),

  // loader (optional)
  loading: document.getElementById("loading"),
};

/* ===== App state ===== */
let APP_POPUP = { enabled: false, message: "" };
let videos = [];
let currentTab = "all"; // "all" | "pinned" | "playlists"

/* ===== Helpers ===== */
function isPlaylist(v){ return v.youtube_id && String(v.youtube_id).startsWith("PL"); }
function tagHTML(t){ const clean=(t||"").replace(/^#/,""); return `<span class="tag" data-tag="${clean}">#${clean}</span>`; }
function catChipHTML(c){ return `<span class="cat-chip" data-cat="${c}">${c}</span>`; }

function embedHTML(v){
  if (v.youtube_id){
    if (String(v.youtube_id).startsWith("PL")){
      return `<iframe src="https://www.youtube.com/embed/videoseries?list=${encodeURIComponent(v.youtube_id)}" style="width:100%;aspect-ratio:16/9;" allowfullscreen loading="lazy" referrerpolicy="strict-origin-when-cross-origin"></iframe>`;
    }
    return `<iframe src="https://www.youtube.com/embed/${encodeURIComponent(v.youtube_id)}" style="width:100%;aspect-ratio:16/9;" allowfullscreen loading="lazy" referrerpolicy="strict-origin-when-cross-origin"></iframe>`;
  }
  if (v.url) return `<video src="${v.url}" controls style="width:100%;height:auto;" preload="metadata" playsinline></video>`;
  return `<div class="meta">No source</div>`;
}
function shareLink(v){
  if (v.youtube_id){
    if (String(v.youtube_id).startsWith("PL")) return `https://www.youtube.com/playlist?list=${v.youtube_id}`;
    return `https://youtu.be/${v.youtube_id}`;
  }
  return location.href;
}
async function shareVideo(v){
  const url = shareLink(v);
  try { await navigator.clipboard.writeText(url); alert("Link copied!"); }
  catch { prompt("Copy link:", url); }
}
function cardHTML(v, idx){
  const tags = (v.tags||[]).map(tagHTML).join("");
  const cats = (Array.isArray(v.category)?v.category:[v.category]).filter(Boolean).map(catChipHTML).join("");
  return `<div class="card">
    <h3>${v.title || "Untitled"}</h3>
    ${embedHTML(v)}
    <div class="meta">Category: ${cats || "—"}</div>
    <div class="tags">${tags}</div>
    <button class="btn" data-share="${idx}">🔗 Share</button>
  </div>`;
}

/* ===== Popup (centered text already styled in CSS) ===== */
function highlightVerses(text){
  const verseRe = /(^|\s)([1-3]?\s?[A-Za-z]+)\s+(\d{1,3}):(\d{1,3})([^\S\r\n]|$)/;
  return String(text).split('\n').map(line => verseRe.test(line) ? `<span class="verse">${line}</span>` : line).join('\n');
}
function showPopupOnce(){
  if (!APP_POPUP.enabled) return;
  if (localStorage.getItem("popupShown")) return;
  els.popupMsg.innerHTML = highlightVerses(APP_POPUP.message||"");
  els.popupOverlay.style.display = "flex";
  els.popupClose.onclick = () => {
    els.popupOverlay.style.display = "none";
    localStorage.setItem("popupShown", "true");
  };
}

/* ===== Category canonicalizer (dedupe, case-insensitive, trimmed) ===== */
function canonicalizeCategories(items){
  const keyToDisplay = new Map();
  for (const v of items) {
    const cats = Array.isArray(v.category) ? v.category : (v.category ? [v.category] : []);
    for (let c of cats){
      c = String(c || "").trim().replace(/\s+/g, " ");
      if (!c) continue;
      const key = c.toLowerCase();
      if (!keyToDisplay.has(key)) keyToDisplay.set(key, c);
    }
  }
  return [...keyToDisplay.values()].sort((a,b)=>a.localeCompare(b));
}
function rebuildCategoryOptions(){
  const normalVideos = videos.filter(v => !isPlaylist(v));
  const onlyPlaylists = videos.filter(isPlaylist);
  const source = currentTab === "playlists" ? onlyPlaylists
                : (currentTab === "pinned" ? normalVideos.filter(v=>v.pinned && !v.featured) : normalVideos);
  const cats = canonicalizeCategories(source);
  els.cat.innerHTML = "<option value=''>All Categories</option>";
  for (const c of cats){
    const o = document.createElement("option");
    o.value = c;
    o.textContent = c;
    els.cat.appendChild(o);
  }
}

/* ===== Render ===== */
function render(){
  // Base list scoped to tab
  let base = videos.slice();
  if (currentTab === "playlists") {
    base = base.filter(isPlaylist);
  } else {
    base = base.filter(v => !isPlaylist(v)); // All/Pinned exclude playlists
  }

  // Featured only on All tab
  if (currentTab === "all") {
    const f = base.find(v => v.featured);
    if (f){ els.featured.style.display=""; els.fbox.innerHTML = embedHTML(f); }
    else { els.featured.style.display="none"; els.fbox.innerHTML=""; }
  } else {
    els.featured.style.display="none"; els.fbox.innerHTML="";
  }

  // Filters
  const q = (els.search.value||"").toLowerCase().trim();
  const cat = els.cat.value;
  const sort = els.sort.value || "random";

  let list = base.filter(v => {
    const matchesQ = !q ||
      (v.title && v.title.toLowerCase().includes(q)) ||
      (v.tags||[]).some(t => (t||"").toLowerCase().includes(q));
    const matchesC = !cat ||
      (Array.isArray(v.category) ? v.category.map(c=>String(c).trim()).includes(cat) : String(v.category||"").trim() === cat);
    return matchesQ && matchesC;
  });

  if (currentTab === "pinned") {
    list = list.filter(v => v.pinned && !v.featured);
  }

  // Sort
  if (sort === "newest") list.sort((a,b)=>new Date(b.added||0)-new Date(a.added||0));
  else if (sort === "oldest") list.sort((a,b)=>new Date(a.added||0)-new Date(b.added||0));
  else if (sort === "az") list.sort((a,b)=>(a.title||"").localeCompare(b.title||""));
  else if (sort === "za") list.sort((a,b)=>(b.title||"").localeCompare(a.title||""));
  else if (sort === "views") list.sort((a,b)=>(b.views||0)-(a.views||0));
  else if (sort === "random") list.sort(()=>Math.random()-0.5);

  // Count
  els.resultCount.textContent = `Showing ${list.length} of ${base.length}`;

  // Grid
  els.grid.innerHTML = list.map(cardHTML).join("");

  // Wire chips & share for current render
  document.querySelectorAll(".tag").forEach(el=>{
    el.onclick = () => { els.search.value = el.dataset.tag||""; render(); };
  });
  document.querySelectorAll(".cat-chip").forEach(el=>{
    el.onclick = () => { els.cat.value = el.dataset.cat||""; render(); };
  });
  document.querySelectorAll("[data-share]").forEach((el,idx)=>{
    el.onclick = () => { shareVideo(list[idx] || {}); };
  });
}

/* ===== SWR Cache (cache-first, then revalidate) ===== */
const CACHE_KEY = "videosCacheV2";
function readCache(){
  try { return JSON.parse(localStorage.getItem(CACHE_KEY) || "null"); }
  catch { return null; }
}
function writeCache(obj){
  try { localStorage.setItem(CACHE_KEY, JSON.stringify({ time: Date.now(), data: obj })); } catch {}
}
function hydrate(data){
  // Accept object or legacy array
  if (Array.isArray(data)) {
    APP_POPUP = { enabled:false, message:"" };
    videos = data;
  } else {
    APP_POPUP = data?.popup || { enabled:false, message:"" };
    videos = Array.isArray(data?.videos) ? data.videos : [];
  }

  rebuildCategoryOptions();  // scoped categories per tab
  showPopupOnce();
  render();
}

/* Paint from cache immediately */
const cached = readCache();
if (cached?.data) hydrate(cached.data);

/* Always fetch fresh */
fetch("videos.json")
  .then(r => r.json())
  .then(data => {
    const prev = JSON.stringify(cached?.data || null);
    const next = JSON.stringify(data);
    if (prev !== next) {
      hydrate(data);
      writeCache(data);
    }
  })
  .catch(err => console.error("Fetch videos.json failed", err))
  .finally(() => {
    els.loading && els.loading.classList.add("hide");
    if (location.hash){
      const target = document.getElementById(location.hash.slice(1));
      if (target) target.scrollIntoView({behavior:'smooth', block:'start'});
    }
  });

/* ===== Events ===== */
els.search.addEventListener("input", render);
els.cat.addEventListener("change", render);
els.sort.addEventListener("change", render);
els.clear.addEventListener("click", ()=>{
  els.search.value = "";
  els.cat.value = "";
  els.sort.value = "random";
  render();
});

/* Tabs */
function setTab(tab){
  currentTab = tab;
  [els.tabAll, els.tabPinned, els.tabPlaylists].forEach(b => b.classList.remove('active'));
  if (tab === "all") els.tabAll.classList.add('active');
  if (tab === "pinned") els.tabPinned.classList.add('active');
  if (tab === "playlists") els.tabPlaylists.classList.add('active');
  // Rebuild categories for the new context and reset selection
  rebuildCategoryOptions();
  els.cat.value = "";
  render();
}
els.tabAll.addEventListener('click', ()=> setTab("all"));
els.tabPinned.addEventListener('click', ()=> setTab("pinned"));
els.tabPlaylists.addEventListener('click', ()=> setTab("playlists"));