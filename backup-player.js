/* Only activated on the Video Hub for entries with verified local backup routes. */
(() => {
  let ready;
  window.matrixYouTubeReady = () => {
    if (window.YT?.Player) return Promise.resolve();
    if (!ready) ready = new Promise((resolve, reject) => {
      const previous = window.onYouTubeIframeAPIReady;
      window.onYouTubeIframeAPIReady = () => { previous?.(); resolve(); };
      const script = document.createElement('script');
      script.src = 'https://www.youtube.com/iframe_api';
      script.onerror = () => reject(new Error('YouTube player unavailable'));
      document.head.append(script);
    });
    return ready;
  };
  window.matrixPlayBackup = (container, video, message = '') => {
    container.cancelYouTubeBackup?.();
    container.cancelYouTubeBackup = null;
    container.dataset.loaded = '1';
    container.replaceChildren();
    const player = document.createElement('video');
    player.src = video.backup_url; if (video.poster_url) player.poster = video.poster_url; player.controls = true; player.playsInline = true;
    player.autoplay = true; player.preload = 'metadata';
    player.style.cssText = 'width:100%;height:100%;object-fit:contain;background:#000';
    container.append(player);
    container.removeAttribute('role'); container.removeAttribute('tabindex');
    if (message) container.setAttribute('aria-label', message);
    let timer,settled=false;
    const cleanup=()=>{clearTimeout(timer);player.pause();player.removeAttribute('src');player.load();};
    const fallback=()=>{
      if(settled||!container.isConnected)return;
      settled=true;cleanup();
      if(video.youtube_id){
        container.replaceChildren();
        const iframe=document.createElement('iframe');
        iframe.src='https://www.youtube.com/embed/'+encodeURIComponent(video.youtube_id)+'?autoplay=1&playsinline=1';
        iframe.title=video.title||'Video';iframe.allow='autoplay; encrypted-media; picture-in-picture';
        iframe.allowFullscreen=true;iframe.style.cssText='width:100%;height:100%;border:0';
        container.append(iframe);
        container.cancelYouTubeBackup=()=>{iframe.src='about:blank';iframe.remove();};
      }else{
        container.replaceChildren();
        const error=document.createElement('p');error.textContent='This video is temporarily unavailable.';
        const retry=document.createElement('button');retry.textContent='Try again';
        retry.onclick=()=>window.matrixPlayBackup(container,video);
        container.append(error,retry);
      }
    };
    container.cancelYouTubeBackup=()=>{settled=true;cleanup();};
    // A blocked autoplay is not a broken file: retain native tap-to-play controls.
    timer=setTimeout(()=>{if(player.readyState<2)fallback();},10000);
    player.addEventListener('loadeddata',()=>clearTimeout(timer));
    player.addEventListener('playing',()=>clearTimeout(timer));
    player.addEventListener('stalled',()=>{clearTimeout(timer);if(!player.paused)timer=setTimeout(fallback,10000);});
    player.addEventListener('error',fallback);
  };
  window.matrixPlayYouTubeWithBackup = (container, video) => {
    // Keep the existing entry point, but the owned file is now primary.
    // YouTube is created only if the owned source fails.
    window.matrixPlayBackup(container,video);
  };
})();
