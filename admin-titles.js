// Review titles: each TikTok video's current title beside its TikTok caption.
// Nothing changes until the owner taps. Uses the shared helpers exposed by admin.js.
(()=>{
  'use strict';
  const A=window.WAWAdmin; if(!A) return;
  const $=id=>document.getElementById(id), node=A.node;
  let data=null, loading=false, picked=new Set(), editing=null, timer=null;

  function button(text, handler, cls){
    const b=node('button',text,cls); b.type='button';
    b.addEventListener('click',async()=>{ if(b.disabled)return; b.disabled=true; b.setAttribute('aria-busy','true');
      try{ await handler(); }catch(e){ A.notice(e.message,true); }finally{ b.disabled=false; b.removeAttribute('aria-busy'); } });
    return b;
  }
  const open=row=>!row.same&&row.decision!=='keep'&&!!row.suggestion;
  function shown(){
    const filter=$('titles-filter').value, rows=data?.items||[];
    return filter==='all'?rows:filter==='untitled'?rows.filter(r=>r.needs_title):rows.filter(open);
  }

  async function load(){
    if(!A.signedIn()||loading) return;
    loading=true;
    try{ data=await A.api('/api/admin/titles'); render(); }
    catch(e){ $('titles-status').textContent='Titles could not be loaded: '+e.message; }
    finally{ loading=false; }
    clearTimeout(timer); timer=null;
    if(data?.refresh?.running) timer=setTimeout(load,4000);
  }

  async function decide(action, ids, title){
    const body={action,ids}; if(title!==undefined) body.title=title;
    const result=await A.api('/api/admin/titles',body);
    data=result; ids.forEach(id=>picked.delete(id)); editing=null; render();
    if(action!=='keep'&&result.changed) await A.reloadCatalog();
    return result;
  }

  function quote(label, text, cls){
    const p=node('p',undefined,'title-line '+(cls||'')); p.append(node('span',label,'title-label'),node('span',text||'—','title-text')); return p;
  }

  function row(r){
    const el=node('article',undefined,'title-row'); el.dataset.id=r.id;
    if(picked.has(r.id)) el.classList.add('selected');
    const pick=node('label',undefined,'bulk-pick'), box=node('input'); box.type='checkbox'; box.checked=picked.has(r.id); box.disabled=!r.suggestion;
    box.setAttribute('aria-label','Select '+(r.title||'this video'));
    box.addEventListener('change',()=>{ if(box.checked) picked.add(r.id); else picked.delete(r.id); el.classList.toggle('selected',box.checked); counts(); });
    pick.append(box,node('span','Select'));
    const thumb=node('div',undefined,'entry-thumb'); thumb.setAttribute('aria-hidden','true');
    if(r.thumb){ const img=node('img'); img.src=r.thumb; img.alt=''; img.loading='lazy'; img.decoding='async'; img.addEventListener('error',()=>{img.remove();thumb.classList.add('empty');}); thumb.append(img); } else thumb.classList.add('empty');
    const body=node('div',undefined,'title-body');
    body.append(quote('Now',r.title,'now'));
    body.append(quote('TikTok caption',r.caption.trim()?r.caption:'(no caption)','caption'));
    if(r.origin==='cover') body.append(quote('Text on the cover · suggested',r.cover_text,'cover suggestion'));
    const badges=node('div',undefined,'badges');
    if(r.same) badges.append(node('span','Matches the caption','badge ok'));
    else if(r.origin==='cover') badges.append(node('span','Cover text · needs review','badge warn'));
    else if(!r.suggestion) badges.append(node('span','No caption words','badge'));
    if(r.decision==='keep') badges.append(node('span','Kept','badge'));
    if(r.needs_title) badges.append(node('span','Needs a title','badge'));
    body.append(badges);
    const actions=node('div',undefined,'actions');
    if(r.suggestion&&!r.same){
      const use=button(r.origin==='cover'?'Use cover text':'Use caption',()=>decide('use',[r.id]).then(()=>A.notice('Title updated from TikTok.')),'primary');
      use.setAttribute('aria-label',(r.origin==='cover'?'Use cover text':'Use caption')+' as the title: '+r.suggestion);
      actions.append(use);
    }
    if(!r.same&&r.decision!=='keep') actions.append(button('Keep current',()=>decide('keep',[r.id]).then(()=>A.notice('Kept the current title.'))));
    actions.append(button('Type a title',async()=>{ editing=r.id; render(); $('title-edit-'+r.id)?.focus(); }));
    if(r.tiktok_url){ const a=node('a','Open on TikTok'); a.href=r.tiktok_url; a.target='_blank'; a.rel='noopener noreferrer'; actions.append(a); }
    if(r.suggestion&&!r.same&&r.origin!=='cover') body.append(quote('Caption title',r.suggestion,'suggestion'));
    body.append(actions);
    if(editing===r.id){
      const form=node('form',undefined,'title-edit stack'), label=node('label','Your title'), input=node('input');
      input.id='title-edit-'+r.id; input.value=r.suggestion||r.title; input.maxLength=1000; input.required=true; label.htmlFor=input.id; label.append(input);
      const save=node('button','Save title','primary'); save.type='submit';
      const cancel=button('Cancel',async()=>{ editing=null; render(); });
      const row2=node('div',undefined,'actions'); row2.append(save,cancel);
      form.append(label,row2);
      form.addEventListener('submit',async e=>{ e.preventDefault(); const value=input.value.trim(); if(!value) return; save.disabled=true;
        try{ await decide('set',[r.id],value); A.notice('Title saved.'); }catch(err){ A.notice(err.message,true); }finally{ save.disabled=false; } });
      body.append(form);
    }
    el.append(pick,thumb,body);
    return el;
  }

  function counts(){
    const rows=shown(), sel=[...picked].filter(id=>rows.some(r=>r.id===id&&r.suggestion));
    $('titles-accept').disabled=!sel.length;
    $('titles-accept').textContent=sel.length?`Use caption for ${sel.length} selected`:'Use caption for selected';
  }

  function render(){
    if(!data) return;
    const rows=shown(), list=$('titles-list'), focus=document.activeElement?.id;
    const r=data.refresh||{};
    $('titles-badge').textContent=data.to_review?`${data.to_review} to review`:'All matched';
    $('titles-status').textContent=r.running?`Reading your TikTok post list… ${r.posts||0} posts so far.`:
      (r.error?r.error+' ':'')+(data.items.length?`${data.items.length} videos have a known TikTok caption. ${data.to_review} differ from their title.`:
      `No TikTok captions known yet (${data.known_posts} posts read). Tap “Read TikTok captions” to fetch your post list.`);
    $('titles-refresh').disabled=!!r.running;
    const t=data.thumbs||{};
    $('thumbs-status').textContent=t.running?'Making card pictures…':t.last_run?`Card pictures: ${t.done} made in the last run${t.missing?`, ${t.missing} still missing`:''}${t.failed?`, ${t.failed} couldn’t be fetched (tried again later)`:''}.`:'Card pictures are filled in automatically, a few at a time.';
    $('thumbs-backfill').disabled=!!t.running;
    const frag=document.createDocumentFragment();
    for(const item of rows) frag.append(row(item));
    if(!rows.length){ const empty=node('div',undefined,'empty'); empty.append(node('h3','Nothing to review here'),node('p',$('titles-filter').value==='review'?'Every title matches its TikTok caption or has been kept.':'No videos in this list.','muted')); frag.append(empty); }
    list.replaceChildren(frag);
    if(focus&&$(focus)) $(focus).focus({preventScroll:true});
    counts();
  }

  $('titles-filter').addEventListener('change',()=>{ picked.clear(); render(); });
  $('titles-select-all').addEventListener('click',()=>{ for(const r of shown()) if(r.suggestion&&!r.same) picked.add(r.id); render(); });
  $('titles-accept').addEventListener('click',async()=>{
    const rows=shown(), ids=[...picked].filter(id=>rows.some(r=>r.id===id&&r.suggestion));
    if(!ids.length) return;
    if(!await A.confirmAction('Use the TikTok caption for these titles?',`${ids.length} ${ids.length===1?'video':'videos'} will take the caption's words as the title, and its hashtags are added to tags. Cover-text suggestions are used where a caption has no words.`,'Use caption')) return;
    const b=$('titles-accept'); b.disabled=true;
    try{ const result=await decide('use',ids); A.notice(`Updated ${result.changed} ${result.changed===1?'title':'titles'}.`); }
    catch(e){ A.notice(e.message,true); } finally{ counts(); }
  });
  $('titles-refresh').addEventListener('click',async()=>{
    try{ await A.api('/api/admin/titles/refresh',{}); A.notice('Reading your TikTok post list. Captions appear here as they arrive.'); await load(); }
    catch(e){ A.notice(e.message,true); }
  });
  $('thumbs-backfill').addEventListener('click',async()=>{
    try{ await A.api('/api/admin/thumbs/backfill',{}); A.notice('Filling in missing card pictures. This runs in the background.'); setTimeout(load,3000); }
    catch(e){ A.notice(e.message,true); }
  });
  window.WAWTitles={load,clear(){data=null;picked.clear();$('titles-list').replaceChildren();}};
})();
