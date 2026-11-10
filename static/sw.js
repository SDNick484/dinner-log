// Minimal service worker: needed for Android "install app" eligibility.
// Network-first so the log is always current; falls back to cache when offline.
// Only successful same-origin responses are cached, so an error page or an
// authenticating proxy's login redirect is never replayed offline.
const CACHE = 'dinner-log-v2';

self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', e => e.waitUntil(
  caches.keys()
    .then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k))))
    .then(() => self.clients.claim())
));

self.addEventListener('fetch', e => {
  const req = e.request;
  const url = new URL(req.url);
  if (req.method !== 'GET' || url.origin !== self.location.origin) return;
  // Straight to the network: it carries notes and a new URL per typed name, and the cache below
  // would keep every one of them (it doesn't honour no-store).
  if (url.pathname === '/last-time') return;
  e.respondWith(
    fetch(req)
      .then(res => {
        if (res.ok && res.type === 'basic' && !res.redirected) {
          const copy = res.clone();
          caches.open(CACHE).then(c => c.put(req, copy));
        }
        return res;
      })
      .catch(() => caches.match(req))
  );
});
