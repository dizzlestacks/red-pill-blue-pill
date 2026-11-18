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
    player.src = video.backup_url; player.controls = true; player.playsInline = true;
    player.autoplay = true; player.preload = 'metadata';
    player.style.cssText = 'width:100%;height:100%;object-fit:contain;background:#000';
    container.append(player);
    container.removeAttribute('role'); container.removeAttribute('tabindex');
    if (message) container.setAttribute('aria-label', message);
    player.addEventListener('error', () => {
      const error = document.createElement('p'); error.textContent = 'Backup storage is unavailable. Please try again later.';
      container.append(error);
    });
  };
  window.matrixPlayYouTubeWithBackup = (container, video) => {
    let settled = false, player, timer;
    const fallback = () => {
      if (settled || !container.isConnected) return;
      settled = true; clearTimeout(timer);
      try { player?.destroy(); } catch {}
      window.matrixPlayBackup(container, video, 'YouTube unavailable; playing the saved backup');
    };
    container.replaceChildren();
    const mount = document.createElement('div'); container.append(mount);
    container.cancelYouTubeBackup = () => {
      settled = true; clearTimeout(timer);
      try { player?.destroy(); } catch {}
    };
    // Covers blocked API/iframe hosts; never uses iframe load as proof of playback.
    timer = setTimeout(fallback, 18000);
    window.matrixYouTubeReady().then(() => {
      if (settled || !container.isConnected) return;
      player = new YT.Player(mount, {
        videoId: video.youtube_id,
        playerVars: {autoplay: 1, playsinline: 1, origin: location.origin},
        events: {
          onReady: () => { clearTimeout(timer); },
          onError: fallback,
          onStateChange: event => {
            if (event.data === 1) clearTimeout(timer);
          }
        }
      });
    }).catch(fallback);
  };
})();
