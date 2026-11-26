// Browser check, at iPhone size, that every published video shows in the hub
// whatever its source: own copy only, YouTube only, or both. Covers the Pi hub
// and the lite site with the Pi reachable and unreachable.
//   NODE_PATH=<node_modules> node tests/hub-sources.cjs
const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { execFileSync } = require('child_process');
const { chromium, devices } = require('playwright');

const ROOT = path.resolve(__dirname, '..');
const PI = 'https://pi.example';
function sampleVideo() {
  const file = path.join(fs.mkdtempSync(path.join(os.tmpdir(), 'hub-mp4-')), 'clip.mp4');
  execFileSync('ffmpeg', ['-y', '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=size=320x568:rate=25:duration=2',
    '-f', 'lavfi', '-i', 'sine=frequency=440:duration=2', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac',
    '-movflags', '+faststart', '-shortest', file]);
  return fs.readFileSync(file);
}
const MP4 = sampleVideo();
const POSTER = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=', 'base64');

// The Pi's raw catalogue, as public_catalog/admin_store would serve it.
const piCatalog = {
  popup: { enabled: true, message: 'Welcome' },
  videos: [
    { id: 'own', title: 'tester', added: '2024-01-02', published: '2026-11-20T10:00:00Z', category: ['Imports'], tags: ['gathering'],
      source_url: 'https://www.tiktok.com/@owner/video/7300000000000000001', url: '/media/tt-7300000000000000001',
      backup_available: true, backup_url: '/media/tt-7300000000000000001', poster_url: '/media/tt-7300000000000000001/poster' },
    { id: 'yt', title: 'YouTube only', added: '2025-06-01', category: ['Teaching'], youtube_id: 'abcdefghijk' },
    { id: 'both', title: 'Both sources', added: '2025-01-01', category: ['Teaching'], youtube_id: 'bcdefghijkl',
      backup_available: true, backup_url: '/media/yt-bcdefghijkl', poster_url: '/media/yt-bcdefghijkl/poster' },
    { id: 'pl', title: 'A playlist', added: '2025-01-01', youtube_id: 'PLabcdefghijklmnop' }
  ]
};

function liteSite() {
  const out = fs.mkdtempSync(path.join(os.tmpdir(), 'hub-lite-'));
  const catalog = JSON.parse(JSON.stringify(piCatalog));
  catalog.public_pi_url = PI;
  catalog.videos[0].tiktok_url = catalog.videos[0].source_url;
  fs.writeFileSync(path.join(out, 'catalog.json'), JSON.stringify(catalog));
  execFileSync('python3', ['-I', 'lite_site.py', '--catalog', path.join(out, 'catalog.json'), '--output', path.join(out, 'site')],
    { cwd: ROOT, env: { ...process.env, PI_PUBLIC_URL: '' } });
  return path.join(out, 'site');
}

async function open(browser, dir, { piUp, rawCatalog }) {
  const ctx = await browser.newContext({ ...devices['iPhone 13'], serviceWorkers: 'block' });
  await ctx.addInitScript(() => { try { localStorage.setItem('popupShown', 'true'); } catch {} });
  const page = await ctx.newPage();
  const opened = [];
  await page.exposeFunction('__opened', u => opened.push(u));
  await page.addInitScript(() => { window.open = u => { window.__opened(String(u)); return null; }; });
  await page.route('**/*', route => {
    const url = new URL(route.request().url());
    if (url.hostname === 'img.youtube.com' || url.hostname === 'fonts.googleapis.com' || url.hostname === 'fonts.gstatic.com')
      return route.fulfill({ status: 200, contentType: 'image/png', body: POSTER });
    if (url.origin === PI || (rawCatalog && url.pathname.startsWith('/media/'))) {
      if (!piUp) return route.abort();
      if (url.pathname === '/api/public/backups')
        return route.fulfill({ json: { backups: { own: PI + '/media/tt-7300000000000000001', both: PI + '/media/yt-bcdefghijkl' },
          posters: { own: PI + '/media/tt-7300000000000000001/poster' } } });
      if (url.pathname.endsWith('/poster')) return route.fulfill({ status: 200, contentType: 'image/png', body: POSTER });
      return route.fulfill({ status: 200, contentType: 'video/mp4', body: MP4 });
    }
    if (url.hostname !== 'hub.test') return route.abort();
    if (rawCatalog && url.pathname === '/videos.json') return route.fulfill({ json: rawCatalog });
    if (rawCatalog && url.pathname === '/healthz') return route.fulfill({ json: { ok: true } });
    const file = path.join(dir, url.pathname === '/' ? 'index.html' : decodeURIComponent(url.pathname));
    if (!file.startsWith(dir) || !fs.existsSync(file)) return route.fulfill({ status: 404, body: 'missing' });
    const type = file.endsWith('.html') ? 'text/html' : file.endsWith('.js') ? 'text/javascript' : file.endsWith('.css') ? 'text/css' : 'application/octet-stream';
    return route.fulfill({ status: 200, contentType: type, body: fs.readFileSync(file) });
  });
  await page.goto('http://hub.test/videos.html');
  await page.waitForFunction(() => document.querySelectorAll('#videoGrid .card').length >= 3, null, { timeout: 15000 });
  if (piUp && !rawCatalog) await page.waitForFunction(() => !!document.querySelector('#videoGrid img.own-poster'), null, { timeout: 8000 });
  return { ctx, page, opened };
}

