const CACHE='matrix-public-2026.11.15.2-limited-view';
const CORE=['./','index.html','choice.html','videos.html','learn.html','readonly.html','reset.html',
 'themes.css','videos.js','offline.js','backup-player.js','videos.json',
 'assets/favicon-32x32.png','assets/favicon.ico','assets/pill-choice.png',
 'assets/tiktok.png','assets/instagram.png'];
const OPTIONAL=['The-Matrix-Blue-Pill-or-Red-Pill.mp4','pdfs/kjv-restored-names.pdf','pdfs/apocrypha.pdf'];
const ALLOWED=new Set([...CORE,...OPTIONAL].map(p=>new URL(p,self.registration.scope).pathname));
const clean = data => {
 const result=JSON.parse(JSON.stringify(data));
 (Array.isArray(result)?result:result.videos||[]).forEach(v=>{
  ['backup','backup_url','backup_available','backup_status','url'].forEach(k=>delete v[k]);
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
 for(const name of await caches.keys())if(name.startsWith('matrix-public-')&&name!==CACHE)await caches.delete(name);
 await self.clients.claim();
 const cache=await caches.open(CACHE);
 await Promise.all(OPTIONAL.map(async path=>{try{const r=await fetch(path);if(r.ok)await cache.put(path,r);}catch{}}));
})()));
async function offline(request){
 const cache=await caches.open(CACHE),url=new URL(request.url);
 const response=await cache.match(url.pathname);
 for(const client of await self.clients.matchAll())client.postMessage({type:'MATRIX_OFFLINE'});
 if(!response)return new Response('Not cached yet. Reconnect to the Pi once to save this page.',{status:503,headers:{'Content-Type':'text/plain'}});
 if(url.pathname.endsWith('/videos.json'))return new Response(JSON.stringify(clean(await response.json())),{headers:{'Content-Type':'application/json','X-Matrix-Offline':'1'}});
 const range=request.headers.get('Range');
 if(range && url.pathname.endsWith('.mp4')){
   const bytes=await response.arrayBuffer(),match=/^bytes=(\d+)-(\d*)$/.exec(range);
   if(!match)return new Response(null,{status:416});
   const start=+match[1],end=match[2]?Math.min(+match[2],bytes.byteLength-1):bytes.byteLength-1;
   if(start>end)return new Response(null,{status:416});
   return new Response(bytes.slice(start,end+1),{status:206,headers:{'Content-Type':'video/mp4','Content-Length':String(end-start+1),'Content-Range':`bytes ${start}-${end}/${bytes.byteLength}`,'Accept-Ranges':'bytes'}});
 }
 return response;
}
self.addEventListener('fetch',event=>{
 const request=event.request,url=new URL(request.url);
 if(request.method!=='GET'||url.origin!==self.location.origin||!ALLOWED.has(url.pathname))return;
 event.respondWith((async()=>{
  try{
   const response=await fetch(request,{signal:AbortSignal.timeout(3500)});
   if(!response.ok)throw new Error('Unavailable');
   if(response.status===200){const cache=await caches.open(CACHE);await cache.put(url.pathname,response.clone());}
   return response;
  }catch{return offline(request);}
 })());
});
