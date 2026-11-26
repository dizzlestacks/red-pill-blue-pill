/* Optional published-backup lookup. Never calls admin APIs or sends cookies. */
(() => {
  const catalog=window.MATRIX_LITE_CATALOG;
  const base=catalog?.public_pi_url;
  if(!window.MATRIX_LITE || !base)return;
  let last='',running=false,backups={},posters={},enrichedFor=null;
  // Enrich the current catalogue, never replace it with an older embedded copy.
  function apply(){
    const current=window.MATRIX_CURRENT_CATALOG;
    if(!current)return;
    enrichedFor=current;
    const data=JSON.parse(JSON.stringify(current));
    for(const v of data.videos){
      const url=backups[v.id];
      if(typeof url==='string' && url.startsWith(base+'/media/')){
        v.backup_available=true;v.backup_url=url;v.limited_unavailable=false;
        if(!v.youtube_id)v.url=url;
        const poster=posters[v.id];
        if(typeof poster==='string'&&poster.startsWith(base+'/media/'))v.poster_url=poster;
      }
    }
    window.MATRIX_PI_REACHABLE=Object.keys(backups).length>0;
    document.dispatchEvent(new CustomEvent('matrix-offline-catalog',{detail:data}));
  }
  async function check(){
    if(running)return;
    running=true;
    let next={},nextPosters={};
    const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),3000);
    try{
      const response=await fetch(base+'/api/public/backups',{credentials:'omit',cache:'no-store',signal:controller.signal});
      if(response.ok){const body=await response.json();next=body.backups||{};nextPosters=body.posters||{};}
    }catch{}finally{clearTimeout(timer);running=false;}
    const signature=JSON.stringify([next,nextPosters]);
    backups=next;posters=nextPosters;
    if(signature===last&&enrichedFor===window.MATRIX_CURRENT_CATALOG)return;
    last=signature;
    apply();
  }
  // The catalogue can arrive after the first check: enrich it as soon as it is shown.
  document.addEventListener('videosloaded',()=>{
    if(window.MATRIX_CURRENT_CATALOG&&enrichedFor!==window.MATRIX_CURRENT_CATALOG&&last)apply();
  });
  window.addEventListener('online',check);
  window.addEventListener('matrix-backup-failed',()=>{last='';check();});
  check();setInterval(check,30000);
})();
