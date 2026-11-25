// Phone confirmation-sheet checks. Run only against tests/preview_server.py
// (isolated temporary data, synthetic password, no upstream key or GitHub sync).
//   UI_TEST_URL=http://127.0.0.1:5000 node tests/mobile-dialogs.cjs
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs');
(async()=>{
 const executablePath=process.env.CHROMIUM_PATH||(fs.existsSync('/repl/tools/bin/chromium')?'/repl/tools/bin/chromium':undefined);
 const browser=await chromium.launch({headless:true,executablePath,args:['--no-sandbox']});
 try{
  const base=process.env.UI_TEST_URL||'http://127.0.0.1:5000';
  const context=await browser.newContext({viewport:{width:390,height:844},isMobile:true,hasTouch:true});
  const page=await context.newPage();
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto(base+'/admin');
  await page.locator('#password').fill('local-ui-fixture-only');
  await page.locator('#login-submit').click();
  await page.locator('#console').waitFor({state:'visible'});
  // Protected endpoints: no session -> 401, session without CSRF token -> 403.
  const anon=await browser.newContext();
  assert.equal((await anon.request.get(base+'/api/admin/watcher')).status(),401);
  await anon.close();
  assert.equal((await page.request.post(base+'/api/admin/watcher',{data:{enabled:false}})).status(),403);
  // Settings defaults.
  await page.locator('#tab-automatic').click();
  assert.equal(await page.locator('#watcher-profile').inputValue(),'https://www.tiktok.com/@wokearewe1');

  const geometry=async()=>{
    await page.waitForTimeout(60);
    const vp=page.viewportSize();
    const sheet=await page.locator('#confirm-dialog').boundingBox();
    const cancel=await page.locator('#confirm-cancel').boundingBox();
    const accept=await page.locator('#confirm-accept').boundingBox();
    assert(sheet.y>=-1&&sheet.y+sheet.height<=vp.height+1,'sheet outside viewport '+JSON.stringify({sheet,vp}));
    for(const b of [cancel,accept]){
      assert(b.height>=48&&b.width>=48,'tap target too small');
      assert(b.y>=0&&b.y+b.height<=vp.height+1,'action off screen '+JSON.stringify({b,vp}));
    }
    assert(cancel.x+cancel.width<=accept.x||accept.x+accept.width<=cancel.x||cancel.y+cancel.height<=accept.y,'actions overlap');
    assert.equal(await page.evaluate(()=>document.activeElement.id),'confirm-cancel');
    // Focus stays inside the modal in both directions.
    for(const key of ['Tab','Tab','Tab','Shift+Tab','Shift+Tab','Shift+Tab']){
      await page.keyboard.press(key);
      assert(await page.evaluate(()=>!!document.activeElement.closest('#confirm-dialog')),'focus escaped with '+key);
    }
    await page.locator('#confirm-cancel').focus();
    assert.equal(await page.evaluate(()=>getComputedStyle(document.body).position),'fixed','background not locked');
  };

  await page.locator('#tab-settings').click();
  await page.locator('#advanced summary').click();
  await page.locator('#load-raw').click();
  if(await page.locator('#confirm-dialog').evaluate(d=>d.open))await page.locator('#confirm-accept').click();
  await page.waitForFunction(()=>!document.getElementById('overwrite').disabled);
  for(const size of [{width:390,height:844},{width:320,height:568},{width:390,height:300},{width:844,height:390}]){
    await page.setViewportSize(size);
    await page.locator('#tab-settings').click();
    await page.locator('#overwrite').scrollIntoViewIfNeeded();
    const before=await page.evaluate(()=>scrollY);
    await page.locator('#overwrite').click();
    await geometry();
    await page.screenshot({path:`${require('node:os').tmpdir()}/dialog-${size.width}x${size.height}.jpg`});
    await page.locator('#confirm-cancel').click();
    await page.waitForTimeout(60);
    assert(Math.abs(await page.evaluate(()=>scrollY)-before)<3,'scroll position not restored');
    assert.equal(await page.evaluate(()=>document.activeElement.id),'overwrite','focus not returned to trigger');
  }
  // Long text scrolls inside the sheet; actions stay reachable.
  await page.setViewportSize({width:390,height:500});
  await page.locator('#overwrite').click();
  await page.locator('#confirm-text').evaluate(el=>el.textContent='Long confirmation. '.repeat(500));
  assert(await page.locator('#confirm-dialog .dialog-content').evaluate(el=>el.scrollHeight>el.clientHeight),'long text not internally scrollable');
  await geometry();
  await page.keyboard.press('Escape');
  await page.waitForTimeout(60);
  assert.equal(await page.locator('#confirm-dialog').evaluate(d=>d.open),false);
  // Simulated keyboard: shrink the visual viewport and confirm the sheet follows it.
  await page.setViewportSize({width:390,height:844});
  await page.locator('#overwrite').click();
  await page.evaluate(()=>{const s=document.documentElement.style;s.setProperty('--visual-height','420px');s.setProperty('--visual-top','0px');});
  await page.waitForTimeout(60);
  const kb=await page.locator('#confirm-accept').boundingBox();
  assert(kb.y+kb.height<=421,'actions hidden behind simulated keyboard '+JSON.stringify(kb));
  await page.locator('#confirm-cancel').click();
  await page.evaluate(()=>window.dispatchEvent(new Event('resize')));
  // Confirm path on harmless data: overwrite the catalogue with itself.
  await page.locator('#load-raw').click();
  if(await page.locator('#confirm-dialog').evaluate(d=>d.open))await page.locator('#confirm-accept').click();
  await page.locator('#overwrite').click();
  await page.locator('#confirm-accept').click();
  await page.locator('#message').filter({hasText:/saved/i}).waitFor({timeout:5000});
  // The saved notice must be on screen and not hidden under the bottom navigation.
  const note=await page.locator('#message').boundingBox(),nav=await page.locator('.bottom-nav').boundingBox();
  assert(note&&note.y>=0&&note.y+note.height<=page.viewportSize().height,'notice off screen '+JSON.stringify({note,vp:page.viewportSize(),y:await page.evaluate(()=>scrollY),parent:await page.evaluate(()=>document.getElementById('message').parentElement.id||document.getElementById('message').parentElement.className)}));
  assert(!nav||note.y+note.height<=nav.y+1||note.y>=nav.y+nav.height,'notice covered by navigation '+JSON.stringify({note,nav}));
  // Delete confirmation from the Videos tab (cancelled, nothing deleted).
  await page.locator('#tab-videos').click();
  const del=page.locator('#entries button.danger').first();
  await del.scrollIntoViewIfNeeded();await del.click();await geometry();
  assert.equal(await page.locator('#confirm-title').textContent(),'Delete this video?');
  await page.locator('#confirm-cancel').click();
  // Simulated review-ready import (route-mocked; no real download) uses the same sheet.
  await page.route('**/api/admin/queue',route=>{
    if(route.request().method()!=='GET')return route.continue();
    return route.fulfill({json:{items:[{id:'abcdef',kind:'tiktok',title:'Simulated TikTok post',tags:['truth'],status:'imported',
      url:'https://www.tiktok.com/@wokearewe1/video/12345678',reason:'Simulated verified copy',preview_state:'ready',add_end_card:false}],storage_ready:true}});
  });
  await page.locator('#tab-add').click();await page.locator('#queue-refresh').click();
  const pub=page.locator('[data-publish]');await pub.waitFor();await pub.scrollIntoViewIfNeeded();
  if(await pub.isDisabled())throw new Error('Simulated ready import cannot be published');
  await pub.click();await geometry();
  assert.equal(await page.locator('#confirm-title').textContent(),'Publish this import?');
  await page.locator('#confirm-cancel').click();
  assert.deepEqual(errors,[]);
  console.log('PASS Chromium mobile emulation: CSRF/auth guards, 4 viewports incl. 320x568 and landscape, deep scroll restore, focus trap/return, 48px+ actions, internal scrolling, simulated keyboard viewport, Cancel/Escape/Confirm. WebKit and physical iPhone NOT verified.');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1)});
