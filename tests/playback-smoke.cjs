const {chromium}=require('playwright'),assert=require('node:assert/strict'),{execFileSync}=require('node:child_process'),path=require('node:path');
(async()=>{
 const movie=execFileSync('ffmpeg',['-loglevel','error','-f','lavfi','-i','color=c=black:s=160x90:d=2','-an','-c:v','libx264','-pix_fmt','yuv420p','-movflags','frag_keyframe+empty_moov','-f','mp4','pipe:1']);
 const b=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/repl/tools/bin/chromium',args:['--no-sandbox']});
 try{
  for(const fail of [false,true]){
   const p=await b.newPage({viewport:{width:390,height:844}}),requests=[];
   await p.route('https://files.test/**',r=>{requests.push('owned');return fail?r.abort():r.fulfill({contentType:'video/mp4',body:movie});});
   await p.route('https://www.youtube.com/**',r=>{requests.push('youtube');return r.abort();});
   await p.setContent('<div id="player" style="width:350px;height:200px"></div>');
   await p.addScriptTag({path:path.resolve('backup-player.js')});
   await p.evaluate(()=>matrixPlayYouTubeWithBackup(document.querySelector('#player'),{backup_url:'https://files.test/video.mp4',youtube_id:'abcdefghijk',title:'Test'}));
   if(fail)await p.waitForSelector('iframe');
   else await p.waitForFunction(()=>document.querySelector('video')?.readyState>=2);
   assert.equal(requests[0],'owned');assert.equal(requests.includes('youtube'),fail);
   await p.evaluate(()=>document.querySelector('#player').cancelYouTubeBackup());
   await p.close();
  }
  console.log('PASS owned media first, no YouTube request when playable, YouTube fallback on failure, teardown');
 }finally{await b.close();}
})().catch(e=>{console.error(e);process.exit(1)});
