/* Public-only resilience: never cache admin pages, cookies, or private API data. */
(() => {
  const originalFetch = window.fetch.bind(window);
  const strip = data => {
    const result=JSON.parse(JSON.stringify(data));
    (Array.isArray(result)?result:result.videos||[]).forEach(v=>{
      ['backup','backup_available','backup_url','backup_status','url'].forEach(k=>delete v[k]);
      if(!v.youtube_id&&!v.tiktok_url)v.limited_unavailable=true;
    });
    return result;
  };
  let limited=!!window.MATRIX_LITE, lastData=null;
  // Unregister directly; update() can hang while an obsolete worker still
  // controls this document. A worker cannot relinquish a client without navigation.
  const cleanup = window.MATRIX_LITE && window.isSecureContext ? (async()=>{
    try {
      if('serviceWorker' in navigator){
        const registrations=await navigator.serviceWorker.getRegistrations();
        await Promise.all(registrations.filter(r=>/\/(?:service-worker|sw)\.js$/.test(new URL(r.active?.scriptURL||r.waiting?.scriptURL||r.installing?.scriptURL||'/',location.href).pathname)).map(r=>r.unregister()));
      }
      if('caches' in window)await Promise.all((await caches.keys()).filter(n=>n.startsWith('matrix-')).map(n=>caches.delete(n)));
      try {localStorage.removeItem('matrix:public-offline');localStorage.removeItem('videosCacheV4');}catch{}
      const url=new URL(location.href),version=window.MATRIX_BUILD?.commit||'2026.11.15.7';
      if(navigator.serviceWorker?.controller){
        if(url.searchParams.get('matrix-refresh')!==version){
          url.searchParams.set('matrix-refresh',version);location.replace(url.href);
        }
      }else if(url.searchParams.has('matrix-refresh')){
        url.searchParams.delete('matrix-refresh');history.replaceState(null,'',url.href);
      }
    }catch{/* Optional browser storage must never block the embedded catalogue. */}
  })():Promise.resolve();
  const hideUnavailable = () => {
    document.querySelectorAll('[data-backup],a[href*="/media/"],a[href="/admin"],a[href="editor.html"]').forEach(el=>{
      el.dataset.limitedControl='';
      const backup=el.matches('[data-backup]');
      el.hidden=limited && !(backup && window.MATRIX_LITE && window.MATRIX_PI_REACHABLE);
    });
  };
  const show = value => {
    limited=value;
    window.MATRIX_LIMITED=value;
    if(value && !window.MATRIX_LITE)window.dispatchEvent(new Event('matrix-server-unavailable'));
    const banner=document.getElementById('limited-mode');
    if(banner)banner.hidden=!value;
    hideUnavailable();
  };
  window.fetch = async (input, init) => {
    const url=new URL(typeof input==='string'?input:input.url,location.href);
    if(!url.pathname.endsWith('/videos.json'))return originalFetch(input,init);
    await cleanup;
    if(window.MATRIX_LITE && navigator.serviceWorker?.controller && window.MATRIX_LITE_CATALOG){
      lastData=strip(window.MATRIX_LITE_CATALOG);window.MATRIX_CURRENT_CATALOG=lastData;
      return new Response(JSON.stringify(lastData),{headers:{'Content-Type':'application/json'}});
    }
    try {
      const response=await originalFetch(input,{...init,signal:init?.signal||AbortSignal.timeout(6000)});
      if(!response.ok)throw new Error('Catalogue unavailable');
      const data=await response.json();
      if(!Array.isArray(data)&&!Array.isArray(data?.videos))throw new Error('Invalid catalogue');
      lastData=data;
      window.MATRIX_CURRENT_CATALOG=data;
      try{localStorage.setItem('matrix:public-offline',JSON.stringify(strip(data)));}catch{}
      const offline=response.headers.get('X-Matrix-Offline')==='1';
      if(offline)show(true);
      return new Response(JSON.stringify(offline?strip(data):data),{headers:{'Content-Type':'application/json'}});
    } catch(error) {
      if(window.MATRIX_LITE_CATALOG){
        lastData=strip(window.MATRIX_LITE_CATALOG);
        window.MATRIX_CURRENT_CATALOG=lastData;
        return new Response(JSON.stringify(lastData),{headers:{'Content-Type':'application/json'}});
      }
      let saved;try{saved=localStorage.getItem('matrix:public-offline');}catch{}
      if(saved){show(true);lastData=JSON.parse(saved);return new Response(saved,{headers:{'Content-Type':'application/json'}});}
      throw error;
    }
  };
  async function check() {
    if(window.MATRIX_LITE)return show(true);
    try {
      const response=await originalFetch('healthz',{cache:'no-store',signal:AbortSignal.timeout(4000)});
      if(!response.ok)throw new Error('Pi unavailable');
      if(limited && navigator.onLine) {
        show(false);
        // Reload the catalogue via the existing application; never retain broken local backup links.
        if(lastData)location.reload();
      }
    }catch{
      show(true);
      if(lastData)document.dispatchEvent(new CustomEvent('matrix-offline-catalog',{detail:strip(lastData)}));
    }
  }
  document.addEventListener('DOMContentLoaded',()=>{
    const notice=document.createElement('div'); notice.id='limited-mode';notice.hidden=true;
    notice.setAttribute('role','status');
    notice.textContent='Limited view';
    notice.style.cssText='position:relative;z-index:99999;padding:3px 8px;color:var(--muted,#7bd58a);background:var(--bg,#000);font:11px/1.4 monospace;letter-spacing:.06em;text-align:center';
    const style=document.createElement('style');
    style.textContent='[data-limited-control][hidden]{display:none!important}';
    document.head.appendChild(style);
    document.body.prepend(notice);
    show(limited);
    new MutationObserver(()=>{if(limited)hideUnavailable();}).observe(document.body,{childList:true,subtree:true});
    if(!window.MATRIX_LITE && 'serviceWorker' in navigator && ['https:','http:'].includes(location.protocol)) {
      navigator.serviceWorker.register('service-worker.js',{updateViaCache:'none'})
        .then(registration=>registration.update()).catch(()=>{});
      navigator.serviceWorker.addEventListener('message',event=>{if(event.data?.type==='MATRIX_OFFLINE'){show(true);if(lastData)document.dispatchEvent(new CustomEvent('matrix-offline-catalog',{detail:strip(lastData)}));}});
      check(); setInterval(check,30000);
    }
  });
  window.addEventListener('offline',()=>{show(true);if(lastData&&!window.MATRIX_LITE)document.dispatchEvent(new CustomEvent('matrix-offline-catalog',{detail:strip(lastData)}));});
  window.addEventListener('online',check);
  window.MATRIX_LIMITED=limited;
})();
