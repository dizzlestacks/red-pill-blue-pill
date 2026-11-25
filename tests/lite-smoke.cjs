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
  const probe=await browser.newContext();
  const markerResponse=await probe.request.get(base+'/build-info.json');
  assert.equal(markerResponse.status(),200);
  const marker=await markerResponse.json();
  assert.equal(marker.release,'2026.11.15.9');
  if(process.env.EXPECTED_COMMIT)assert.equal(marker.commit,process.env.EXPECTED_COMMIT,'Deployment is still serving a different commit');
  console.log('Build marker',marker);
  await probe.close();
  for(const mode of ['clean','old-site','old-data','storage-blocked']){
   const context=await browser.newContext({viewport:{width:390,height:844},isMobile:true,hasTouch:true});
   // Third-party fonts/thumbnails must not decide whether a catalogue release passes.
   await context.route('**/*',r=>{
    if(new URL(r.request().url()).origin!==new URL(base).origin)return r.abort();
    return r.continue();
   });
   const page=await context.newPage(),errors=[],requests=[];
   page.on('pageerror',e=>errors.push(e.message));
   page.on('response',r=>{if(/videos.json|lite-data.js|service-worker.js/.test(r.url()))requests.push([r.status(),r.url()]);});
   page.on('dialog',d=>d.dismiss());
   const data=await (await context.request.get(base+'/videos.json')).json();
   const expected=Array.isArray(data)?data.length:data.videos.length;
   assert(expected>0,'Release catalogue is empty');
   if(mode==='old-data'||mode==='old-site')await context.addInitScript(data=>{
    if(window.top!==window)return;
    try {
    if(sessionStorage.getItem('release-fixture-seeded'))return;
    sessionStorage.setItem('release-fixture-seeded','true');
    localStorage.setItem('videosCacheV4',JSON.stringify({time:Date.now(),data}));
    localStorage.setItem('matrix:public-offline',JSON.stringify({videos:[]}));
    localStorage.setItem('preferredTheme','gold');localStorage.setItem('splashSeen','true');localStorage.setItem('popupShown','true');
    }catch{}
   },mode==='old-site'?{videos:[{id:'obsolete',title:'Old saved catalogue',youtube_id:'abcdefghijk'}]}:data);
   if(mode==='old-data'){
    await context.route('**/service-worker.js',r=>r.fulfill({contentType:'application/javascript',body:`
      self.addEventListener('install',e=>e.waitUntil(self.skipWaiting()));
      self.addEventListener('activate',e=>e.waitUntil(self.clients.claim()));
      self.addEventListener('fetch',e=>{if(new URL(e.request.url).pathname.endsWith('/videos.json'))e.respondWith(caches.open('matrix-public-old').then(async c=>(await c.match('/videos.json'))||fetch(e.request)));});`}));
    await page.goto(base+'/404.html');
    await page.evaluate(async()=>{
      const c=await caches.open('matrix-public-old');await c.put('/videos.json',new Response('{"videos":[]}'));
      await navigator.serviceWorker.register('service-worker.js');await navigator.serviceWorker.ready;
    });
    await context.unroute('**/service-worker.js');
   }
   if(mode==='storage-blocked')await context.addInitScript(()=>{
    Storage.prototype.getItem=()=>{throw new DOMException('Blocked','SecurityError');};
    Storage.prototype.setItem=()=>{throw new DOMException('Blocked','SecurityError');};
   });
   // Optional Pi failure must not stop catalogue rendering.
   await context.route('**/api/public/backups',r=>r.abort());
   await page.goto(base+'/videos.html');
   await page.waitForFunction(()=>/Showing [1-9]\d* of [1-9]\d*/.test(document.body?.textContent||''),null,{timeout:20000}).catch(async error=>{
    console.error('Catalogue gate failed',mode,{errors,requests},await page.evaluate(async()=>{
     return {count:document.querySelector('#resultCount')?.textContent,workers:(await navigator.serviceWorker.getRegistrations()).map(r=>({active:r.active?.scriptURL,waiting:r.waiting?.state,installing:r.installing?.state})),caches:await caches.keys()};
    }));
    throw error;
   });
   await page.waitForFunction(n=>document.querySelector('#resultCount')?.textContent===`Showing ${n} of ${n}`,expected);
   assert.equal(await page.locator('#limited-mode').textContent(),'Limited view');
   assert((await page.locator('#release-marker').textContent()).includes(marker.commit.slice(0,12)));
   if(await page.locator('#popupClose').isVisible())await page.locator('#popupClose').click();
   const title=await page.locator('#videoGrid .card').first().locator('.card-title').textContent();
   await page.fill('#searchInput',title.trim());
   await page.waitForTimeout(250);
   assert(await page.locator('#videoGrid .card').count()>0,'Search returned no matching video');
   await page.fill('#searchInput','__no_such_video_783478__');await page.waitForTimeout(250);
   assert.equal(await page.locator('#videoGrid .card').count(),0,'Search did not filter');
   await page.fill('#searchInput','');
   const category=await page.locator('#categoryFilter option').nth(1).getAttribute('value');
   await page.selectOption('#categoryFilter',category);await page.waitForTimeout(250);
   assert(await page.locator('#videoGrid .card').count()>0,'Category returned no videos');
   await page.selectOption('#categoryFilter','');
   await page.locator('#videoGrid .thumb-wrap').first().click();
   assert(await page.locator('#videoGrid iframe,#videoGrid video').count()>0,'Video player did not open');
   if(mode==='old-data'){
      await page.waitForFunction(async()=>!(await navigator.serviceWorker.getRegistrations()).length);
      await page.waitForFunction(async()=>!(await caches.keys()).some(n=>n.startsWith('matrix-')));
    await page.reload();
    await page.waitForFunction(()=>/Showing [1-9]\d* of [1-9]\d*/.test(document.body?.textContent||''));
    if(!remote)await page.screenshot({path:path.join(arg('--dir'),'..','lite-smoke-phone.jpg')});
   }
   await page.goto(base+'/readonly.html');
   await page.waitForFunction(()=>/Showing [1-9]\d* of [1-9]\d*/.test(document.querySelector('#roResultCount')?.textContent||''),null,{timeout:20000});
   await page.waitForFunction(n=>document.querySelector('#roResultCount')?.textContent===`Showing ${n} of ${n} videos`,expected);
   assert(requests.some(([s,u])=>s===200&&u.includes('videos.json')),'Browser never fetched videos.json');
   assert.deepEqual(errors,[],mode+' browser errors');
   if(mode!=='storage-blocked'){
    // Exercise the intro with legacy flags cleared deliberately, just as a
    // visitor choosing to replay it would. Other legacy data remains intact.
    await page.evaluate(()=>{localStorage.removeItem('splashSeen');localStorage.removeItem('popupShown');});
    await page.goto(base+'/index.html');
    await page.locator('#popupClose').click();
    await page.waitForFunction(()=>{const v=document.querySelector('#introVideo');return v&&v.currentTime>0&&Number.isFinite(v.duration);},null,{timeout:30000});
    await page.evaluate(()=>{document.querySelector('#introVideo').playbackRate=16;});
    await page.waitForURL(/choice(?:\.html)?(?:[?#].*)?$/,{timeout:60000}).catch(async error=>{
      console.error('Intro diagnostic',page.url(),await page.evaluate(()=>{const v=document.querySelector('video');return v?{time:v.currentTime,duration:v.duration,ready:v.readyState,paused:v.paused,ended:v.ended,error:v.error?.message}:null;}));throw error;
    });
    await page.goto(base+'/learn.html');
    for(const selector of ['#pdfKjvRestored','#pdfApocrypha']){
     const href=await page.locator(selector).getAttribute('href');
     const r=await context.request.get(new URL(href,page.url()).href);
     assert.equal(r.status(),200);assert((await r.body()).subarray(0,5).toString()==='%PDF-');
    }
    assert.deepEqual(errors,[],mode+' intro/scripture browser errors');
   }
   console.log(JSON.stringify({mode,expected,result:'PASS',requests}));
   await context.close();
  }
  const context=await browser.newContext({viewport:{width:390,height:844},isMobile:true});
  const page=await context.newPage();
  if(remote){
   for(const route of ['/admin','/admin/','/editor.html']){
    const response=await context.request.get(base+route,{maxRedirects:0});
    assert.equal(response.status(),302);assert.equal(response.headers().location,'https://woke.you2mp3.music/admin');
   }
   const missing=await context.request.get(base+'/does-not-exist-release-check');
   assert.equal(missing.status(),404);assert((await missing.text()).includes('Page not found'));
  }
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
