/* Optional published-backup lookup. Never calls admin APIs or sends cookies. */
(() => {
  const catalog=window.MATRIX_LITE_CATALOG;
  const base=catalog?.public_pi_url;
  if(!window.MATRIX_LITE || !base)return;
  let last='',running=false;
  async function check(){
    if(running)return;
    running=true;
    let backups={};
    const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),3000);
    try{
      const response=await fetch(base+'/api/public/backups',{credentials:'omit',cache:'no-store',signal:controller.signal});
      if(response.ok)backups=(await response.json()).backups||{};
    }catch{}finally{clearTimeout(timer);running=false;}
    const signature=JSON.stringify(backups);
    if(signature===last)return;
    last=signature;
    // Enrich the current catalogue, never replace it with an older embedded copy.
    const current=window.MATRIX_CURRENT_CATALOG;
    if(!current)return;
    const data=JSON.parse(JSON.stringify(current));
    for(const v of data.videos){
      const url=backups[v.id];
      if(typeof url==='string' && url.startsWith(base+'/media/')){
        v.backup_available=true;v.backup_url=url;v.limited_unavailable=false;
        if(!v.youtube_id)v.url=url;
      }
    }
    window.MATRIX_PI_REACHABLE=Object.keys(backups).length>0;
    document.dispatchEvent(new CustomEvent('matrix-offline-catalog',{detail:data}));
  }
  window.addEventListener('online',check);
  window.addEventListener('matrix-backup-failed',()=>{last='';check();});
  check();setInterval(check,30000);
})();
