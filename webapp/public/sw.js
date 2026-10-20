// Keeps Beru stored on the glasses so it opens instantly, even on a slow connection.
// The page comes from the stored copy at once and is refreshed in the background, so a new
// version shows on the next launch. API calls and the mascot videos always go to the network.
const CACHE = 'beru-shell-v2';
const SHELL_URL = '/beru/';

self.addEventListener('install', (event) => {
  // Store the page together with the files it loads, so the stored copy is always complete.
  event.waitUntil(refreshShell(new Request(SHELL_URL)).catch(() => {}));
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil((async () => {
    for (const key of await caches.keys()) if (key !== CACHE) await caches.delete(key);
    await self.clients.claim();
  })());
});

// Fetch the latest page, store it, and pre-store the files it needs; drop files it no longer uses.
async function refreshShell(request) {
  const response = await fetch(request, { cache: 'no-store' });
  if (!response.ok) return response;
  const cache = await caches.open(CACHE);
  const html = await response.clone().text();
  const assets = [...new Set(html.match(/\/assets\/[^"'\s>]+/g) || [])];
  await Promise.all(assets.map(async (url) => {
    if (!(await cache.match(url))) {
      const r = await fetch(url);
      if (r.ok) await cache.put(url, r);
    }
  }));
  for (const old of await cache.keys()) {
    const path = new URL(old.url).pathname;
    if (path.startsWith('/assets/') && !assets.includes(path)) await cache.delete(old);
  }
  await cache.put(request, response.clone());
  return response;
}

self.addEventListener('fetch', (event) => {
  const { request } = event;
  if (request.method !== 'GET') return;
  const url = new URL(request.url);
  if (url.origin !== location.origin || url.pathname.startsWith('/api/') || url.pathname.endsWith('.mp4')) return;

  if (request.mode === 'navigate') {
    event.respondWith((async () => {
      const cached = await caches.match(request, { ignoreSearch: true });
      const fresh = refreshShell(request);
      if (cached) {
        event.waitUntil(fresh.catch(() => {}));
        return cached;
      }
      return fresh;
    })());
    return;
  }

  if (/^\/(assets|icons|fonts|mascot)\//.test(url.pathname)) {
    // Fingerprinted or rarely changing files: stored copy first.
    event.respondWith((async () => {
      const cached = await caches.match(request);
      if (cached) return cached;
      const response = await fetch(request);
      if (response.ok && url.pathname.startsWith('/assets/')) {
        const copy = response.clone();
        event.waitUntil(caches.open(CACHE).then((c) => c.put(request, copy)));
      }
      return response;
    })());
  }
});