const card = (page, title) => page.locator('#videoGrid .card', { has: page.locator('.card-title', { hasText: new RegExp('^' + title + '$') }) });

async function common(page, label) {
  // Every video (not the playlist) is listed, and the counter matches the grid.
  assert.equal(await page.locator('#videoGrid .card').count(), 3, label + ': three cards');
  assert.match(await page.locator('#resultCount').textContent(), /Showing 3 of 3/, label + ': counter matches cards');
  // Search by title, hashtag and category all find the own-copy video.
  for (const q of ['tester', '#gathering', 'imports']) {
    await page.fill('#searchInput', q); await page.waitForTimeout(250);
    assert.equal(await card(page, 'tester').count(), 1, `${label}: search ${q}`);
  }
  await page.fill('#searchInput', ''); await page.waitForTimeout(250);
  await page.selectOption('#categoryFilter', 'Imports'); await page.waitForTimeout(150);
  assert.equal(await page.locator('#videoGrid .card').count(), 1, label + ': category view');
  await page.selectOption('#categoryFilter', ''); await page.waitForTimeout(150);
  // New lists the newly published video first even though its post date is old.
  await page.click('#tab-new'); await page.waitForTimeout(200);
  assert.equal((await page.locator('#videoGrid .card-title').first().textContent()).trim(), 'tester', label + ': New tab order');
  await page.click('#tab-all'); await page.waitForTimeout(200);
}

(async () => {
  const browser = await chromium.launch({ executablePath: process.env.CHROMIUM || '/repl/tools/bin/chromium' });
  try {
    // Pi hub: own copy plays inline with sound and has its poster.
    let { ctx, page, opened } = await open(browser, ROOT, { piUp: true, rawCatalog: piCatalog });
    await common(page, 'pi');
    assert.equal(await card(page, 'tester').locator('img.own-poster').count(), 1, 'pi: poster thumbnail');
    await card(page, 'tester').locator('.thumb-wrap').click();
    await page.waitForSelector('#videoGrid video');
    assert.equal(await page.locator('#videoGrid video').evaluate(v => v.muted), false, 'pi: sound on');
    assert.equal(opened.length, 0, 'pi: plays in the hub, not a new window');
    await ctx.close();

    const site = liteSite();
    // Lite with the Pi reachable: same as the Pi.
    ({ ctx, page, opened } = await open(browser, site, { piUp: true }));
    await common(page, 'lite up');
    assert.equal(await card(page, 'tester').locator('img.own-poster').count(), 1, 'lite up: poster');
    await card(page, 'tester').locator('.thumb-wrap').click();
    await page.waitForSelector('#videoGrid video');
    assert.equal(opened.length, 0, 'lite up: no TikTok fallback window');
    assert.equal(await page.locator('#limited-mode:visible').count(), 0, 'lite: no Limited view banner');
    assert.equal(await page.locator('#release-marker').count(), 1, 'lite: release line');
    // Lucky plays inside the hub (one handler only, no new windows).
    await page.click('#luckyBtn'); await page.waitForTimeout(400);
    assert.equal(opened.length, 0, 'lucky: plays in the hub');
    await ctx.close();

    // Lite with the Pi down: still listed and clearly unavailable.
    ({ ctx, page, opened } = await open(browser, site, { piUp: false }));
    await common(page, 'lite down');
    const box = card(page, 'tester').locator('.unavailable-box');
    assert.equal(await box.count(), 1, 'lite down: unavailable box');
    assert.match(await box.textContent(), /Temporarily unavailable/);
    await card(page, 'tester').locator('.thumb-wrap').click(); await page.waitForTimeout(300);
    assert.equal(opened.length, 0, 'lite down: no silent TikTok redirect');
    assert.equal(await card(page, 'YouTube only').locator('img').count(), 1, 'lite down: YouTube still shows');
    await ctx.close();
    console.log('hub-sources: all checks passed');
  } finally {
    await browser.close();
  }
})().catch(e => { console.error(e); process.exit(1); });
