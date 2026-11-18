'use strict';
(() => {
  const $ = id => document.getElementById(id);
  const themes = ['dark','light','grey','pink','blue','vaporwave','solarized-dark','dracula','cyberpunk','sepia','midnight','facebook','mcdonalds','walmart','office','discord','netflix','apple','google','amazon','spotify','twitch','youtube','terminal','desert','ocean','forest','blood','gold','ice'];
  const state = { csrf:'', signedIn:false, catalog:null, revision:null, editing:null, stale:false, busy:false, queueBusy:false, poll:null, queue:new Map(), drafts:new Map(), connection:false, storage:false };
  let authGeneration = 0;
  for (const name of themes) { const option = document.createElement('option'); option.value = name; option.textContent = name.split('-').map(s => s[0].toUpperCase()+s.slice(1)).join(' '); $('theme').append(option); }
  let savedTheme = 'terminal';
  try { savedTheme = localStorage.getItem('admin:theme') || localStorage.getItem('editor:theme') || 'terminal'; } catch {}
  $('theme').value = themes.includes(savedTheme) ? savedTheme : 'terminal';
  document.body.className = $('theme').value;
  $('theme').addEventListener('change', () => { document.body.className = $('theme').value; try { localStorage.setItem('admin:theme',$('theme').value); } catch {} });
  function notice(message, error=false) { $('message').textContent=message; $('message').classList.toggle('error',error); $('message').classList.toggle('success',!!message&&!error&&message!=='Working…'); $('message').setAttribute('role',error?'alert':'status'); $('message').setAttribute('aria-live',error?'assertive':'polite'); }
  let pressedButton=null;
  document.addEventListener('click',event=>{
    const button=event.target.closest('button');
    if(!button || button.disabled) return;
    pressedButton=button; button.setAttribute('aria-busy','true');
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
  function safeURL(value) { try { const u=new URL(value,location.origin); return ['http:','https:'].includes(u.protocol) ? u.href : ''; } catch { return ''; } }
  async function api(path, data) {
    const button=pressedButton, controller=new AbortController();
    const wasDisabled=button?.disabled;
    const timeout=setTimeout(()=>controller.abort(),30000);
    if(button){button.dataset.requestBusy='true';button.disabled=true;button.setAttribute('aria-busy','true');notice('Working…');}
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
      if(response.status===409) { state.stale=true; updateControls(); throw new Error('The catalogue changed in another session. Reload the catalogue before saving; nothing was overwritten.'); }
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
    authGeneration++; clearInterval(state.poll); state.poll=null; state.signedIn=false; state.csrf=''; state.catalog=null; state.revision=null; state.queue.clear(); state.drafts.clear(); state.editing=null; state.stale=false; state.connection=false; state.storage=false;
    $('console').hidden=true; $('logout').hidden=true; $('login').hidden=false; $('entries').replaceChildren(); $('queue').replaceChildren(); $('raw').value=''; $('import-urls').value=''; $('video-form').reset(); resetForm(); $('password').value=''; $('password').disabled=false; $('login-submit').disabled=false; $('login-submit').textContent='Sign in to console'; $('login-status').textContent='Your session is signed out.'; updateControls();
  }
  async function signedIn(csrf) {
    state.csrf=csrf; state.signedIn=true; $('login').hidden=true; $('console').hidden=false; $('logout').hidden=false; $('password').value=''; notice('');
    await Promise.allSettled([loadCatalog(),loadQueue()]);
    clearInterval(state.poll); if(state.signedIn) state.poll=setInterval(()=>loadQueue(true),5000);
  }
  async function session() {
    $('session-retry').hidden=true; $('login-submit').disabled=true; $('password').disabled=true; $('login-submit').textContent='Checking access…';
    try {
      const result=await api('/api/admin/session');
      document.querySelector('.release').textContent='Release '+(result.release||'2026.11.15.4');
      if(!result.configured) { $('login-submit').textContent='Admin password not configured'; $('login-status').textContent='Admin access is disabled. Set ADMIN_PASSWORD in Secrets, then retry. No login or saves are permitted until it is configured.'; $('session-retry').hidden=false; return; }
      if(result.authenticated) return signedIn(result.csrf);
      $('password').disabled=false; $('login-submit').disabled=false; $('login-submit').textContent='Sign in to console'; $('login-status').textContent='Password-protected owner access. No public registration.';
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
  document.querySelectorAll('[data-tab]').forEach(button=>button.addEventListener('click',()=>{
    document.querySelectorAll('[data-tab]').forEach(tab=>{ const selected=tab===button; tab.setAttribute('aria-selected',String(selected)); $(tab.dataset.tab).hidden=!selected; });
    notice(button.textContent.trim()+' opened.');
  }));
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
      if(!result.catalog || !Array.isArray(result.catalog.videos)) throw new Error('The catalogue response is missing its videos array.');
      state.catalog=result.catalog; state.revision=result.revision; state.stale=false;
      $('total-count').textContent=String(result.total ?? result.catalog.videos.length);
      const backed=result.backed_up ?? result.catalog.videos.filter(v=>v.backup_available===true).length;
      $('backup-count').textContent=String(backed); $('missing-count').textContent=String(Math.max(0,(result.total ?? result.catalog.videos.length)-backed));
      renderEntries(); updateControls();
      const opts=new Set(state.catalog.videos.flatMap(v=>Array.isArray(v.category)?v.category:categories(v.category??''))); $('category-options').replaceChildren(...[...opts].sort().map(c=>{ const o=node('option'); o.value=c; return o; }));
      return true;
    } catch(error) { notice(error.message,true); if(!state.catalog) { const box=node('div',undefined,'empty'); box.append(node('h3','Collection unavailable'),node('p','Your videos have not been loaded.','muted'),action('Retry',loadCatalog)); $('entries').replaceChildren(box); } return false; }
  }
  $('reload').addEventListener('click',async()=>{ if(await confirmAction('Reload catalogue?','This loads the latest revision. Unsaved raw JSON and form inputs are kept, but check your edits against any changes before saving.','Reload')) { if(await loadCatalog()) notice('Latest catalogue loaded. Check any unsaved edits before saving.'); } });
  function resetForm() { state.editing=null; $('video-form').reset(); $('form-heading').textContent='Add a video'; $('form-mode').textContent='New entry'; $('save-video').textContent='Save video'; $('reset-video').textContent='Clear form'; }
  $('reset-video').addEventListener('click',()=>{resetForm();notice('Form cleared.');});
  function editVideo(v,index) {
    state.editing={index,original:v}; $('source').value=v.youtube_id ? `https://www.youtube.com/watch?v=${v.youtube_id}` : v.url||''; $('title').value=v.title||''; $('categories').value=list(v.category); $('tags').value=list(v.tags); $('clip-start').value=v.start??v.clip_start??''; $('clip-end').value=v.end??v.clip_end??''; $('notes').value=v.notes??''; $('featured').checked=!!v.featured; $('pinned').checked=!!v.pinned;
    $('form-heading').textContent='Edit video'; $('form-mode').textContent='Editing'; $('save-video').textContent='Save changes'; $('reset-video').textContent='Cancel edit'; $('video-form').scrollIntoView({behavior:'smooth',block:'start'}); $('title').focus({preventScroll:true});
    notice('Editing '+(v.title||'video')+'.');
  }
  function renderEntries() {
    if(!state.catalog) return;
    const query=$('search').value.toLowerCase().trim(); const frag=document.createDocumentFragment(); let count=0;
    state.catalog.videos.forEach((v,index)=>{
      if(query && ![v.title,list(v.category),list(v.tags),v.url,v.youtube_id,v.notes].join(' ').toLowerCase().includes(query)) return;
      count++; const row=node('article',undefined,'entry'); const top=node('div',undefined,'entry-top'); const heading=node('div'); heading.append(node('h3',v.title||'Untitled video')); const bs=node('div',undefined,'badges'); if(v.pinned) bs.append(node('span','Pinned','badge')); if(v.featured) bs.append(node('span','Featured','badge')); heading.append(bs); top.append(heading,node('span',v.backup_available===true?'Backed up':'No backup','badge')); row.append(top,node('p',[list(v.category),list(v.tags)].filter(Boolean).join(' / ')||'No categories or tags','meta'));
      const buttons=node('div',undefined,'actions'); const locked=state.busy||state.stale;
      buttons.append(action('Edit',()=>editVideo(v,index),locked),action(v.pinned?'Unpin':'Pin',()=>mutateCatalog(c=>{c.videos[index]={...c.videos[index],pinned:!v.pinned};},'Pin updated.'),locked),action('Back up',()=>backup({video_id:String(v.id)}),locked||!v.id||v.backup_available===true));
      if(v.backup_available===true && safeURL(v.backup_url)) { const link=node('a','View backup'); link.href=safeURL(v.backup_url); link.target='_blank'; link.rel='noopener noreferrer'; buttons.append(link); }
      const del=action('Delete',async()=>{ if(await confirmAction('Delete this video?',`“${v.title||'Untitled video'}” will be removed from the catalogue. This cannot be undone here.`,'Delete video')) await mutateCatalog(c=>{c.videos.splice(index,1);},'Video deleted.'); },locked); del.className='danger'; buttons.append(del); row.append(buttons); frag.append(row);
    });
    if(!count) { const empty=node('div',undefined,'empty'); empty.append(node('div','[ / ]','mono'),node('h3',query?'No matching videos':'Your collection starts here'),node('p',query?'Try another title, category, or hashtag.':'Add your first source link using the form.','muted')); frag.append(empty); }
    $('entries').replaceChildren(frag);
  }
  $('search').addEventListener('input',renderEntries);
  function sourceFields(value) {
    const raw=value.trim(); if(/^[\w-]{11}$/.test(raw)) return {youtube_id:raw};
    if (/^\/media\/(?:tt-\d+|yt-[\w-]+)$/.test(raw)) return {youtube_id:'',url:raw};
    const url=safeURL(raw); if(!url || !/^https?:\/\//i.test(raw)) throw new Error('Enter a valid HTTP or HTTPS source link.');
    const u=new URL(url); const host=u.hostname.toLowerCase().replace(/^www\./,''); let id='';
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
      const source=sourceFields($('source').value),start=seconds($('clip-start').value),end=seconds($('clip-end').value);
      if(start!==null && end!==null && end<=start) throw new Error('Clip end must be after clip start.');
      const editing=state.editing;
      if(editing && !state.catalog.videos.includes(editing.original)) throw new Error('The catalogue was refreshed since you opened this edit. Open the current video again to avoid replacing newer changes.');
      const video={...(editing?editing.original:{youtube_id:'',url:'',added:new Date().toISOString().slice(0,10)}),...source,title:$('title').value.trim(),category:categories($('categories').value),tags:tags($('tags').value),featured:$('featured').checked,pinned:$('pinned').checked,notes:$('notes').value};
      if(!video.title) throw new Error('Enter a title.');
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
      state.catalog=next; state.revision=result.revision; notice(success + (result.durable_state===false?' Primary catalogue saved, but the recovery snapshot failed. Check storage before updating the server.':''));
      await loadCatalog(); return true;
    } catch(error) { notice(error.message,true); return false; }
    finally { state.busy=false; updateControls(); renderEntries(); }
  }
  let pendingConfirm=null;
  function confirmAction(title,text,label='Confirm') {
    if(pendingConfirm) return Promise.resolve(false);
    $('confirm-title').textContent=title; $('confirm-text').textContent=text; $('confirm-accept').textContent=label; $('confirm-dialog').showModal(); $('confirm-cancel').focus();
    return new Promise(resolve=>{pendingConfirm=resolve;});
  }
  function finishConfirm(value) { $('confirm-dialog').close(); notice(value?'Confirmed. Working…':'Cancelled. Nothing changed.'); const callback=pendingConfirm; pendingConfirm=null; if(callback) callback(value); }
  $('confirm-cancel').addEventListener('click',()=>finishConfirm(false)); $('confirm-accept').addEventListener('click',()=>finishConfirm(true)); $('confirm-dialog').addEventListener('cancel',event=>{event.preventDefault();finishConfirm(false);});
  function reportResults(result,fallback) {
    const results=Array.isArray(result.results)?result.results:[];
    if(!results.length) return fallback;
    return results.map(r=>[r.url||r.video_id||r.id||'Item',r.status||(r.ok===false?'failed':r.ok===true?'accepted':''),r.reason||r.error||r.message||''].filter(Boolean).join(' — ')).join('; ');
  }
  async function backup(data) {
    if(state.busy) return;
    state.busy=true; updateControls(); renderEntries();
    try { const result=await api('/api/admin/backup',data); await Promise.all([loadCatalog(),loadQueue(true)]);notice(reportResults(result,'Backup request processed. Verified status is shown in the collection.')); }
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
      $('connection-status').textContent=state.connection?'Import connection is configured.':'Connection not set up: YOU2MP3_API_KEY is missing. You can queue links now; downloading waits for connection.';
      $('storage-status').textContent=(data.storage_message || (state.storage?'Media storage is ready.':'Permanent media storage is not ready. You can queue jobs; copying waits for storage.')) + (data.state_storage_message ? ' '+data.state_storage_message : '');
      renderQueue(data.items||[]); updateControls();
      if(!silent || state.queueRefreshRequested)notice('Queue status refreshed.');
    } catch(error) { $('connection-status').textContent=error.message; notice(error.message,true); }
    finally { state.queueBusy=false;if(state.queueRefreshRequested){state.queueRefreshRequested=false;$('queue-refresh').disabled=false;$('queue-refresh').removeAttribute('aria-busy');} }
  }
  $('queue-refresh').addEventListener('click',()=>loadQueue());
  $('import-form').addEventListener('submit',async event=>{
    event.preventDefault(); const urls=[...new Set($('import-urls').value.split(/\r?\n/).map(s=>s.trim()).filter(Boolean))];
    if(!urls.length) return notice('Paste at least one TikTok link.',true);
    if(urls.some(value=>{try {const u=new URL(value); return !['http:','https:'].includes(u.protocol) || !(u.hostname==='tiktok.com'||u.hostname.endsWith('.tiktok.com'));} catch{return true;}})) return notice('Enter only valid TikTok URLs, one per line.',true);
    if(state.importBusy)return;
    state.importBusy=true; updateControls();
    try { const result=await api('/api/admin/queue',{urls}); await loadQueue(true);notice(reportResults(result,'Import request processed. Check queue status below.')); }
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
        const draft=state.drafts.get(key)||{title:item.title||'',tags:list(item.tags),dirty:false};
        const titleField=queueField('Review title',draft.title,'title',`review-title-${key}`),tagField=queueField('Review hashtags',draft.tags,'tags',`review-tags-${key}`);
        titleField.input.required=true; const save=node('button','Save review'); save.type='submit'; const reviewStatus=node('p','Review title and tags before publishing.','help');
        form.append(titleField.wrapper,tagField.wrapper,save,reviewStatus); head.append(title,badge); el.append(head,url,reason,preview,form,controls);
        record={el,title,badge,url,reason,preview,controls,form,titleInput:titleField.input,tagsInput:tagField.input,save,reviewStatus,draft,previewURL:'',busy:false,item};
        state.queue.set(key,record);
        const markDirty=()=>{draft.title=titleField.input.value; draft.tags=tagField.input.value; draft.dirty=true; state.drafts.set(key,draft); reviewStatus.textContent='Unsaved review changes'; const publish=controls.querySelector('[data-publish]'); if(publish) publish.disabled=true; };
        titleField.input.addEventListener('input',markDirty); tagField.input.addEventListener('input',markDirty);
        form.addEventListener('submit',async e=>{e.preventDefault(); if(!titleField.input.value.trim()) return; await queueMutation(record,'review',{title:titleField.input.value.trim(),tags:tags(tagField.input.value)},()=>{draft.dirty=false; draft.title=titleField.input.value.trim(); draft.tags=tagField.input.value; reviewStatus.textContent='Review saved. Ready to publish when the import is complete.';});});
        container.append(el);
      }
      record.item=item; record.title.textContent=item.title||'TikTok import'; record.badge.textContent=item.status||'Pending'; record.url.textContent=item.url||''; record.reason.textContent=item.reason||'';
      if(!record.draft.dirty && !record.busy) { record.titleInput.value=item.title||''; record.tagsInput.value=list(item.tags); }
      const previewURL=safeURL(item.preview_url);
      if(previewURL!==record.previewURL) { record.preview.replaceChildren(); record.previewURL=previewURL; if(previewURL) { const video=node('video'); video.controls=true; video.preload='metadata'; video.playsInline=true; video.src=previewURL; record.preview.append(video); video.addEventListener('error',()=>{ if(!record.preview.querySelector('p')) record.preview.append(node('p','Media preview unavailable. Retry the import if the file could not be retrieved.','help')); }); } }
      const imported=item.status==='imported'; const published=item.status==='published'; record.form.hidden=!imported;
      record.save.disabled=record.busy;
      const controlsSignature=JSON.stringify([item.status,record.busy,record.draft.dirty]);
      if(record.controlsSignature===controlsSignature)continue;
      record.controlsSignature=controlsSignature;record.controls.replaceChildren();
      const retry=action('Retry import',()=>queueMutation(record,'retry',{}),record.busy);
      if(['failed','blocked','waiting for connection'].includes(item.status)) record.controls.append(retry);
      if(imported) { const publish=action('Publish to collection',async()=>{ if(await confirmAction('Publish this import?','This adds the imported video to your public catalogue with the saved review title and hashtags.','Publish video')) await queueMutation(record,'publish',{}); },record.busy||record.draft.dirty); publish.className='primary'; publish.dataset.publish='true'; record.controls.append(publish); }
      if(published) record.controls.append(node('span','Published to collection','help'));
    }
    for(const [key,record] of state.queue) if(!present.has(key)) {record.el.remove();state.queue.delete(key);state.drafts.delete(key);}
  }
  async function queueMutation(record,operation,data,onSuccess) {
    if(record.busy) return; record.busy=true; record.el.querySelectorAll('button').forEach(b=>b.disabled=true);
    try { await api(`/api/admin/queue/${encodeURIComponent(record.item.id)}/${operation}`,data); if(onSuccess) onSuccess(); notice(operation==='publish'?'Import published.':operation==='review'?'Review saved.':'Retry requested.'); if(operation==='publish') await loadCatalog(); }
    catch(error) { notice(error.message,true); } finally { record.busy=false;record.controlsSignature=null; await loadQueue(true); record.save.disabled=false; }
  }
  session();
})();
