/* Release gate: real browser rendering, not just a successful JSON response.
   node tests/lite-smoke.cjs --url https://actual-netlify-preview
   node tests/lite-smoke.cjs --dir generated-artifacts/netlify-lite */
const {chromium}=require('playwright'),{spawn}=require('child_process');
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path');
const arg=n=>process.argv[process.argv.indexOf(n)+1];
(async()=>{
 let server;const remote=process.argv.includes('--url');
 const base=remote?arg('--url').replace(/\/$/,''):'http://127.0.0.1:18765';
 if(!remote){
  server=spawn('python3',['-m','http.server','18765','--bind','127.0.0.1','--directory',arg('--dir')],{stdio:'ignore'});
  await new Promise(r=>setTimeout(r,600));
 }
 const executablePath=process.env.CHROMIUM_PATH||(fs.existsSync('/repl/tools/bin/chromium')?'/repl/tools/bin/chromium':undefined);
 const browser=await chromium.launch({executablePath,args:['--no-sandbox']});
 try {
  for(const mode of ['clean','old-data','storage-blocked']){
   const context=await browser.newContext({viewport:{width:390,height:844},isMobile:true,hasTouch:true});
   const page=await context.newPage(),errors=[],requests=[];
   page.on('pageerror',e=>errors.push(e.message));
   page.on('response',r=>{if(/videos.json|lite-data.js|service-worker.js/.test(r.url()))requests.push([r.status(),r.url()]);});
   page.on('dialog',d=>d.dismiss());
   const data=await (await context.request.get(base+'/videos.json')).json();
   const expected=Array.isArray(data)?data.length:data.videos.length;
   assert(expected>0,'Release catalogue is empty');
   if(mode==='old-data')await context.addInitScript(data=>{
    localStorage.setItem('videosCacheV4',JSON.stringify({time:Date.now(),data}));
    localStorage.setItem('matrix:public-offline',JSON.stringify({videos:[]}));
    localStorage.setItem('preferredTheme','gold');localStorage.setItem('splashSeen','true');localStorage.setItem('popupShown','true');
   },data);
   if(mode==='storage-blocked')await context.addInitScript(()=>{
    Storage.prototype.getItem=()=>{throw new DOMException('Blocked','SecurityError');};
    Storage.prototype.setItem=()=>{throw new DOMException('Blocked','SecurityError');};
   });
   // Optional Pi failure must not stop catalogue rendering.
   await context.route('**/api/public/backups',r=>r.abort());
   await page.goto(base+'/videos.html');
   await page.waitForFunction(()=>/Showing [1-9]\d* of [1-9]\d*/.test(document.body?.textContent||''),null,{timeout:20000});
   if(mode==='old-data'){
    await page.evaluate(async()=>{const c=await caches.open('matrix-public-old');await c.put('/videos.json',new Response('{"videos":[]}'));});
    if(!remote){
     const swPath=path.join(arg('--dir'),'service-worker.js'),retirement=fs.readFileSync(swPath);
     try{
      fs.writeFileSync(swPath,`self.addEventListener('install',e=>e.waitUntil(self.skipWaiting()));
       self.addEventListener('activate',e=>e.waitUntil(self.clients.claim()));
       self.addEventListener('fetch',e=>{if(new URL(e.request.url).pathname.endsWith('/videos.json'))e.respondWith(caches.open('matrix-public-old').then(c=>c.match('/videos.json')));});`);
      await page.evaluate(async()=>{await navigator.serviceWorker.register('service-worker.js');await navigator.serviceWorker.ready;});
      fs.writeFileSync(swPath,retirement);
      await page.evaluate(async()=>{const r=await navigator.serviceWorker.getRegistration();await r.update();});
      await page.waitForFunction(async()=>!(await navigator.serviceWorker.getRegistrations()).length);
      await page.waitForFunction(async()=>!(await caches.keys()).some(n=>n.startsWith('matrix-')));
     }finally{fs.writeFileSync(swPath,retirement);}
    }
    await page.reload();
    await page.waitForFunction(()=>/Showing [1-9]\d* of [1-9]\d*/.test(document.body?.textContent||''));
   }
   await page.goto(base+'/readonly.html');
   await page.waitForFunction(()=>/Showing [1-9]\d* of [1-9]\d*/.test(document.querySelector('#roResultCount')?.textContent||''),null,{timeout:20000});
   assert(requests.some(([s,u])=>s===200&&u.includes('videos.json')),'Browser never fetched videos.json');
   assert.deepEqual(errors,[],mode+' browser errors');
   console.log(JSON.stringify({mode,expected,result:'PASS',requests}));
   await context.close();
  }
  const context=await browser.newContext({viewport:{width:390,height:844},isMobile:true});
  const page=await context.newPage();
  // Simulate unavailable intro media: fallback must reach choice, never black screen.
  await context.route('**/*.mp4',r=>r.abort());
  await page.goto(base+'/index.html');await page.locator('#popupClose').click();
  await page.waitForURL(/choice(?:\.html)?(?:[?#].*)?$/,{timeout:15000});
  await page.goto(base+'/learn.html');
  for(const selector of ['#pdfKjvRestored','#pdfApocrypha']){
   const href=await page.locator(selector).getAttribute('href');
   const r=await context.request.get(new URL(href,page.url()).href);
   assert.equal(r.status(),200);assert((await r.body()).subarray(0,5).toString()==='%PDF-','PDF did not open');
  }
  await context.close();
  console.log('PASS intro fallback reaches pill choice and both PDF files open');
 }finally{await browser.close();server?.kill();}
})().catch(e=>{console.error(e);process.exit(1)});
