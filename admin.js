'use strict';
(() => {
  const $ = id => document.getElementById(id);
  const themes = ['dark','light','grey','pink','blue','vaporwave','solarized-dark','dracula','cyberpunk','sepia','midnight','facebook','mcdonalds','walmart','office','discord','netflix','apple','google','amazon','spotify','twitch','youtube','terminal','desert','ocean','forest','blood','gold','ice'];
  const state = { csrf:'', signedIn:false, catalog:null, revision:null, editing:null, stale:false, busy:false, queueBusy:false, poll:null, queue:new Map(), drafts:new Map(), connection:false, storage:false, watcher:null, watcherDirty:false, watcherBusy:false, watcherLoading:false };
  let authGeneration = 0;
  for (const name of themes) { const option = document.createElement('option'); option.value = name; option.textContent = name.split('-').map(s => s[0].toUpperCase()+s.slice(1)).join(' '); $('theme').append(option); }
  let savedTheme = 'terminal';
  try { savedTheme = localStorage.getItem('admin:theme') || localStorage.getItem('editor:theme') || 'terminal'; } catch {}
  $('theme').value = themes.includes(savedTheme) ? savedTheme : 'terminal';
  document.body.className = $('theme').value;
  $('theme').addEventListener('change', () => { document.body.className = $('theme').value; try { localStorage.setItem('admin:theme',$('theme').value); } catch {} });
  let noticeTimer, lastActionTrigger=null;
  function dismissNotice() { clearTimeout(noticeTimer); $('message').replaceChildren(); $('message').hidden=true; }
  function notice(message, error=false) {
    if(message&&!error&&!$('message').hidden&&$('message').classList.contains('error'))return;
    clearTimeout(noticeTimer);
    if(!message) return dismissNotice();
    const target=$('message');
    target.replaceChildren(node('span',message,'notice-copy'));
    const close=node('button','×','notice-close'); close.type='button'; close.setAttribute('aria-label','Dismiss message'); close.addEventListener('click',dismissNotice); target.append(close);
    target.classList.toggle('error',error); target.classList.toggle('success',!error);
    target.setAttribute('role',error?'alert':'status'); target.setAttribute('aria-live',error?'assertive':'polite');
    // An in-flow message cannot hide a keyboard, field or action button.
    // A busy (disabled) trigger drops focus to <body>; keep the notice beside the last action.
    const active=document.activeElement, focus=active&&active!==document.body?active:(lastActionTrigger?.isConnected?lastActionTrigger:null), form=focus?.closest('form');
    const anchor=!state.signedIn?$('login'):form?.id?form:focus?.closest('.panel,.login');
    if($('confirm-dialog').open) $('confirm-dialog').querySelector('.dialog-content').append(target);
    else if(anchor && !anchor.closest('dialog')) anchor.append(target);
    else document.querySelector('[role=tabpanel]:not([hidden])')?.prepend(target);
    target.hidden=false;
    if(!target.closest('dialog')){const box=target.getBoundingClientRect(),height=window.visualViewport?.height||window.innerHeight;if(box.top<0||box.bottom>height)target.scrollIntoView({block:'nearest'});}
    target.onclick=event=>{if(!error&&!event.target.closest('button'))dismissNotice();};
    if(!error)noticeTimer=setTimeout(dismissNotice,4500);
  }
  function updateViewport() {
    const viewport=window.visualViewport, root=document.documentElement.style;
    root.setProperty('--visual-top',`${viewport?.offsetTop||0}px`);
    root.setProperty('--visual-left',`${viewport?.offsetLeft||0}px`);
    root.setProperty('--visual-height',`${viewport?.height||window.innerHeight}px`);
    root.setProperty('--visual-width',`${viewport?.width||window.innerWidth}px`);
    document.body.classList.toggle('keyboard-open',!!viewport && window.innerHeight-viewport.height>150 && /INPUT|TEXTAREA|SELECT/.test(document.activeElement?.tagName||''));
  }
  window.visualViewport?.addEventListener('resize',updateViewport);
  window.visualViewport?.addEventListener('scroll',updateViewport);
  window.addEventListener('resize',updateViewport);
  updateViewport();
  let pressedButton=null;
  document.addEventListener('click',event=>{
    const button=event.target.closest('button');
    if(!button || button.disabled) return;
    pressedButton=button; lastActionTrigger=button; button.setAttribute('aria-busy','true');
    button.classList.add('pressed');
    setTimeout(()=>{if(pressedButton===button)pressedButton=null;},0);
    setTimeout(()=>{button.classList.remove('pressed');if(!button.dataset.requestBusy)button.removeAttribute('aria-busy');},250);
  },true);
  document.addEventListener('submit',event=>{
    if(event.submitter)pressedButton=event.submitter;
    setTimeout(()=>{pressedButton=null;},0);
  },true);
  document.addEventListener('invalid',event=>notice(event.target.validationMessage || 'Complete the required fields.',true),true);
  function node(tag, text, className) { const el=document.createElement(tag); if(text!==undefined) el.textContent=text; if(className) el.className=className; return el; }
  function action(text, handler, disabled=false) { const b=node('button',text); b.type='button'; b.disabled=disabled; b.addEventListener('click',async event=>{
    b.disabled=true; b.setAttribute('aria-busy','true');
    try { await handler(event); } catch(error) { notice(error.message,true); }
    finally {b.disabled=disabled;b.removeAttribute('aria-busy');}
  }); return b; }
  function list(value) { return Array.isArray(value) ? value.join(', ') : String(value ?? ''); }
  function categories(value) { return [...new Set(String(value).split(',').map(x=>x.trim()).filter(Boolean))]; }
  function tags(value) { return [...new Set(String(value).split(/[\s,]+/).map(x=>x.replace(/^#+/,'').trim()).filter(Boolean))]; }
  function safeURL(value) { if(!value) return ''; try { const u=new URL(value,location.origin); return ['http:','https:'].includes(u.protocol) ? u.href : ''; } catch { return ''; } }
  async function api(path, data) {
    const button=pressedButton, controller=new AbortController();
    const wasDisabled=button?.disabled;
    const timeout=setTimeout(()=>controller.abort(),30000);
    if(button){button.dataset.requestBusy='true';button.disabled=true;button.setAttribute('aria-busy','true');}
    try {
    const generation=authGeneration;
    const options={ credentials:'same-origin', headers:{'Accept':'application/json'}, cache:'no-store' };
    if(data!==undefined) { options.method='POST'; options.headers['Content-Type']='application/json'; if(path!=='/api/admin/login') options.headers['X-CSRF-Token']=state.csrf; options.body=JSON.stringify(data); }
    let response;
    options.signal=controller.signal;
    try { response=await fetch(path,options); } catch { throw new Error(controller.signal.aborted?'The request timed out. Refresh the status before retrying; it may have reached the server.':'Cannot reach the owner service. Check your connection and retry.'); }
    let body; try { body=await response.json(); } catch { throw new Error('The owner service returned an unreadable response. Check that the admin API is connected.'); }
    if(generation!==authGeneration) throw new Error('Session changed. Please sign in again.');
    if(!response.ok) {
      if(response.status===409 && path==='/api/videos') { state.stale=true; updateControls(); throw new Error('The catalogue changed in another session. Reload the catalogue before saving; nothing was overwritten.'); }
      if(response.status===401 && state.signedIn) clearSession();
      throw new Error(body.error || `Request failed (${response.status}).`);
    }
    return body;
    } finally {
      clearTimeout(timeout);
      if(button){delete button.dataset.requestBusy;button.disabled=wasDisabled;button.removeAttribute('aria-busy');}
    }
  }
  function updateControls() {
    const writable=state.signedIn && !!state.catalog && !state.stale && !state.busy;
    $('save-video').disabled=!writable; $('overwrite').disabled=!writable;
    $('backup-all').disabled=!writable || !(state.catalog?.videos.some(v=>v.backup_available!==true));
    $('import-submit').disabled=!state.signedIn || state.busy || !!state.importBusy;
  }
  function clearSession() {
    document.querySelector('main').prepend($('message'));
    state.overview=null;state.items=[];state.queueData=null;state.sync=null;homeSignature='';
    if($('confirm-dialog').open) finishConfirm(false);
    state.watcher=null; state.watcherDirty=false; $('watcher-form').reset(); watcherControls();
    authGeneration++; clearInterval(state.poll); state.poll=null; state.signedIn=false; state.csrf=''; state.catalog=null; state.revision=null; state.queue.clear(); state.drafts.clear(); state.editing=null; state.stale=false; state.connection=false; state.storage=false;
    openSection('home');
    $('console').hidden=true; $('logout').hidden=true; $('login').hidden=false; $('entries').replaceChildren(); $('queue').replaceChildren(); $('raw').value=''; $('import-urls').value=''; $('video-form').reset(); resetForm(); $('password').value=''; $('password').disabled=false; $('login-submit').disabled=false; $('login-submit').textContent='Sign in'; $('login-status').textContent='You are signed out.'; updateControls();
  }
  async function signedIn(csrf) {
    state.csrf=csrf; state.signedIn=true; $('login').hidden=true; $('console').hidden=false; $('logout').hidden=false; $('password').value=''; notice('');
    resetForm();
    try {$('first-guide').hidden=localStorage.getItem('admin:guide:phone-2026-11-15')==='seen';} catch {$('first-guide').hidden=false;}
    await Promise.allSettled([loadCatalog(),loadQueue(true),loadWatcher(true),loadOverview(),loadChecks()]);
    loadSync();
    clearInterval(state.poll); if(state.signedIn) state.poll=setInterval(()=>{loadQueue(true);loadSync();loadWatcher(true);loadOverview();if(!checksTimer)loadChecks();},5000);
  }
  async function session() {
    $('session-retry').hidden=true; $('login-submit').disabled=true; $('password').disabled=true; $('login-submit').textContent='Checking access…';
    try {
      const result=await api('/api/admin/session');
      document.querySelector('.release').textContent='Release '+(result.release||'2026.11.15.11');
      if(!result.configured) { $('login-submit').textContent='Password setup needed'; $('login-status').textContent='Set ADMIN_PASSWORD in the Pi settings file, then restart the service and try again. Sign-in stays disabled until then.'; $('session-retry').hidden=false; return; }
      if(result.authenticated) return signedIn(result.csrf);
      $('password').disabled=false; $('login-submit').disabled=false; $('login-submit').textContent='Sign in'; $('login-status').textContent='Only the owner can manage this archive.';
    } catch(error) { $('login-submit').textContent='Service unavailable'; $('login-status').textContent=error.message; $('session-retry').hidden=false; }
  }
  $('session-retry').addEventListener('click',session);
  $('login-form').addEventListener('submit',async event=>{
    event.preventDefault(); $('login-submit').disabled=true;
    try { const result=await api('/api/admin/login',{password:$('password').value}); await signedIn(result.csrf); notice('Signed in.'); }
    catch(error) { $('login-status').textContent=error.message; notice(error.message,true); }
    finally { $('login-submit').disabled=false; }
  });
  $('logout').addEventListener('click',async()=>{
    $('logout').disabled=true;
    try { await api('/api/admin/logout',{}); clearSession(); notice('Signed out.'); }
    catch(error) { notice(error.message,true); } finally { $('logout').disabled=false; }
  });
  const sectionScroll=new Map();
  let currentSection='home';
  function openSection(name) {
    if(!$(name))return;
    sectionScroll.set(currentSection,window.scrollY); currentSection=name;
    // Move the one message with its section without generating a notification.
    if(!$('message').hidden) $(name).prepend($('message'));
    document.querySelectorAll('[data-tab]').forEach(tab=>{const selected=tab.dataset.tab===name;tab.setAttribute('aria-selected',String(selected));tab.tabIndex=selected?0:-1;$(tab.dataset.tab).hidden=!selected;});
    window.scrollTo({top:sectionScroll.get(name)||0,behavior:'instant'});
  }
  document.querySelectorAll('[data-tab]').forEach(button=>button.addEventListener('click',()=>openSection(button.dataset.tab)));
  document.querySelectorAll('[data-go]').forEach(button=>button.addEventListener('click',()=>openSection(button.dataset.go)));
  $('dismiss-guide').addEventListener('click',()=>{$('first-guide').hidden=true;try{localStorage.setItem('admin:guide:phone-2026-11-15','seen');}catch{}});
  document.querySelector('[role=tablist]').addEventListener('keydown',event=>{
    const tabs=[...document.querySelectorAll('[data-tab]')],index=tabs.indexOf(document.activeElement);
    if(index<0 || !['ArrowLeft','ArrowRight','Home','End'].includes(event.key)) return;
    event.preventDefault();
    const next=event.key==='Home'?0:event.key==='End'?tabs.length-1:(index+(event.key==='ArrowRight'?1:-1)+tabs.length)%tabs.length;
    tabs[next].click(); tabs[next].focus();
  });
  async function loadCatalog() {
    try {
      const result=await api('/api/admin/catalog');
      if(!state.signedIn) return;
      if(!result.catalog || !Array.isArray(result.catalog.videos)) throw new Error('Your collection could not be read. Reload to try again.');
      state.catalog=result.catalog; state.revision=result.revision; state.stale=false;
      $('total-count').textContent=String(result.total ?? result.catalog.videos.length);
      const backed=result.backed_up ?? result.catalog.videos.filter(v=>v.backup_available===true).length;
      $('backup-count').textContent=String(backed); $('missing-count').textContent=String(Math.max(0,(result.total ?? result.catalog.videos.length)-backed));
      renderEntries(); updateControls(); renderHome();
      const opts=new Set(state.catalog.videos.flatMap(v=>Array.isArray(v.category)?v.category:categories(v.category??''))); $('category-options').replaceChildren(...[...opts].sort().map(c=>{ const o=node('option'); o.value=c; return o; }));
      return true;
    } catch(error) { notice(error.message,true); if(!state.catalog) { const box=node('div',undefined,'empty'); box.append(node('h3','Collection unavailable'),node('p','Your videos have not been loaded.','muted'),action('Retry',loadCatalog)); $('entries').replaceChildren(box); } return false; }
  }
  $('reload').addEventListener('click',async()=>{ if(await confirmAction('Reload your collection?','This gets the latest saved videos. Your unsaved form and JSON edits stay here; check them before saving.','Reload')) { if(await loadCatalog()) notice('Your collection is up to date. Check any unsaved edits before saving.'); } });
  function resetForm() { state.editing=null; $('video-form').reset(); $('added-date').value=new Date().toISOString().slice(0,10); $('form-heading').textContent='Add a video link'; $('form-mode').textContent='New video'; $('save-video').textContent='Save video'; $('reset-video').textContent='Clear form'; }
  $('reset-video').addEventListener('click',()=>{resetForm();notice('Form cleared.');});
  function editVideo(v,index) {
    state.editing={index,original:v}; $('source').value=v.youtube_id ? `https://www.youtube.com/watch?v=${v.youtube_id}` : v.url||''; $('title').value=v.title||''; $('categories').value=list(v.category); $('tags').value=list(v.tags); $('clip-start').value=v.start??v.clip_start??''; $('clip-end').value=v.end??v.clip_end??''; $('notes').value=v.notes??''; $('featured').checked=!!v.featured; $('pinned').checked=!!v.pinned;
    $('needs-title').checked=needsTitle(v); $('added-date').value=/^\d{4}-\d{2}-\d{2}/.exec(v.added||'')?.[0]||'';
    openSection('add');
    $('form-heading').textContent='Edit video'; $('form-mode').textContent='Editing'; $('save-video').textContent='Save changes'; $('reset-video').textContent='Cancel edit'; $('video-form').scrollIntoView({behavior:'instant',block:'start'}); $('title').focus({preventScroll:true});
  }
  function renderEntries() {
    if(!state.catalog) return;
    const query=$('search').value.toLowerCase().trim(); const frag=document.createDocumentFragment(); let count=0;
    state.catalog.videos.forEach((v,index)=>{
      const filter=$('video-filter').value,source=$('source-filter').value;
      if(filter==='title'&&!needsTitle(v)||filter==='backup'&&v.backup_available===true||filter==='pinned'&&!v.pinned||source&&videoSource(v)!==source)return;
      if(query && ![v.title,list(v.category),list(v.tags),v.url,v.youtube_id,v.notes].join(' ').toLowerCase().includes(query)) return;
      count++; const row=node('article',undefined,'entry'); const top=node('div',undefined,'entry-top'); const heading=node('div'); heading.append(node('h3',displayTitle(v))); const bs=node('div',undefined,'badges'); if(needsTitle(v))bs.append(node('span','Needs a title','badge')); if(v.pinned) bs.append(node('span','Pinned','badge')); if(v.featured) bs.append(node('span','Featured','badge')); heading.append(bs); top.append(heading,node('span',v.backup_available===true?'Backed up':'No backup','badge')); row.append(top,node('p',[videoSource(v)==='youtube'?'YouTube':videoSource(v)==='tiktok'?'TikTok':'Direct video',list(v.category),list(v.tags),v.added?new Date(v.added).toLocaleDateString():''].filter(Boolean).join(' · '),'meta'));
      const buttons=node('div',undefined,'actions'); const locked=state.busy||state.stale;
      buttons.append(action('Edit',()=>editVideo(v,index),locked),action(v.pinned?'Unpin':'Pin',()=>mutateCatalog(c=>{c.videos[index]={...c.videos[index],pinned:!v.pinned};},'Pin updated.'),locked),action('Back up',()=>backup({video_id:String(v.id)}),locked||!v.id||v.backup_available===true));
      if(v.backup_available===true && safeURL(v.backup_url)) { const link=node('a','View backup'); link.href=safeURL(v.backup_url); link.target='_blank'; link.rel='noopener noreferrer'; buttons.append(link); }
      const del=action('Delete',async()=>{ if(await confirmAction('Delete this video?',`“${v.title||'Untitled video'}” will be removed from the catalogue. This cannot be undone here.`,'Delete video')) await mutateCatalog(c=>{c.videos.splice(index,1);},'Video deleted.'); },locked); del.className='danger'; buttons.append(del); row.append(buttons); frag.append(row);
    });
    if(!count) { const empty=node('div',undefined,'empty'); empty.append(node('h3','No videos here yet'),node('p','Try another search or filter, or add a video to your collection.','muted'),action('Add a video',()=>openSection('add'))); frag.append(empty); }
    $('video-results').textContent=`${count} of ${state.catalog.videos.length} videos`;
    $('entries').replaceChildren(frag);
  }
  $('search').addEventListener('input',renderEntries);
  $('video-filter').addEventListener('change',renderEntries);
  $('source-filter').addEventListener('change',renderEntries);
  $('title').addEventListener('input',()=>{$('needs-title').checked=!meaningfulTitle($('title').value);});
  function meaningfulTitle(title) {return !!String(title||'').trim()&&!/^untitled(?:\s|$)/i.test(String(title).trim());}
  function needsTitle(video) {return video.needs_title===true||video.needs_title===1||!meaningfulTitle(video.title);}
  function displayTitle(video) {return String(video.title||'').trim()||`Untitled${video.added?' '+String(video.added).slice(0,10):' video'}`;}
  function videoSource(video) {return video.youtube_id||/youtube\.com|youtu\.be|\/media\/yt-/.test(video.url||video.backup_url||'')?'youtube':video.source==='tiktok'||video.kind==='tiktok'||/tiktok\.com|\/media\/tt-|^tt:/.test([video.url,video.backup_url,video.source_url,video.id].join(' '))?'tiktok':'direct';}
  function sourceFields(value) {
    const raw=value.trim(); if(/^[\w-]{11}$/.test(raw)) return {youtube_id:raw};
    if (/^\/media\/(?:tt-\d+|yt-[\w-]+)$/.test(raw)) return {youtube_id:'',url:raw};
    const url=safeURL(raw); if(!url || !/^https?:\/\//i.test(raw)) throw new Error('Enter a valid HTTP or HTTPS source link.');
    const u=new URL(url); const host=u.hostname.toLowerCase().replace(/^www\./,''); let id='';
    if(host==='tiktok.com'||host.endsWith('.tiktok.com'))return {youtube_id:'',url,source_url:url};
    if(host==='youtu.be') id=u.pathname.split('/')[1];
    else if(['youtube.com','m.youtube.com','music.youtube.com','youtube-nocookie.com'].includes(host)) id=u.searchParams.get('v') || u.searchParams.get('list') || (/^\/(shorts|embed|live)\//.test(u.pathname)?u.pathname.split('/')[2]:'');
    else return {youtube_id:'',url};
    if(!/^[\w-]{11}$/.test(id||'') && !/^PL[\w-]+$/.test(id||'')) throw new Error('This YouTube link does not contain a valid video ID.');
    return {youtube_id:id,url:''};
  }
  function seconds(value) {
    if(!value.trim()) return null;
    if(!/^\d+(?::[0-5]?\d){0,2}(?:\.\d+)?$/.test(value.trim())) throw new Error('Clip times must be non-negative seconds or m:ss / h:mm:ss.');
    return value.trim().split(':').reduce((sum,n)=>sum*60+Number(n),0);
  }
  $('video-form').addEventListener('submit',async event=>{
    event.preventDefault();
    try {
      const rawSource=$('source').value.trim();
      if(!state.editing && /^https:\/\/(?:www\.|m\.|vm\.|vt\.)?(?:youtube\.com|youtu\.be|tiktok\.com)\//i.test(rawSource)){
        const metadata={title:$('title').value.trim(),category:categories($('categories').value),tags:tags($('tags').value),added:$('added-date').value,notes:$('notes').value,pinned:$('pinned').checked,featured:$('featured').checked,needs_title:$('needs-title').checked,start:seconds($('clip-start').value),end:seconds($('clip-end').value)};
        const result=await api('/api/admin/manual',{urls:[rawSource],metadata,keep_copy:$('manual-copy').checked});
        notice(result.results.map(r=>`${r.status}${r.backup?' · copy: '+r.backup.status:''}`).join('; '),result.results.some(r=>r.backup?.status==='failed'));
        await loadCatalog();await loadQueue(true);resetForm();return;
      }
      const source=sourceFields($('source').value),start=seconds($('clip-start').value),end=seconds($('clip-end').value);
      if(start!==null && end!==null && end<=start) throw new Error('Clip end must be after clip start.');
      const editing=state.editing;
      if(editing && !state.catalog.videos.includes(editing.original)) throw new Error('The catalogue was refreshed since you opened this edit. Open the current video again to avoid replacing newer changes.');
      const video={...(editing?editing.original:{youtube_id:'',url:'',added:new Date().toISOString().slice(0,10)}),...source,title:$('title').value.trim(),category:categories($('categories').value),tags:tags($('tags').value),featured:$('featured').checked,pinned:$('pinned').checked,notes:$('notes').value};
      video.needs_title=$('needs-title').checked||!meaningfulTitle(video.title);
      if($('added-date').value)video.added=$('added-date').value;
      if(!video.title)video.title='Untitled '+(video.added||new Date().toISOString()).slice(0,10);
      const startKey=editing && 'clip_start' in editing.original && !('start' in editing.original)?'clip_start':'start';
      const endKey=editing && 'clip_end' in editing.original && !('end' in editing.original)?'clip_end':'end';
      if(start===null) delete video[startKey]; else video[startKey]=start;
      if(end===null) delete video[endKey]; else video[endKey]=end;
      const ok=await mutateCatalog(c=>{ if(editing) c.videos[editing.index]=video; else c.videos.push(video); },editing?'Video updated.':'Video added.');
      if(ok) resetForm();
    } catch(error) { notice(error.message,true); }
  });
  async function mutateCatalog(change,success) {
    if(state.busy || state.stale || !state.catalog) { notice(state.stale?'Reload the catalogue before saving.':'Wait for the catalogue to be available.',true); return false; }
    state.busy=true; updateControls(); renderEntries();
    try {
      const next=JSON.parse(JSON.stringify(state.catalog)); change(next);
      const result=await api('/api/videos',{catalog:next,revision:state.revision});
      state.catalog=next; state.revision=result.revision; notice(success + (result.durable_state===false?' Your collection was saved, but its recovery copy failed. Check storage in Settings.':''),result.durable_state===false);
      await loadCatalog(); return true;
    } catch(error) { notice(error.message,true); return false; }
    finally { state.busy=false; updateControls(); renderEntries(); }
  }
  let pendingConfirm=null, confirmTrigger=null, scrollLock=null;
  function unlockDialog() {
    if(scrollLock) {
      const saved=scrollLock; scrollLock=null;
      document.body.style.position=saved.position; document.body.style.top=saved.top; document.body.style.left=saved.left; document.body.style.width=saved.width; document.body.style.overflow=saved.overflow;
      document.documentElement.style.scrollBehavior='auto'; window.scrollTo(saved.x,saved.y); document.documentElement.style.scrollBehavior=saved.scrollBehavior;
    }
    if($('message').closest('dialog'))document.querySelector('[role=tabpanel]:not([hidden])')?.prepend($('message'));
    if(confirmTrigger?.isConnected) confirmTrigger.focus({preventScroll:true});
    confirmTrigger=null; updateViewport();
  }
  function confirmAction(title,text,label='Confirm') {
    if(pendingConfirm) return Promise.resolve(false);
    confirmTrigger=pressedButton||document.activeElement; lastActionTrigger=confirmTrigger;
    // Action wrappers mark their trigger busy before awaiting confirmation.
    // The native modal makes it inert; keep it focusable for a reliable return.
    if(confirmTrigger?.tagName==='BUTTON') {confirmTrigger.disabled=false;confirmTrigger.removeAttribute('aria-busy');}
    const style=document.body.style;
    scrollLock={x:window.scrollX,y:window.scrollY,position:style.position,top:style.top,left:style.left,width:style.width,overflow:style.overflow,scrollBehavior:document.documentElement.style.scrollBehavior};
    style.position='fixed';style.top=`-${scrollLock.y}px`;style.left=`-${scrollLock.x}px`;style.width='100%';style.overflow='hidden';
    updateViewport();
    $('confirm-title').textContent=title; $('confirm-text').textContent=text; $('confirm-accept').textContent=label;
    $('confirm-dialog').showModal(); $('confirm-cancel').focus({preventScroll:true}); $('confirm-dialog').querySelector('.dialog-content').scrollTop=0;
    return new Promise(resolve=>{pendingConfirm=resolve;});
  }
  function finishConfirm(value) { const callback=pendingConfirm; pendingConfirm=null; $('confirm-dialog').close(); unlockDialog(); if(callback) callback(value); }
  $('confirm-cancel').addEventListener('click',()=>finishConfirm(false)); $('confirm-accept').addEventListener('click',()=>finishConfirm(true)); $('confirm-dialog').addEventListener('cancel',event=>{event.preventDefault();finishConfirm(false);});
  // Keep keyboard focus inside the sheet; native modals can otherwise Tab out to browser UI.
  $('confirm-dialog').addEventListener('keydown',event=>{
    if(event.key!=='Tab') return;
    const items=[...$('confirm-dialog').querySelectorAll('button:not([disabled]),a[href],[tabindex]:not([tabindex="-1"])')].filter(el=>el.offsetParent!==null);
    if(!items.length) return;
    const first=items[0], last=items[items.length-1], active=document.activeElement;
    if(event.shiftKey&&(active===first||!$('confirm-dialog').contains(active))) {event.preventDefault();last.focus({preventScroll:true});}
    else if(!event.shiftKey&&(active===last||!$('confirm-dialog').contains(active))) {event.preventDefault();first.focus({preventScroll:true});}
  });
  $('confirm-dialog').addEventListener('close',()=>{if(scrollLock){const callback=pendingConfirm;pendingConfirm=null;unlockDialog();if(callback)callback(false);}});
  function reportResults(result,fallback) {
    const results=Array.isArray(result.results)?result.results:[];
    if(!results.length) return fallback;
    const skipped=results.filter(r=>r.status==='duplicate skipped').length;
    const failed=results.filter(r=>r.ok===false||['failed','blocked'].includes(r.status)||r.error);
    const accepted=results.length-skipped-failed.length;
    return [accepted?`${accepted} ${accepted===1?'request':'requests'} added.`:'',skipped?`${skipped} already in your archive or waiting to import.`:'',failed.length?`${failed.length} could not start. ${failed[0].reason||failed[0].error||'Check the import details.'}`:''].filter(Boolean).join(' ');
  }
  async function backup(data) {
    if(state.busy) return;
    state.busy=true; updateControls(); renderEntries();
    try { const result=await api('/api/admin/backup',data); await Promise.all([loadCatalog(),loadQueue(true)]);notice(reportResults(result,'Backup requests have been checked. See your collection for completed copies.'),result.results?.some(r=>r.ok===false||r.error||['failed','blocked'].includes(r.status))); }
    catch(error) { notice(error.message,true); } finally { state.busy=false; updateControls(); renderEntries(); }
  }
  $('backup-all').addEventListener('click',async()=>{if(await confirmAction('Back up all missing media?','Request a backup for every video without a verified backup. This may take time and use media storage. Existing verified backups will be left alone.','Back up missing')) await backup({all:true});});
  function loadRaw() { if(state.catalog) { $('raw').value=JSON.stringify(state.catalog,null,2);notice('Current JSON loaded.'); } else notice('Load the catalogue first.',true); }
  $('load-raw').addEventListener('click',async()=>{if(!$('raw').value || await confirmAction('Replace JSON draft?','Replace the text in this editor with the currently loaded catalogue?','Replace draft')) loadRaw();});
  $('download').addEventListener('click',()=>{ if(!state.catalog) return notice('Load the catalogue first.',true); const url=URL.createObjectURL(new Blob([JSON.stringify(state.catalog,null,2)],{type:'application/json'})); const a=node('a'); a.href=url; a.download='matrix-videos.json'; document.body.append(a);a.click();a.remove(); notice('JSON download requested. Check your browser downloads.'); setTimeout(()=>URL.revokeObjectURL(url),10000); });
  $('overwrite').addEventListener('click',async()=>{
    try {
      const next=JSON.parse($('raw').value); if(!next || Array.isArray(next) || !Array.isArray(next.videos) || next.videos.some(v=>!v || typeof v!=='object' || Array.isArray(v))) throw new Error('Paste a catalogue object containing a videos array of video objects.');
      if(await confirmAction('Overwrite the full catalogue?',`Replace the catalogue with ${next.videos.length} videos and all settings from this JSON. Removed entries will be deleted. Download a copy first if you need a recovery file.`,'Overwrite catalogue')) await mutateCatalog(c=>{Object.keys(c).forEach(k=>delete c[k]); Object.assign(c,next);},'Catalogue saved.');
    } catch(error) { notice(error instanceof SyntaxError?'Invalid JSON. Check commas, quotes, and brackets before trying again.':error.message,true); }
  });
  async function loadQueue(silent=false) {
    if(!state.signedIn) return;
    if(state.queueBusy) {if(!silent){state.queueRefreshRequested=true;notice('Refreshing queue…');$('queue-refresh').disabled=true;$('queue-refresh').setAttribute('aria-busy','true');}return;}
    state.queueBusy=true;
    try {
      const data=await api('/api/admin/queue'); if(!state.signedIn) return;
      state.connection=!!data.connection_configured; state.storage=!!data.storage_ready;
      state.queueData=data; state.items=data.items||[];
      $('connection-status').textContent=state.connection?'Your import connection is set up.':'Add YOU2MP3_API_KEY to the Pi settings file and restart the service. You can paste links now; downloads will wait.';
      $('storage-status').textContent=(data.storage_message || (state.storage?'Media storage is ready.':'Permanent media storage is not ready. You can queue jobs; copying waits for storage.')) + (data.state_storage_message ? ' '+data.state_storage_message : '');
      $('settings-storage').textContent=$('storage-status').textContent;
      renderQueue(state.items); filterQueue(); updateControls(); renderHome();
      if(!silent || state.queueRefreshRequested)notice('Import status refreshed.');
    } catch(error) { state.queueData=null; $('connection-status').textContent=error.message; if(!state.items?.length){const empty=node('div',undefined,'empty');empty.append(node('h3','Imports could not be loaded'),node('p',error.message,'help'),action('Try again',()=>loadQueue()));$('queue').replaceChildren(empty);} if(!silent)notice(error.message,true); renderHome(); }
    finally { state.queueBusy=false;if(state.queueRefreshRequested){state.queueRefreshRequested=false;$('queue-refresh').disabled=false;$('queue-refresh').removeAttribute('aria-busy');} }
  }
  $('queue-refresh').addEventListener('click',()=>loadQueue());
  const watcherFields=['watcher-enabled','watcher-profile','watcher-interval','watcher-limit','watcher-disk','watcher-auto-publish'];
  let watcherEditVersion=0;
  function watcherControls() {
    const unavailable=!state.signedIn||!state.watcher, busy=state.watcherBusy;
    watcherFields.forEach(id=>{$(id).disabled=unavailable||busy;});
    $('watcher-save').disabled=unavailable||busy;
    $('publishing-save').disabled=unavailable||busy; $('limits-save').disabled=unavailable||busy;
    $('watcher-reset').disabled=unavailable||busy||!state.watcherDirty;
    const status=String(state.watcher?.status||'').toLowerCase();
    const blocked=unavailable||busy||status==='unsupported'||/no[ _-]?token|missing[ _-]?token|not configured/.test(status);
    $('watcher-check').disabled=blocked;
    $('watcher-older').disabled=blocked||!!state.watcher?.history_done;
    $('watcher-refresh').disabled=busy||state.watcherLoading;
    for(const id of ['watcher-save','watcher-check','watcher-older']) {
      if(busy && $(id).dataset.activeRequest) $(id).setAttribute('aria-busy','true');
      else $(id).removeAttribute('aria-busy');
    }
  }
  function fillWatcher() {
    const data=state.watcher;if(!data)return;
    $('watcher-enabled').checked=!!data.enabled; $('watcher-profile').value=data.profile||'';
    $('watcher-interval').value=String(data.interval_minutes??15);
    $('watcher-limit').value=data.hourly_limit??6; $('watcher-disk').value=data.min_free_gb??5;
    $('watcher-auto-publish').checked=!!data.auto_publish;
    $('watcher-edit-status').textContent='Saved settings loaded. Status refreshes every 5 seconds.';
  }
  function watcherTime(value) {
    if(!value)return 'Not yet';
    const date=new Date(typeof value==='number'?value*1000:value);
    return Number.isNaN(date.getTime())?String(value):date.toLocaleString();
  }
  function renderWatcher(data) {
    state.watcher=data;
    renderBackCatalogue(data);
    const status=String(data.status||'idle'), normalized=status.toLowerCase();
    $('watcher-badge').textContent=data.error?'Needs attention':data.enabled?'On':'Off';
    $('watcher-status').textContent=normalized==='unsupported'?'Watching is not available from the import service yet.':/no[ _-]?token|missing[ _-]?token|not configured|waiting for connection/.test(normalized)?'Set up the import connection in Settings to watch for posts.':data.error?'Watching needs your attention. See the details below.':data.enabled?'New posts will be checked and imported on your schedule.':'Watching is off. Turn it on and save to check for new posts.';
    $('watcher-error').textContent=data.error?`Watcher error: ${data.error}`:'';
    $('watcher-end-card-help').textContent='No end cards on TikTok imports. Previously submitted jobs retain their original request.';
    $('watcher-last').textContent=`Last checked: ${watcherTime(data.last_check)}. ${data.last_count??0} new posts found.`;
    $('watcher-next').textContent=`Next check: ${data.next_check?watcherTime(data.next_check):'Not scheduled'}`;
    const free=data.free_gb==null?null:Number(data.free_gb);
    const low=free!==null&&Number.isFinite(free)&&free<Number(data.min_free_gb);
    $('watcher-free').textContent=free===null?'Free disk: unavailable':`${low?'Disk warning: ':''}${Number.isFinite(free)?free.toFixed(1):'Unknown'} GB free / reserve ${data.min_free_gb??'—'} GB${low?' — imports may be paused.':''}`;
    $('watcher-free').classList.toggle('error',low);
    $('watcher-history').textContent=`${data.inventory_count??'An unknown number of'} posts found so far. TikTok may not list your complete history. ${data.history_done?'All currently listed older posts have been checked.':'You can import the next 25 older posts below, or use Import everything.'} Watching looks for new posts after ${watcherTime(data.watching_since)}. ${data.warning||''}`;
    if(!state.watcherDirty&&!state.watcherBusy)fillWatcher();
    watcherControls(); renderHome();
  }
  async function loadWatcher(silent=false) {
    if(!state.signedIn||state.watcherLoading||state.watcherBusy)return;
    state.watcherLoading=true;watcherControls();
    if(!silent)$('watcher-refresh').setAttribute('aria-busy','true');
    try {const data=await api('/api/admin/watcher');if(state.signedIn)renderWatcher(data);}
    catch(error){$('watcher-error').textContent=error.message;if(!state.watcher){$('watcher-status').textContent='Watcher unavailable. Use Refresh watcher to retry.';$('watcher-badge').textContent='Unavailable';}if(!silent)notice(error.message,true);}
    finally{state.watcherLoading=false;$('watcher-refresh').removeAttribute('aria-busy');watcherControls();}
  }
  function renderLibrary(m){
    const l=m.library||{},box=$('library-box');
    if(!m.configured){$('library-status').textContent='Set up the NAS copy first (MIRROR_DIR on the Pi). The library is built there automatically.';$('library-rebuild').disabled=true;$('library-problems').replaceChildren();return;}
    $('library-rebuild').disabled=false;
    const when=l.last_run?' Last update '+new Date(l.last_run*1000).toLocaleTimeString()+'.':'';
    const progress=l.total?`${l.done} of ${l.total} files in the library`:l.last_run?'No saved videos to add yet':'Not built yet';
    const words={'up to date':'Up to date','copying':'Copying','updating':'Updating','waiting for NAS':'Waiting for the NAS','not set up':'Not set up'};
    $('library-status').textContent=`${words[l.state]||plainStatus(l.state||'waiting')}. ${progress}.${l.copying?' Copying “'+l.copying+'”.':''}${l.rebuild?' Rebuild in progress.':''}${when} ${l.last_error||''}`;
    const list=$('library-problems');list.replaceChildren();
    for(const p of (l.problems||[]).slice(0,20)){const row=node('p',undefined,'help');row.append(node('strong',p.title+': '),document.createTextNode(p.reason));list.append(row);}
  }
  $('library-rebuild').addEventListener('click',async()=>{
    if(!await confirmAction('Rebuild the NAS library?','Every file in the library is checked again and anything missing is copied. Nothing is deleted. This runs gently in the background and can take a while.','Rebuild library'))return;
    try{const r=await api('/api/admin/nas/rebuild',{});notice('Library rebuild started. Progress shows here.');renderLibrary({...(state.watcher?.mirror||{}),configured:true,library:r.library});}
    catch(error){notice(error.message,true);}
  });
  let backBusy=false;
  function renderBackCatalogue(data) {
    if(backBusy)return;
    const b=data.back_catalog||{},r=b.run,counts=b.counts||{},m=data.mirror||{};
    $('back-status').textContent=r?`${plainStatus(r.state)}. ${r.scanned||0} posts checked. ${Object.entries(counts).map(([k,n])=>`${n} ${plainStatus(k).toLowerCase()}`).join(' · ')}. ${r.reason||''}`:'No import of all your posts has started yet.';
    $('back-badge').textContent=r&&!['done','paused'].includes(r.state)?'On':'Off';
    $('mirror-status').textContent=`${m.configured?'NAS copy: '+plainStatus(m.state):'NAS copy is not set up'}. ${m.count??'—'} of ${m.total??'—'} backups checked. ${m.error||''}`;
    renderLibrary(m);
    const controls=$('back-actions');controls.replaceChildren();
    let toggle=$('back-enabled');
    if(!toggle){
      const label=node('label','Import running','toggle-line');toggle=node('input');toggle.id='back-enabled';toggle.type='checkbox';toggle.className='switch-input';toggle.setAttribute('role','switch');label.append(toggle);$('back-status').before(label);
      toggle.addEventListener('change',()=>{toggle.checked=!toggle.checked;controls.querySelector('button')?.click();});
    }
    toggle.checked=!!r&&!['done','paused'].includes(r.state);toggle.disabled=!state.watcher;
    const mutate=async(op,body={})=>{
      backBusy=true;
      try {await api('/api/admin/back-catalog/'+op,body);notice('Older imports updated.');}
      finally{backBusy=false;}
      await loadWatcher();
    };
    if(!r||r.state==='done')controls.append(action('Import everything',async()=>{
      backBusy=true;
      try {
        const e=await api('/api/admin/back-catalog/estimate');
        const gb=n=>(n/1024**3).toFixed(1);
        const message=`${e.posts} posts listed (duplicates skipped). Estimated ${gb(e.estimated_bytes)} GB, with ${gb(e.free_bytes)} GB free and ${gb(e.reserve_bytes)} GB reserved. Size is an estimate, not a guarantee. Imports pause safely if space runs low. Publishing follows your saved Publish automatically setting.`;
        if(await confirmAction('Import everything?',message,'Start importing')){
          await api('/api/admin/back-catalog/start',{add_end_card:false});
          notice('Import everything started. You can close this page.');
        }
      }finally{backBusy=false;await loadWatcher();}
    }));
    else controls.append(action(r.state==='paused'?'Resume imports':'Pause imports',()=>mutate(r.state==='paused'?'resume':'pause')));
    controls.querySelector('button')?.classList.add('primary');
    $('back-matches').replaceChildren();
    for(const match of b.matches||[]){
      const row=node('div',undefined,'stack');
      row.append(node('p',`${match.title||'Imported post'} was matched to a video already in your collection.`));
      row.append(action('Undo match',async()=>{
        if(await confirmAction('Undo this match?','The original video stays unchanged. The imported copy returns to review without automatic publishing.','Undo match'))
          await mutate('undo',{job_id:match.job_id});
      }));
      $('back-matches').append(row);
    }
  }
  function markWatcherDirty(){watcherEditVersion++;state.watcherDirty=true;$('watcher-edit-status').textContent='You have unsaved settings. Save to apply them.';watcherControls();}
  watcherFields.forEach(id=>$(id).addEventListener('input',markWatcherDirty));
  $('watcher-reset').addEventListener('click',()=>{watcherEditVersion++;state.watcherDirty=false;fillWatcher();watcherControls();notice('Watcher edits discarded.');});
  $('watcher-refresh').addEventListener('click',()=>loadWatcher());
  async function watcherRequest(operation,data,button) {
    if(state.watcherBusy)return;
    const version=watcherEditVersion;
    state.watcherBusy=true;button.dataset.activeRequest='true';watcherControls();
    try {
      const result=await api('/api/admin/watcher'+operation,data);
      if(!state.signedIn)return;
      if(!operation&&version===watcherEditVersion)state.watcherDirty=false;
      renderWatcher(result);
      const text=(!operation?'Watcher settings saved. ': 'Watcher check scheduled. ') + (result.error?`Watcher status: ${result.error}`:result.status==='unsupported'?'watching not available yet':operation?'Status will update shortly.':'');
      notice(text,!!result.error);
    }catch(error){$('watcher-error').textContent=error.message;notice(error.message,true);}
    finally{state.watcherBusy=false;delete button.dataset.activeRequest;watcherControls();if(!state.watcherDirty)fillWatcher();}
  }
  $('watcher-form').addEventListener('submit',event=>{
    event.preventDefault();
    const interval_minutes=Number($('watcher-interval').value),hourly_limit=Number($('watcher-limit').value),min_free_gb=Number($('watcher-disk').value);
    if(![5,15,30,60].includes(interval_minutes)||!Number.isInteger(hourly_limit)||hourly_limit<1||hourly_limit>60||!Number.isInteger(min_free_gb)||min_free_gb<1||min_free_gb>1024)return notice('Check the watcher interval, hourly limit, and free disk reserve.',true);
    const profile=$('watcher-profile').value.trim();
    if(profile){try{const url=new URL(profile);if(url.protocol!=='https:'||!['www.tiktok.com','tiktok.com','m.tiktok.com'].includes(url.hostname)||!/^\/@[^/]+\/?$/.test(url.pathname))throw new Error();}catch{return notice('Enter a TikTok profile URL such as https://www.tiktok.com/@yourprofile.',true);}}
    if($('watcher-enabled').checked&&!profile)return notice('Enter your TikTok profile before enabling the watcher.',true);
    watcherRequest('',{enabled:$('watcher-enabled').checked,profile,interval_minutes,hourly_limit,min_free_gb,auto_publish:$('watcher-auto-publish').checked,add_end_card:false},$('watcher-save'));
  });
  $('watcher-check').addEventListener('click',()=>watcherRequest('/check',{},$('watcher-check')));
  $('watcher-older').addEventListener('click',()=>watcherRequest('/older',{},$('watcher-older')));
  const importEndCard=node('input'); importEndCard.type='checkbox'; importEndCard.id='import-end-card'; importEndCard.checked=false;
  const importEndLabel=node('label',undefined,'end-card-control'); importEndLabel.htmlFor=importEndCard.id; importEndLabel.append(importEndCard,document.createTextNode('Add end-card to these imports'));
  const importProviderDefault=node('input');importProviderDefault.type='checkbox';importProviderDefault.id='import-provider-default';
  const importProviderLabel=node('label',undefined,'end-card-control');importProviderLabel.htmlFor=importProviderDefault.id;importProviderLabel.append(importProviderDefault,document.createTextNode('Use service default instead'));
  importEndLabel.hidden=true;importProviderLabel.hidden=true;
  const importEndHelp=node('p','TikTok imports are saved without adding an end card.','help');
  importProviderDefault.addEventListener('change',()=>{importEndCard.disabled=importProviderDefault.checked;});
  $('import-submit').before(importEndLabel,importProviderLabel,importEndHelp);
  const bulkCopyLabel=node('label','Keep my own copies and mirror them to the NAS when configured');
  const bulkCopy=node('input');bulkCopy.type='checkbox';bulkCopy.checked=true;bulkCopy.id='bulk-copy';bulkCopyLabel.prepend(bulkCopy);
  $('import-submit').before(bulkCopyLabel);
  $('import-form').closest('.panel').querySelector('h2').textContent='Add several links';
  $('import-form').querySelector('p').textContent='Paste YouTube or TikTok links, one per line. They are added to Videos, where you can edit every detail. Copies appear in the queue below.';
  $('import-urls').closest('label').firstChild.textContent='Video links';
  $('import-submit').textContent='Add these links';
  $('import-form').addEventListener('submit',async event=>{
    event.preventDefault(); const urls=[...new Set($('import-urls').value.split(/\r?\n/).map(s=>s.trim()).filter(Boolean))];
    if(!urls.length) return notice('Paste at least one video link.',true);
    if(state.importBusy)return;
    state.importBusy=true; updateControls();
    try { const result=await api('/api/admin/manual',{urls,keep_copy:bulkCopy.checked}); await loadQueue(true);await loadCatalog();notice(reportResults(result,'Videos saved. Edit their details in Videos; requested copies are queued below.'),result.results?.some(r=>r.backup?.status==='failed')); }
    catch(error) { notice(error.message,true); } finally { state.importBusy=false;updateControls(); }
  });
  function queueField(label,value,type,id) { const wrapper=node('label',label); const input=node('input'); input.type='text'; input.value=value; input.id=id; wrapper.htmlFor=id; input.dataset.field=type; wrapper.append(input); return {wrapper,input}; }
  function renderQueue(items) {
    const container=$('queue'),present=new Set();
    if(!items.length) { const empty=node('div',undefined,'empty'); empty.append(node('div','[ + ]','mono'),node('h3','No imports yet'),node('p','Paste TikTok links to start a review queue.','muted')); container.replaceChildren(empty); state.queue.clear(); return; }
    container.querySelectorAll('.empty,.skeleton').forEach(el=>el.remove());
    for(const item of items) {
      const key=String(item.id); present.add(key); let record=state.queue.get(key);
      if(!record) {
        const el=node('article',undefined,'queue-card'),head=node('div',undefined,'entry-top'),title=node('h3'),badge=node('span',undefined,'badge'),url=node('p',undefined,'queue-url muted'),reason=node('p',undefined,'reason help'),preview=node('div'),controls=node('div',undefined,'actions'),form=node('form',undefined,'stack');
        const draft=state.drafts.get(key)||{title:item.title||'',tags:list(item.tags),add_end_card:item.add_end_card??true,use_provider_default:false,dirty:false};
        const titleField=queueField('Review title',draft.title,'title',`review-title-${key}`),tagField=queueField('Review hashtags',draft.tags,'tags',`review-tags-${key}`);
        titleField.input.required=true; const save=node('button','Save review'); save.type='submit'; const reviewStatus=node('p','Review title and tags before publishing.','help');
        const endCard=node('input'); endCard.type='checkbox'; endCard.id=`review-end-card-${key}`; endCard.checked=draft.add_end_card;
        const endLabel=node('label',undefined,'end-card-control'); endLabel.htmlFor=endCard.id; endLabel.append(endCard,document.createTextNode('Add end-card'));
        const providerDefault=node('input');providerDefault.type='checkbox';providerDefault.id=`review-provider-default-${key}`;providerDefault.checked=!!draft.use_provider_default;
        const providerLabel=node('label',undefined,'end-card-control');providerLabel.htmlFor=providerDefault.id;providerLabel.append(providerDefault,document.createTextNode('Use service default instead'));
        const endHelp=node('p',undefined,'help');
        form.append(titleField.wrapper,tagField.wrapper,endLabel,providerLabel,endHelp,save,reviewStatus); head.append(title,badge); el.append(head,url,reason,preview,form,controls);
        record={el,title,badge,url,reason,preview,controls,form,endCard,providerDefault,endHelp,titleInput:titleField.input,tagsInput:tagField.input,save,reviewStatus,draft,previewURL:'',busy:false,item};
        state.queue.set(key,record);
        endLabel.hidden=true;providerLabel.hidden=true;
        const markDirty=()=>{draft.title=titleField.input.value; draft.tags=tagField.input.value; draft.add_end_card=endCard.checked;draft.use_provider_default=providerDefault.checked;endCard.disabled=providerDefault.checked||requestLocked(record.item); draft.dirty=true; state.drafts.set(key,draft); reviewStatus.textContent='Unsaved review changes'; const publish=controls.querySelector('[data-publish]'); if(publish) publish.disabled=true; };
        titleField.input.addEventListener('input',markDirty); tagField.input.addEventListener('input',markDirty);
        endCard.addEventListener('change',markDirty);
        providerDefault.addEventListener('change',markDirty);
        form.addEventListener('submit',async e=>{e.preventDefault(); if(record.item.status==='imported'&&!titleField.input.value.trim()) return; const data={title:titleField.input.value.trim(),tags:tags(tagField.input.value),needs_title:!meaningfulTitle(titleField.input.value)}; if(!requestLocked(record.item)){if(record.item.kind==='tiktok')data.add_end_card=false;else if(providerDefault.checked)data.use_provider_default=true;else data.add_end_card=endCard.checked;} await queueMutation(record,'review',data,()=>{draft.dirty=false; draft.title=titleField.input.value.trim(); draft.tags=tagField.input.value; draft.add_end_card=endCard.checked;draft.use_provider_default=providerDefault.checked; reviewStatus.textContent='Review saved.';});});
        container.append(el);
      }
      record.item=item; record.title.textContent=displayTitle(item); record.badge.textContent=plainStatus(item.status); record.url.textContent=item.url||''; record.reason.textContent=item.reason||'';
      const locked=requestLocked(item);
      if(!record.draft.dirty && !record.busy) { record.titleInput.value=item.title||''; record.tagsInput.value=list(item.tags); record.endCard.checked=item.add_end_card??true; }
      if(locked) {record.endCard.checked=item.add_end_card===true;record.draft.add_end_card=item.add_end_card;record.providerDefault.checked=item.add_end_card==null;}
      record.providerDefault.disabled=locked||record.busy;
      record.endCard.disabled=locked||record.busy||record.providerDefault.checked;
      record.endHelp.textContent=locked&&item.add_end_card!==false&&item.add_end_card!==0?'Previously submitted request: it may include an end card. Its request is preserved.':'No end card will be added to this TikTok import.';
      renderPreview(record,item);
      const imported=item.status==='imported'; const published=item.status==='published'; record.form.hidden=published||item.status==='duplicate skipped';
      record.titleInput.required=imported;
      record.titleInput.disabled=record.busy||(!imported&&locked);record.tagsInput.disabled=record.busy||(!imported&&locked);
      record.save.disabled=record.busy||(!imported&&locked);
      record.reviewStatus.textContent=record.draft.dirty?'You have unsaved review changes.':imported?(needsTitle(item)?'This video needs a useful title before you publish.':'Review the title and tags before publishing.'):locked?'Import in progress. You can review it when it finishes.':'You can save a title and tags while this import waits.';
      const controlsSignature=JSON.stringify([item.status,record.busy,record.draft.dirty,item.can_keep_silent]);
      if(record.controlsSignature===controlsSignature)continue;
      record.controlsSignature=controlsSignature;record.controls.replaceChildren();
      const retry=action('Retry import',()=>queueMutation(record,'retry',{}),record.busy);
      if(['failed','blocked','waiting for connection'].includes(item.status)) record.controls.append(retry);
      if(item.status==='problem') {
        const again=action('Download again',()=>queueMutation(record,'redownload',{}),record.busy); again.className='primary'; record.controls.append(again);
        if(item.can_keep_silent) record.controls.append(action('Keep without sound',async()=>{ if(await confirmAction('Keep this video without sound?','Only do this if the original post really has no sound. It will be checked again and can then be published.','Keep without sound')) await queueMutation(record,'keepsilent',{}); },record.busy));
      }
      if(imported) { const publish=action('Publish to collection',async()=>{ if(await confirmAction('Publish this import?','This adds the imported video to your public catalogue with the saved review title and hashtags.','Publish video')) await queueMutation(record,'publish',{}); },record.busy||record.draft.dirty); publish.className='primary'; publish.dataset.publish='true'; record.controls.append(publish); }
      if(published) record.controls.append(node('span','Published to collection','help'));
    }
    for(const [key,record] of state.queue) if(!present.has(key)) {record.el.remove();state.queue.delete(key);state.drafts.delete(key);}
  }
  const PREVIEW_LABEL={processing:'Still processing',missing:'File missing',unsupported:'Format not supported',failed:'Preview could not be prepared',connection:'Connection problem'};
  function previewMessage(kind,reason) { return `${PREVIEW_LABEL[kind]||'Preview unavailable'}: ${reason||'No further details were reported.'}`; }
  function renderPreview(record,item) {
    const url=safeURL(item.preview_url), st=item.preview_state||'processing';
    const signature=JSON.stringify([url,st,item.preview_reason||'',item.poster_url||'',record.previewNonce||0]);
    if(signature===record.previewSignature) return;
    record.previewSignature=signature; record.preview.replaceChildren(); record.preview.className='queue-preview';
    if(!url) return;
    const poster=safeURL(item.poster_url), status=node('p','', 'help preview-status'), retry=node('button','Retry preview','preview-retry');
    status.setAttribute('role','status'); retry.type='button'; retry.hidden=true;
    retry.addEventListener('click',()=>retryPreview(record,retry,status));
    const show=(kind,reason)=>{ status.textContent=previewMessage(kind,reason); status.hidden=false; retry.hidden=kind==='processing'; };
    if(st==='ready') {
      const video=node('video'); video.controls=true; video.preload='metadata'; video.playsInline=true; video.setAttribute('playsinline',''); video.muted=false; video.defaultMuted=false;
      if(poster) video.poster=poster;
      video.src=url+(record.previewNonce?`?r=${record.previewNonce}`:'');
      video.addEventListener('error',async()=>{
        // The player can't say why it failed, so ask the Pi what it knows about this file.
        try { const r=await fetch(url+'/status',{credentials:'same-origin',cache:'no-store'}); const body=await r.json().catch(()=>({}));
          if(r.status===401) show('connection','Your sign-in has expired. Sign in again to watch the preview.');
          else if(!r.ok) show('connection',body.error||`The Pi answered with an error (${r.status}).`);
          else if(body.state==='ready') show('connection','The video could not be loaded from the Pi. Check your connection and retry the preview.');
          else show(body.state,body.reason);
        } catch { show('connection','Cannot reach the Pi to load the video. Check your connection and retry the preview.'); }
      });
      record.preview.append(video);
      status.hidden=true;
    } else {
      if(poster) { const img=node('img'); img.src=poster; img.alt='Poster frame of this import'; img.className='preview-poster'; record.preview.append(img); }
      show(st,item.preview_reason);
    }
    record.preview.append(status,retry);
  }
  async function retryPreview(record,button,status) {
    if(button.disabled) return;
    button.disabled=true; button.setAttribute('aria-busy','true'); button.textContent='Retrying…';
    status.hidden=false; status.textContent='Checking the saved file and preparing the preview. This can take a few minutes for long videos.';
    try {
      const result=await api(`/api/admin/queue/${encodeURIComponent(record.item.id)}/preview`,{});
      record.previewNonce=Date.now();
      Object.assign(record.item,{preview_state:result.preview?.state,preview_reason:result.preview?.reason});
      renderPreview(record,record.item);
      await loadQueue(true);
    } catch(error) {
      button.disabled=false; button.removeAttribute('aria-busy'); button.textContent='Retry preview';
      status.textContent=previewMessage('connection',error.message);
    }
  }
  function requestLocked(item) { return item.request_locked===true || item.payload!=null || item.upstream_id!=null; }
  async function queueMutation(record,operation,data,onSuccess) {
    if(record.busy) return; record.busy=true; record.el.querySelectorAll('button,input').forEach(b=>b.disabled=true);
    try { await api(`/api/admin/queue/${encodeURIComponent(record.item.id)}/${operation}`,data); if(onSuccess) onSuccess(); notice(operation==='publish'?'Import published.':operation==='review'?'Review saved.':operation==='redownload'?'Downloading again. It will be checked before it can be published.':operation==='keepsilent'?'Kept without sound. Checking it plays.':'Retry requested.'); if(operation==='publish') await loadCatalog(); }
    catch(error) { notice(error.message,true); } finally { record.busy=false;record.controlsSignature=null; await loadQueue(true); record.save.disabled=requestLocked(record.item)&&record.item.status!=='imported';record.titleInput.disabled=record.save.disabled;record.tagsInput.disabled=record.save.disabled;record.providerDefault.disabled=requestLocked(record.item);record.endCard.disabled=requestLocked(record.item)||record.providerDefault.checked; }
  }
  async function loadSync(){
    if(!state.signedIn)return;
    try{
      const data=await api('/api/admin/sync');
      $('sync-state').textContent=plainStatus(data.status)+(data.reason?' — '+data.reason:'');
      $('sync-last').textContent=data.last_success?'Last successful sync: '+new Date(data.last_success*1000).toLocaleString():'No successful sync yet.';
      state.sync=data; renderHome();
      state.syncSeen=true;state.syncLast=data.last_success;state.syncError=data.status==='failed'?data.reason:null;
    }catch(error){state.sync=null;$('sync-state').textContent=error.message;renderHome();}
  }
  $('sync-now').addEventListener('click',async()=>{
    try{const result=await api('/api/admin/sync',{});notice(result.status==='not set up'?'GitHub sync is not set up on the Pi.':'Sync requested. Checking GitHub shortly.');await loadSync();}
    catch(error){notice(error.message,true);}
  });
  function plainStatus(value) {
    const labels={problem:'Problem',imported:'Ready to publish',published:'Published',queued:'Waiting to start',downloading:'Downloading',processing:'Preparing video',submitted:'Preparing video',failed:'Needs help',blocked:'Needs setup','waiting for connection':'Waiting for connection',done:'Finished',paused:'Paused',running:'Importing',synced:'Up to date',pending:'Waiting to sync',off:'Off',watching:'Watching for posts',unsupported:'Not available yet',unavailable:'Unavailable',ready:'Ready',ok:'Ready','not set up':'Not set up','duplicate skipped':'Already in your collection'};
    return labels[value]||String(value||'Not checked yet').replace(/[_-]/g,' ').replace(/^./,s=>s.toUpperCase());
  }
  function isActive(item) {return !['imported','published','failed','problem','blocked','waiting for connection','duplicate skipped','unsupported','skipped','matched','complete','completed'].includes(item.status);}
  function filterQueue(){
    const filter=$('queue-filter').value;let visible=0;
    for(const record of state.queue.values()){
      const item=record.item;
      record.el.hidden=filter==='review'?item.status!=='imported':filter==='failed'?!['failed','blocked','waiting for connection','problem'].includes(item.status):filter==='active'?!isActive(item):false;
      if(!record.el.hidden)visible++;
    }
    let empty=$('queue-filter-empty');
    if(!empty){empty=node('div',undefined,'empty');empty.id='queue-filter-empty';empty.append(node('h3','Nothing in this view'),node('p','Choose another view to see your other imports.','help'));$('queue').after(empty);}
    empty.hidden=!state.items?.length||visible>0;
  }
  $('queue-filter').addEventListener('change',filterQueue);
  function goToReview(filter='') {
    openSection('add');$('queue-filter').value=filter;filterQueue();
    $('review-panel').scrollIntoView({behavior:'instant',block:'start'});
  }
  let overviewLoading=false,homeSignature='';
  async function loadOverview(){
    if(!state.signedIn||overviewLoading)return;
    overviewLoading=true;
    try{const result=await api('/api/admin/overview');if(state.signedIn){state.overview=result;state.overviewError='';}}
    catch(error){state.overview=null;state.overviewError=error.message;}
    finally{overviewLoading=false;renderHome();}
  }
  function renderHome(){
    if(!state.signedIn)return;
    const overview=state.overview,items=state.items||[],w=state.watcher,m=w?.mirror,sync=state.sync,q=state.queueData;
    const signature=JSON.stringify([overview,state.overviewError,items.map(i=>[i.id,i.title,i.status,i.reason]),w?.status,w?.enabled,w?.error,w?.back_catalog?.run,m,sync,q?.storage_ready,q?.connection_configured,state.catalog?.videos.map(v=>[v.id,v.needs_title,v.title])]);
    if(signature===homeSignature)return;homeSignature=signature;
    const attention=$('attention');attention.replaceChildren();
    const waiting=overview?.waiting_publish??(q?items.filter(i=>i.status==='imported').length:null);
    const titles=overview?.needs_title??(state.catalog?state.catalog.videos.filter(needsTitle).length:null);
    const failed=overview?.failed_imports??(q?items.filter(i=>i.status==='failed').length:null);
    const setup=q?[!q.connection_configured,!q.storage_ready,!m?.configured,sync?.status==='not set up'].filter(Boolean).length:null;
    function attentionItem(count,title,detail,handler){
      if(count===0)return;
      const button=action('',handler);button.className='attention-row';
      const copy=node('span',title);copy.append(node('small',detail));button.append(node('strong',count??'—'),copy);attention.append(button);
    }
    attentionItem(waiting,'Awaiting publish','Review imported videos before they go public.',()=>goToReview('review'));
    attentionItem(titles,'Need a title','Give these videos a name you will recognise.',()=>{openSection('videos');$('video-filter').value='title';renderEntries();});
    if(overview?.queue_needs_title)attentionItem(overview.queue_needs_title,'Imports need a title','Name these imports during review.',()=>goToReview('review'));
    attentionItem(overview?.problem_videos??null,'Videos with a problem','No sound, damaged or incomplete copies. Download them again.',()=>{openSection('videos');$('checks-panel').scrollIntoView({block:'start'});loadChecks();});
    attentionItem(failed,'Imports need help','Check the reason and try again when ready.',()=>goToReview('failed'));
    attentionItem(setup,'Setup to finish','Check import connection, storage, NAS and sync.',()=>openSection('settings'));
    if(!attention.childElementCount)attention.append(node('h3','Nothing needs you right now.'),node('p','Your current imports and collection have no outstanding tasks.','help'));
    if(state.overviewError)attention.append(node('p','Some checks could not be loaded. Refresh to try again.','help'));
    const active=$('active-progress');active.replaceChildren();
    const activeItems=items.filter(isActive),activeCount=overview?.active_imports??activeItems.length,run=w?.back_catalog?.run;
    if(run&&!['done','paused'].includes(run.state)){const row=node('div',undefined,'progress-item');row.append(node('h3','Importing older TikTok posts'),node('p',`${run.scanned||0} posts checked so far.`,'help'),action('View progress',()=>openSection('automatic')));active.append(row);}
    for(const item of activeItems.slice(0,3)){const row=node('div',undefined,'progress-item');row.append(node('h3',displayTitle(item)),node('p',plainStatus(item.status),'help'),node('div',undefined,'progress-track'));active.append(row);}
    if(activeCount>activeItems.slice(0,3).length)active.append(node('p',`${activeCount} imports are currently in progress.`,'help'));
    if(!active.childElementCount)active.append(node('h3',q?'No imports in progress.':'Import progress is unavailable.'),node('p',q?'Add links when you find something worth keeping.':'Use Refresh to check again.','help'));
    if(activeCount)active.append(action('View imports',()=>goToReview('active')));
    const health=$('home-health');health.replaceChildren();
    function healthRow(label,tone,text,button,handler){
      const row=node('div',undefined,'health-row'),dot=node('span',undefined,`health-dot ${tone}`);dot.setAttribute('aria-label',tone==='ok'?'Ready':tone==='error'?'Needs help':'Not ready');
      row.append(dot,node('strong',label),node('p',text),action(button,handler));health.append(row);
    }
    healthRow('Pi',overview?.pi?.state==='ok'?'ok':overview?.pi?.state==='error'?'error':'warn',overview?.pi?.message||'Not checked.','Check',()=>loadOverview());
    const mirrorOK=m?.configured&&m.state==='available'&&!m.error&&m.last_check&&m.count>=m.total;
    healthRow('NAS',m?.error?'error':mirrorOK?'ok':'warn',!m?'Not checked.':!m.configured?'No second-copy folder chosen. Set MIRROR_DIR on the Pi.':m.error?'Cannot reach the copy folder. Check the NAS mount.':`${m.count??0} of ${m.total??0} copies checked.`,'Details',()=>{openSection('settings');$('nas-panel').scrollIntoView({block:'start'});});
    healthRow('Sync',sync?.status==='synced'?'ok':sync?.status==='failed'?'error':'warn',sync?.status==='not set up'?'GitHub is not connected. Add its sync settings on the Pi.':sync?plainStatus(sync.status):'Not checked.','Details',()=>{openSection('settings');$('sync-panel').scrollIntoView({block:'start'});});
    healthRow('TikTok',w?.error?'error':w?.enabled&&w?.status==='watching'?'ok':'warn',q&&!q.connection_configured?'Import key missing. Set YOU2MP3_API_KEY on the Pi.':!w?'Not checked.':w.error?w.error:w.enabled?'Watching is on.':'Watching is off.','Options',()=>openSection('automatic'));
    healthRow('YouTube','warn',overview?.youtube?.message||'Watching is not available yet.','Details',()=>openSection('automatic'));
  }
  $('home-refresh').addEventListener('click',async()=>{await Promise.allSettled([loadCatalog(),loadQueue(true),loadWatcher(true),loadSync(),loadOverview(),loadChecks()]);});
  let checksTimer=null,checksSignature='';
  function renderChecks(data){
    const signature=JSON.stringify(data);if(signature===checksSignature)return;checksSignature=signature;
    const problems=data.problems||[],waiting=problems.filter(p=>!p.redownloading);
    $('checks-badge').textContent=data.running?'Checking':problems.length?`${problems.length} need help`:'All good';
    const when=data.run?.finished?' Last full check finished '+new Date(data.run.finished*1000).toLocaleString()+'.':'';
    $('checks-status').textContent=data.total===0?'No saved copies yet.':data.running?`Checking ${data.checked} of ${data.total} saved videos. Long videos take a few minutes each on the Pi.`:problems.length?`${problems.length} of ${data.total} saved videos have a problem.${when}`:`All ${data.total} saved videos have working picture and sound.${when}`;
    $('checks-run').disabled=data.running;
    $('checks-redownload').hidden=!waiting.length;$('checks-redownload').textContent=waiting.length>1?`Download all ${waiting.length} again`:'Download it again';
    const list=$('checks-list');list.replaceChildren();
    for(const p of problems){
      const row=node('article',undefined,'check-row'),head=node('div',undefined,'entry-top');
      head.append(node('h3',p.title||'Untitled'),node('span',p.redownloading?'Downloading again':p.published?'Published':'Not published','badge'));
      row.append(head,node('p',p.reason,'help'));
      if(p.published&&!p.redownloading)row.append(node('p','Still public with the current copy until a checked one replaces it.','help'));
      const buttons=node('div',undefined,'actions');
      if(p.job_id&&!p.redownloading)buttons.append(action('Download again',()=>redownload({job_ids:[p.job_id]})));
      if(p.job_id&&p.can_keep_silent)buttons.append(action('Keep without sound',async()=>{if(await confirmAction('Keep this video without sound?','Only do this if the original post really has no sound.','Keep without sound')){try{await api(`/api/admin/queue/${encodeURIComponent(p.job_id)}/keepsilent`,{});notice('Kept without sound. Checking it plays.');await loadChecks();}catch(error){notice(error.message,true);}}}));
      if(buttons.childElementCount)row.append(buttons);
      list.append(row);
    }
  }
  async function loadChecks(){
    if(!state.signedIn)return;
    clearTimeout(checksTimer);checksTimer=null;
    try{const data=await api('/api/admin/checks');renderChecks(data);if(data.running)checksTimer=setTimeout(loadChecks,15000);}
    catch(error){$('checks-status').textContent='Checks could not be loaded: '+error.message;}
  }
  async function redownload(body){
    try{const result=await api('/api/admin/checks/redownload',body);notice(result.started?`Downloading ${result.started} ${result.started===1?'video':'videos'} again. Each is checked before it replaces the saved copy.`:'Nothing to download again.');renderChecks(result);await loadQueue(true);}
    catch(error){notice(error.message,true);}
  }
  $('checks-run').addEventListener('click',async()=>{if(!await confirmAction('Check all saved videos?','Every saved copy is played through and its sound is checked, one at a time. This can take a while on the Pi; you can keep using admin.','Start checking'))return;try{renderChecks(await api('/api/admin/checks/run',{}));notice('Checking all saved videos.');loadChecks();}catch(error){notice(error.message,true);}});
  $('checks-redownload').addEventListener('click',async()=>{if(await confirmAction('Download these videos again?','Each is downloaded again and checked. Published videos stay public with their current copy until a checked one replaces it.','Download again'))await redownload({all:true});});
  document.addEventListener('focusin',updateViewport);
  document.addEventListener('focusout',()=>setTimeout(updateViewport,100));
  session();
})();
