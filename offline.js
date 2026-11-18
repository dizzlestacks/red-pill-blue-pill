/* Public-only resilience: never cache admin pages, cookies, or private API data. */
(() => {
  const originalFetch = window.fetch.bind(window);
  const strip = data => {
    const result=JSON.parse(JSON.stringify(data));
    (Array.isArray(result)?result:result.videos||[]).forEach(v=>{
      ['backup','backup_available','backup_url','backup_status','url'].forEach(k=>delete v[k]);
      if(!v.youtube_id)v.limited_unavailable=true;
    });
    return result;
  };
  let limited=!!window.MATRIX_LITE, lastData=null;
  const hideUnavailable = () => {
    document.querySelectorAll('[data-backup],a[href*="/media/"],a[href="/admin"],a[href="editor.html"]').forEach(el=>{
      el.dataset.limitedControl='';
      el.hidden=limited;
    });
  };
  const show = value => {
    limited=value;
    window.MATRIX_LIMITED=value;
    const banner=document.getElementById('limited-mode');
    if(banner)banner.hidden=!value;
    hideUnavailable();
  };
  window.fetch = async (input, init) => {
    const url=new URL(typeof input==='string'?input:input.url,location.href);
    if(!url.pathname.endsWith('/videos.json'))return originalFetch(input,init);
    if(window.MATRIX_LITE_CATALOG) {
      lastData=strip(window.MATRIX_LITE_CATALOG);
      return new Response(JSON.stringify(lastData),{headers:{'Content-Type':'application/json'}});
    }
    try {
      const response=await originalFetch(input,init);
      if(!response.ok)throw new Error('Catalogue unavailable');
      const data=await response.json();
      lastData=data;
      try{localStorage.setItem('matrix:public-offline',JSON.stringify(strip(data)));}catch{}
      const offline=response.headers.get('X-Matrix-Offline')==='1';
      if(offline)show(true);
      return new Response(JSON.stringify(offline?strip(data):data),{headers:{'Content-Type':'application/json'}});
    } catch(error) {
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
      navigator.serviceWorker.register('service-worker.js').catch(()=>{});
      navigator.serviceWorker.addEventListener('message',event=>{if(event.data?.type==='MATRIX_OFFLINE'){show(true);if(lastData)document.dispatchEvent(new CustomEvent('matrix-offline-catalog',{detail:strip(lastData)}));}});
      check(); setInterval(check,30000);
    }
  });
  window.addEventListener('offline',()=>{show(true);if(lastData)document.dispatchEvent(new CustomEvent('matrix-offline-catalog',{detail:strip(lastData)}));});
  window.addEventListener('online',check);
  window.MATRIX_LIMITED=limited;
})();
