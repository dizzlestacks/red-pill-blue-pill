/* Owner's release checklist on a phone-sized browser, complementing
   lite-smoke.cjs (intro, PDFs, old service workers, blocked storage, /admin
   redirect, not-found page).
     node tests/release-checklist.cjs --url https://lite-preview--wokearewe.netlify.app
     node tests/release-checklist.cjs --dir /tmp/lite
   Runs clean and with leftover data from older versions, each with the Pi
   reachable (real public Pi) and unreachable. Imported videos that only have
   the owner's copy must be playable from the Pi when it is reachable and say
   "Temporarily unavailable" otherwise; never missing. */
const {chromium}=require('playwright'),{spawn}=require('child_process');
const assert=require('node:assert/strict'),fs=require('node:fs');
const arg=n=>process.argv[process.argv.indexOf(n)+1];
const RELEASE='2026.11.15.16';
(async()=>{
 let server;const remote=process.argv.includes('--url');
 const base=remote?arg('--url').replace(/\/$/,''):'http://127.0.0.1:18766';
 if(!remote){server=spawn('python3',['-m','http.server','18766','--bind','127.0.0.1','--directory',arg('--dir')],{stdio:'ignore'});await new Promise(r=>setTimeout(r,600));}
 const executablePath=process.env.CHROMIUM_PATH||(fs.existsSync('/repl/tools/bin/chromium')?'/repl/tools/bin/chromium':undefined);
 const browser=await chromium.launch({executablePath,args:['--no-sandbox']});
 const passed=[];
 try{
  const probe=await browser.newContext();
  const marker=await (await probe.request.get(base+'/build-info.json')).json();
  assert.equal(marker.release,RELEASE,'build marker');
  if(process.env.EXPECTED_COMMIT)assert.equal(marker.commit,process.env.EXPECTED_COMMIT,'deployed commit');
  const catalog=await (await probe.request.get(base+'/videos.json')).json();
  await probe.close();
  const all=catalog.videos;
  const pi=String(catalog.public_pi_url||'').replace(/\/$/,'');
  const ownOnly=all.filter(v=>!v.youtube_id);
  const hubTotal=all.filter(v=>!String(v.youtube_id||'').startsWith('PL')).length;
  const newest=t=>Math.max(Date.parse(t.published||'')||0,Date.parse(t.added||'')||0);
  const newestTitle=[...all].filter(v=>!String(v.youtube_id||'').startsWith('PL')).sort((a,b)=>newest(b)-newest(a))[0].title;
  console.log('catalogue',{videos:all.length,hubTotal,ownOnly:ownOnly.map(v=>v.title),pi,newestTitle});
  assert(ownOnly.length>0,'catalogue has no own-copy-only imports to check');
  assert(pi,'catalogue has no public Pi address');
  const corsOk=new URL(base).origin==='https://wokearewe.com';
  console.log(corsOk?'Pi checked as-is':'Pi backup list relayed with a CORS header (Pi only grants wokearewe.com)');

  for(const leftover of [false,true])for(const piUp of [true,false]){
   const mode=`${leftover?'leftover data':'clean'}, Pi ${piUp?'reachable':'unreachable'}`;
   const context=await browser.newContext({viewport:{width:390,height:844},isMobile:true,hasTouch:true});
   await context.route('**/*',r=>{
    const o=new URL(r.request().url()).origin;
    if(o===new URL(base).origin)return r.continue();
    if(o===pi){
     if(!piUp)return r.abort();
     // The real Pi only grants its backup list to https://wokearewe.com. On any
     // other origin (preview, local) fetch the real list and add the header.
     if(!corsOk&&new URL(r.request().url()).pathname==='/api/public/backups')
      return r.fetch().then(res=>r.fulfill({response:res,headers:{...res.headers(),'access-control-allow-origin':new URL(base).origin}}));
     return r.continue();
    }
    return r.abort(); // thumbnails/fonts/embeds must not decide a release
   });
   if(leftover)await context.addInitScript(old=>{
    if(window.top!==window||sessionStorage.getItem('seeded'))return;
    sessionStorage.setItem('seeded','1');
    localStorage.setItem('videosCacheV4',JSON.stringify({time:Date.now(),data:old}));
    localStorage.setItem('videosCacheV3',JSON.stringify({time:Date.now(),data:old}));
    localStorage.setItem('matrix:public-offline',JSON.stringify({videos:[]}));
    localStorage.setItem('preferredTheme','gold');localStorage.setItem('splashSeen','true');localStorage.setItem('popupShown','true');
   },{videos:all.filter(v=>v.youtube_id).slice(0,20)});
   const page=await context.newPage(),errors=[];
   page.on('pageerror',e=>errors.push(e.message));page.on('dialog',d=>d.dismiss());
   await page.goto(base+'/videos.html');
   await page.waitForFunction(n=>document.querySelector('#resultCount')?.textContent===`Showing ${n} of ${n}`,hubTotal,{timeout:25000});
   if(await page.locator('#popupClose').isVisible())await page.locator('#popupClose').click();
   assert.equal(await page.locator('#videoGrid .card').count(),hubTotal,mode+': counter matches cards shown');
   assert.equal((await page.locator('#limited-mode').textContent()).trim(),'Limited view',mode+': notice');
   const card=t=>page.locator('#videoGrid .card').filter({has:page.locator('.card-title',{hasText:t})}).first();
   if(piUp)await page.waitForFunction(n=>document.querySelectorAll('#videoGrid .thumb-wrap[data-type="direct"]').length>=n,ownOnly.length,{timeout:15000});
   for(const v of ownOnly){
    const c=card(v.title);
    assert.equal(await c.count(),1,`${mode}: "${v.title}" listed`);
    if(piUp)assert.equal(await c.locator('.thumb-wrap[data-type="direct"]').count(),1,`${mode}: "${v.title}" playable from Pi`);
    else assert.match(await c.textContent(),/Temporarily unavailable/,`${mode}: "${v.title}" unavailable note`);
   }
   if(piUp){
    await card(ownOnly[0].title).locator('.thumb-wrap').click();
    await page.waitForSelector('#videoGrid video');
    const src=await page.locator('#videoGrid video').first().evaluate(v=>v.currentSrc||v.src);
    assert(src.startsWith(pi),mode+': own copy plays from the Pi');
    await page.reload();await page.waitForFunction(n=>document.querySelector('#resultCount')?.textContent===`Showing ${n} of ${n}`,hubTotal);
   }
   // Search finds an imported video by title.
   const imported=ownOnly[ownOnly.length-1].title;
   await page.fill('#searchInput',imported);await page.waitForTimeout(300);
   assert(await card(imported).count()===1,mode+': search finds imported title');
   await page.fill('#searchInput','');await page.waitForTimeout(300);
   // Categories.
   const category=await page.locator('#categoryFilter option').nth(1).getAttribute('value');
   await page.selectOption('#categoryFilter',category);await page.waitForTimeout(300);
   const inCat=await page.locator('#videoGrid .card').count();
   assert(inCat>0&&inCat<hubTotal,mode+': category filters');
   await page.selectOption('#categoryFilter','');await page.waitForTimeout(300);
   // New: newest to the hub first.
   await page.click('#tab-new');await page.waitForTimeout(400);
   assert.equal((await page.locator('#videoGrid .card .card-title').first().textContent()).trim(),newestTitle.trim(),mode+': New shows newest first');
   await page.click('#tab-all');await page.waitForTimeout(300);
   // A YouTube video opens.
   await page.locator('#videoGrid .thumb-wrap[data-type="video"]').first().click();
   const yt=await page.locator('#videoGrid iframe').first().getAttribute('src');
   assert(/youtube(-nocookie)?\.com\/embed\//.test(yt),mode+': YouTube player opens');
   // Read Only lists every video, imports included.
   await page.goto(base+'/readonly.html');
   await page.waitForFunction(n=>document.querySelector('#roResultCount')?.textContent===`Showing ${n} of ${n} videos`,all.length,{timeout:25000});
   assert.equal(await page.locator('.ro-item').count(),all.length,mode+': Read Only counter matches items');
   if(piUp)await page.waitForFunction(n=>[...document.querySelectorAll('.ro-item')].filter(e=>e.querySelector('[data-open]')).length>=document.querySelectorAll('.ro-item').length,null,{timeout:15000}).catch(()=>{});
   for(const v of ownOnly){
    const item=page.locator('.ro-item').filter({has:page.locator('.ro-title',{hasText:v.title})}).first();
    assert.equal(await item.count(),1,`${mode}: Read Only lists "${v.title}"`);
    if(piUp)assert.equal(await item.locator('[data-open]').count(),1,`${mode}: Read Only "${v.title}" watchable`);
    else assert.match(await item.textContent(),/Temporarily unavailable/,`${mode}: Read Only "${v.title}" unavailable`);
   }
   assert.equal((await page.locator('#limited-mode').textContent()).trim(),'Limited view',mode+': Read Only notice');
   // No Twitter link anywhere.
   for(const p of ['/index.html','/choice.html','/videos.html','/readonly.html','/learn.html','/404.html']){
    await page.goto(base+p);await page.waitForTimeout(300);
    const tw=await page.evaluate(()=>[...document.querySelectorAll('a')].filter(a=>/twitter\.com|\/\/(www\.)?x\.com|^twitter$/i.test(a.href+' ')||/^\s*(twitter|x)\s*$/i.test(a.textContent)).map(a=>a.href));
    assert.deepEqual(tw,[],`${mode}: no Twitter link on ${p}`);
    assert(!/twitter/i.test(await page.content()),`${mode}: no Twitter mention on ${p}`);
   }
   assert.deepEqual(errors,[],mode+': browser errors');
   passed.push(mode);console.log('PASS',mode);
   await context.close();
  }
  console.log('PASS release checklist:',passed.join(' | '));
 }finally{await browser.close();server?.kill();}
})().catch(e=>{console.error(e);process.exit(1)});
