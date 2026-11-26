const CACHE='matrix-public-2026.11.15.12';
const CORE=['./','index.html','choice.html','videos.html','learn.html','readonly.html','reset.html',
 'themes.css','videos.js','offline.js','backup-player.js','videos.json',
 'assets/favicon-32x32.png','assets/favicon.ico','assets/pill-choice.png',
 'assets/tiktok.png','assets/instagram.png'];
const ALLOWED=new Set(CORE.map(p=>new URL(p,self.registration.scope).pathname));
const clean = data => {
 const result=JSON.parse(JSON.stringify(data));
 (Array.isArray(result)?result:result.videos||[]).forEach(v=>{
  ['backup','backup_url','backup_available','backup_status','url','poster_url'].forEach(k=>delete v[k]);
  if(!v.youtube_id)v.limited_unavailable=true;
 });
 return result;
};
self.addEventListener('install',event=>event.waitUntil((async()=>{
 const cache=await caches.open(CACHE);
 // A missing optional asset must not prevent the hub and catalogue working.
 await Promise.all(CORE.map(async path=>{try{const r=await fetch(path,{cache:'reload'});if(r.ok)await cache.put(path,r);}catch{}}));
 await self.skipWaiting();
})()));
self.addEventListener('activate',event=>event.waitUntil((async()=>{
 const old=(await caches.keys()).filter(name=>name.startsWith('matrix-public-')&&name!==CACHE);
 for(const name of old)await caches.delete(name);
 await self.clients.claim();
 // An old cached intro can already be executing when its worker is replaced.
 // Refresh public tabs once on upgrade so they also receive the playback fix.
 if(old.length)for(const client of await self.clients.matchAll({type:'window'})){
   const url=new URL(client.url);
   if(url.origin===self.location.origin&&ALLOWED.has(url.pathname)&&
      (url.pathname.endsWith('.html')||url.pathname===new URL('./',self.registration.scope).pathname)){
     await client.navigate(client.url);
   }
 }
})()));
async function offline(request){
 const cache=await caches.open(CACHE),url=new URL(request.url);
 const response=await cache.match(url.pathname);
 for(const client of await self.clients.matchAll())client.postMessage({type:'MATRIX_OFFLINE'});
 if(!response)return new Response('Not cached yet. Reconnect to the Pi once to save this page.',{status:503,headers:{'Content-Type':'text/plain'}});
 if(url.pathname.endsWith('/videos.json'))return new Response(JSON.stringify(clean(await response.json())),{headers:{'Content-Type':'application/json','X-Matrix-Offline':'1'}});
 return response;
}
self.addEventListener('fetch',event=>{
 const request=event.request,url=new URL(request.url);
 // Never take ownership of media, PDFs or ANY range request, including small files.
 if(request.method!=='GET'||request.headers.has('Range')||request.destination==='video'||request.destination==='audio'||/\.(mp4|pdf)$/i.test(url.pathname)||url.origin!==self.location.origin||!ALLOWED.has(url.pathname))return;
 event.respondWith((async()=>{
  try{
   const response=await fetch(request,{signal:AbortSignal.timeout(3500)});
   if(!response.ok)throw new Error('Unavailable');
   if(response.status===200){const cache=await caches.open(CACHE);await cache.put(url.pathname,response.clone());}
   return response;
  }catch{return offline(request);}
 })());
});
