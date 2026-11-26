// Publish to YouTube panel. Uses the shared helpers exposed by admin.js.
(()=>{
  'use strict';
  const A=window.WAWAdmin; if(!A) return;
  const $=id=>document.getElementById(id), node=A.node;
  let data=null, dirty=false, loading=false, lastLoad=0, shown=new Set(['check']);
  const GROUPS=[['check','Needs your check'],['missing','Not on YouTube yet'],['on_youtube','Already on YouTube'],['failed','Couldn’t upload'],['skipped','Skipped']];
  const when=t=>t?new Date(t*1000).toLocaleString([], {weekday:'short',hour:'numeric',minute:'2-digit',month:'short',day:'numeric'}):'';
  const len=s=>s?`${Math.floor(s/60)}:${String(Math.round(s%60)).padStart(2,'0')}`:'';
  const local=t=>{if(!t)return '';const d=new Date(t*1000);d.setMinutes(d.getMinutes()-d.getTimezoneOffset());return d.toISOString().slice(0,16);};
  const ytLink=id=>{const a=node('a','Open on YouTube');a.href='https://www.youtube.com/watch?v='+encodeURIComponent(id);a.target='_blank';a.rel='noopener noreferrer';return a;};
  function button(text, handler, cls){const b=node('button',text,cls);b.type='button';b.addEventListener('click',async()=>{b.disabled=true;try{await handler();}catch(e){A.notice(e.message,true);}finally{b.disabled=false;}});return b;}
  async function post(path, body, message){data=await A.api('/api/admin/publish/'+path, body||{});render();if(message)A.notice(message);}

  async function load(force){
    if(!A.signedIn()||loading) return; if(!force&&Date.now()-lastLoad<4000) return;
    loading=true;
    try{data=await A.api('/api/admin/publish');lastLoad=Date.now();render();}
    catch(e){$('publish-connection').textContent=e.message;$('publish-badge').textContent='Problem';}
    finally{loading=false;}
  }
  function tick(){
    if(!data) return load(true);
    const busy=data.groups?.missing?.some(x=>x.state==='uploading')||data.scan?.status==='scanning'||data.scan?.status==='waiting';
    const visible=!$('automatic').hidden;
    if(visible&&(busy||Date.now()-lastLoad>30000)) load(true);
  }
  function clear(){data=null;dirty=false;$('publish-lists').replaceChildren();$('publish-actions').replaceChildren();}

  function badge(){
    if(!data.configured) return 'Setup needed';
    if(!data.connected) return 'Not connected';
    if(!data.right_channel) return 'Wrong channel';
    if(data.error) return 'Attention';
    if(data.paused) return 'Paused';
    if(data.pause_reason) return 'Waiting';
    if(data.started&&data.waiting) return 'Uploading';
    if(data.started) return 'Up to date';
    return 'Ready to review';
  }
  function renderConnection(){
    const box=$('publish-connection'); box.replaceChildren();
    $('publish-redirect').textContent=data.redirect_uri||('PI_PUBLIC_URL + '+data.callback_path);
    $('publish-connect').hidden=data.connected||!data.configured; $('publish-disconnect').hidden=!data.connected;
    $('publish-paste-box').hidden=data.connected||!data.configured; $('publish-form').hidden=!data.connected;
    if(!data.configured){box.append(node('p','Google sign-in isn’t set up on the Pi yet. Follow the Google setup steps below (about 10 minutes, once).'));return;}
    if(!data.connected){box.append(node('p','Not connected. Press Connect YouTube and sign in with Google as '+data.expected_handle+'. The Pi never sees your password; Google gives it a permission you can remove at any time.'));if(data.error)box.append(node('p',data.error,'help error'));return;}
    const ch=data.channel||{};
    const line=node('p'); line.append('Connected as ',node('strong',ch.handle||ch.title||'your channel')); if(ch.title&&ch.handle) line.append(' ('+ch.title+')'); box.append(line);
    if(!data.right_channel) box.append(node('p',`This isn’t ${data.expected_handle}. Nothing will be uploaded. Disconnect and sign in to the right channel.`,'help error'));
    const scan=data.scan; box.append(node('p',scan.status==='done'?`Channel checked ${when(scan.last)}: ${scan.videos} videos found.`:scan.status==='failed'?'Channel check failed: '+scan.error:'Reading your channel’s videos…','help'+(scan.status==='failed'?' error':'')));
    if(data.error) box.append(node('p',data.error,'help error'));
  }
  function renderSettings(){
    if(dirty) return;
    const s=data.settings, cat=$('publish-category');
    if(!cat.options.length) for(const [id,name] of Object.entries(data.categories).sort((a,b)=>a[1].localeCompare(b[1]))){const o=node('option',name);o.value=id;cat.append(o);}
    $('publish-visibility').value=s.visibility; cat.value=s.category; $('publish-kids').checked=s.made_for_kids; $('publish-paid').checked=s.paid_promotion;
    $('publish-per-day').value=s.per_day; $('publish-per-day').max=data.quota.insert_limit; $('publish-start-after').value=local(s.start_after);
    $('publish-end-card').checked=s.end_card; $('publish-auto').checked=s.auto_new; $('publish-audit').value=s.audit;
    $('publish-save').disabled=true; $('publish-save-status').textContent='';
  }
  function renderWarning(){
    const w=$('publish-warning'); w.replaceChildren(); w.hidden=!data.connected;
    if(w.hidden) return;
    w.append(node('h3','Before you start'));
    const audit={'not requested':'Your Google project hasn’t been audited. Since 2 November 2026 that no longer forces uploads to private: they get the visibility you choose. The audit only matters if you want more than 100 uploads a day for the project.',
      requested:'You asked Google for an audit. Uploads already keep the visibility you choose; the audit only raises the 100-a-day project limit once approved.',
      passed:'Your project passed Google’s audit. Use a higher daily number only if Google raised your limit.'}[data.settings.audit];
    for(const t of [audit,
      'YouTube can still make an upload private (for example a channel setting or a policy check). If that happens the video is marked here so you can look in YouTube Studio.',
      `Limits: at most ${data.quota.insert_limit} uploads a day for the project, and your channel has its own daily limit. The Pi uploads ${data.settings.per_day} a day, spread out, and carries on after midnight Pacific time.`,
      'Already-on-YouTube matches below are used as they are when you press Start. Check them first; you can change any of them later.']) w.append(node('p',t));
    if(data.private_results) w.append(node('p',`${data.private_results} upload${data.private_results===1?' was':'s were'} made private by YouTube.`,'error'));
  }
  function renderProgress(){
    const box=$('publish-progress'), q=data.quota; box.replaceChildren(); box.hidden=!data.connected;
    if(box.hidden) return;
    const c=data.counts;
    const stats=node('div','', 'publish-stats');
    for(const [n,label] of [[c.on_youtube,'on YouTube'],data.started?[data.waiting,'waiting']:[c.missing,'to upload'],[c.check,'to check'],[c.failed,'failed']]){const s=node('div','', 'publish-stat');s.append(node('strong',String(n||0)),node('span',label));stats.append(s);}
    box.append(stats);
    const meter=node('p',`Today: ${q.uploads_today} of ${Math.min(q.per_day,q.insert_limit)} uploads. Resets ${when(q.resets_at)}.`,'help'); box.append(meter);
    const flying=data.groups.missing.find(x=>x.state==='uploading');
    if(flying){const p=flying.progress, pct=p&&p.size?Math.floor(p.sent*100/p.size):0;
      const row=node('div','', 'publish-uploading'); row.append(node('p','Uploading: '+flying.title));
      const bar=node('progress'); bar.max=100; bar.value=pct; bar.setAttribute('aria-label','Upload progress'); row.append(bar);
      if(flying.reason) row.append(node('p',flying.reason,'help')); box.append(row);}
    else if(data.started){
      const why=data.paused?'Paused by you.':data.pause_reason||data.blocked||(data.waiting?(data.next_upload_at>Date.now()/1000?'Next upload '+when(data.next_upload_at)+'.':'Next upload shortly.'):'Everything is on YouTube. New imports '+(data.settings.auto_new?'upload automatically.':'wait for you here.'));
      box.append(node('p',why,'help'));
    }
  }
  function renderActions(){
    const box=$('publish-actions'); box.replaceChildren(); if(!data.connected) return;
    box.append(button('Check channel again',()=>post('scan',{},'Reading your channel again.')));
    if(!data.started){
      const start=button('Start uploading',async()=>{
        const n=data.counts.missing||0;
        if(!await A.confirmAction('Start uploading to YouTube?',`${n} video${n===1?'':'s'} will be uploaded to ${data.channel?.handle||'your channel'} as ${data.settings.visibility||'(choose who can see them first)'}, ${data.settings.per_day} a day. ${data.counts.on_youtube||0} already on YouTube will be linked to their hub entries. ${data.counts.check||0} that need your check stay waiting until you choose.`,'Start uploading'))return;
        await post('start',{},'Uploading has started.');},'primary');
      start.disabled=!data.right_channel||!data.settings.visibility||data.scan.status!=='done';
      box.append(start);
      if(start.disabled) box.append(node('p',!data.settings.visibility?'Choose who can see the uploads and save first.':data.scan.status!=='done'?'Start is available after the channel check finishes.':'Connect the right channel first.','help'));
    } else {
      box.append(button(data.paused?'Resume uploads':'Pause uploads',()=>post('pause',{paused:!data.paused},data.paused?'Uploads resumed.':'Uploads paused. An upload in progress continues from where it stopped when you resume.')));
      box.append(button('Stop',async()=>{if(await A.confirmAction('Stop uploading?','Nothing more is uploaded until you press Start again. Videos already on YouTube stay there.','Stop'))await post('stop',{},'Stopped.');}));
    }
  }
  function itemAction(id, action, extra, message){return post('item',{video_id:id,action,...extra},message);}
  function renderItem(x, group){
    const li=node('li','', 'publish-item');
    const head=node('div','', 'publish-item-head'); head.append(node('strong',x.title)); if(x.duration) head.append(node('span',len(x.duration),'muted')); li.append(head);
    if(x.youtube_id&&(group==='on_youtube'||x.state==='uploaded')){
      const p=node('p','', 'help'); p.append((x.state==='uploaded'?'Uploaded'+(x.privacy?' ('+x.privacy+')':''):x.kind==='auto'?'Matched by title and length':x.kind==='marker'?'Found by its upload marker':x.kind==='hub'?'Already linked on the site':'Matched by you')+(x.match_title&&x.state!=='uploaded'?': “'+x.match_title+'” '+len(x.match_duration):'')+' · ',ytLink(x.youtube_id)); li.append(p);
    }
    if(x.reason) li.append(node('p',x.reason,'help'+(group==='failed'?' error':'')));
    if(group==='missing'&&x.needs_title) li.append(node('p','Needs a title in Videos before it can upload.','help'));
    if(group==='missing'&&!x.has_copy) li.append(node('p','Waits for its saved copy to pass the picture and sound check.','help'));
    const actions=node('div','', 'actions');
    if(group==='check') for(const c of x.candidates){actions.append(button(`This is it: “${c.title}” ${len(c.duration)}`,()=>itemAction(x.id,'match',{youtube_id:c.id},'Matched. It won’t be uploaded again.')),ytLink(c.id));}
    if(['check','on_youtube'].includes(group)&&x.state!=='uploaded'){
      const sel=node('select'); sel.setAttribute('aria-label','Choose the YouTube video for '+x.title); sel.append(Object.assign(node('option','Pick a different video…'),{value:''}));
      for(const v of data.channel_videos) sel.append(Object.assign(node('option',v.title+(v.duration?' · '+len(v.duration):'')),{value:v.id}));
      sel.addEventListener('change',()=>{if(sel.value)itemAction(x.id,'match',{youtube_id:sel.value},'Match saved.').catch(e=>A.notice(e.message,true));});
      li.append(sel);
      actions.append(button('Not on YouTube — upload it',()=>itemAction(x.id,'upload',{},'It will be uploaded.')));
    }
    if(group==='missing'&&!x.approved&&x.state==='missing') {li.append(node('p','Waiting for you: new since you pressed Start.','help'));actions.append(button('Upload this one',()=>itemAction(x.id,'upload',{},'It will be uploaded.')));}
    if(group==='failed') actions.append(button('Try again',()=>itemAction(x.id,'upload',{},'It will be tried again.')));
    if(['check','missing','failed'].includes(group)&&x.state!=='uploading') actions.append(button('Skip',()=>itemAction(x.id,'skip',{},'Skipped. It won’t be uploaded.')));
    if(group==='skipped') actions.append(button('Undo skip',()=>itemAction(x.id,'reset',{},'Back in the list.')));
    if(actions.childElementCount) li.append(actions);
    return li;
  }
  function renderLists(){
    const box=$('publish-lists'); box.replaceChildren(); if(!data.connected||data.scan.status==='never') return;
    for(const [key,label] of GROUPS){
      const items=data.groups[key]||[]; if(!items.length&&key!=='missing') continue;
      const d=node('details','', 'publish-group'); d.open=shown.has(key); d.addEventListener('toggle',()=>d.open?shown.add(key):shown.delete(key));
      d.append(node('summary',`${label} (${items.length})`));
      const ul=node('ul','', 'publish-list');
      for(const x of items.slice(0,150)) ul.append(renderItem(x,key));
      if(items.length>150) ul.append(node('li',`And ${items.length-150} more.`,'help'));
      if(!items.length) ul.append(node('li','Nothing here.','help'));
      d.append(ul); box.append(d);
    }
  }
  function render(){
    if(!data) return;
    $('publish-badge').textContent=badge();
    renderConnection(); if(data.connected){renderSettings();} renderWarning(); renderProgress(); renderActions();
    if(!document.activeElement?.closest?.('#publish-lists')) renderLists();
  }

  $('publish-connect').addEventListener('click',async()=>{
    try{const r=await A.api('/api/admin/publish/connect',{origin:location.origin});location.assign(r.url);}catch(e){A.notice(e.message,true);}
  });
  $('publish-disconnect').addEventListener('click',async()=>{
    if(!await A.confirmAction('Disconnect YouTube?','The Pi forgets Google’s permission and asks Google to cancel it. Uploads stop. Videos already on YouTube stay there.','Disconnect'))return;
    try{await post('disconnect',{},'YouTube disconnected.');}catch(e){A.notice(e.message,true);}
  });
  $('publish-paste-send').addEventListener('click',async()=>{
    try{await post('callback',{url:$('publish-paste').value},'YouTube connected.');$('publish-paste').value='';}catch(e){A.notice(e.message,true);}
  });
  $('publish-form').addEventListener('input',()=>{dirty=true;$('publish-save').disabled=false;$('publish-save-status').textContent='Unsaved changes.';});
  $('publish-form').addEventListener('submit',async event=>{
    event.preventDefault();
    const start=$('publish-start-after').value, s={visibility:$('publish-visibility').value,category:$('publish-category').value,
      made_for_kids:$('publish-kids').checked,paid_promotion:$('publish-paid').checked,per_day:Number($('publish-per-day').value),
      start_after:start?Math.floor(new Date(start).getTime()/1000):0,end_card:$('publish-end-card').checked,auto_new:$('publish-auto').checked,audit:$('publish-audit').value};
    if(!s.visibility) return A.notice('Choose who can see the uploads.',true);
    try{dirty=false;await post('settings',{settings:s},'Upload settings saved.');}catch(e){dirty=true;A.notice(e.message,true);}
  });

  // Returning from Google's sign-in page.
  const q=new URLSearchParams(location.search);
  if(q.has('youtube')||q.has('youtube_error')){
    const err=q.get('youtube_error');
    history.replaceState(null,'',location.pathname+'#automatic');
    setTimeout(()=>{$('tab-automatic')?.click();A.notice(err||'YouTube connected. Reading your channel’s videos now.',!!err);},800);
  }
  window.WAWPublish={load:()=>load(true),tick,clear};
  if(A.signedIn()) load(true);
})();
