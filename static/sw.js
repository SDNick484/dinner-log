// Service worker: needed for Android "install app" eligibility, and the receiving end of the
// manifest's share_target.
//
// Pages: network-first so the log is always current; falls back to cache when offline. Only
// successful same-origin GET responses are cached, so an error page or an authenticating proxy's
// login redirect is never replayed offline. POST responses are never cached.
//
// Shares: Android posts the shared files to SHARE_PATH. Answering that POST here, without the
// network, means a share can't be lost to an expired login session (the proxy would redirect it to
// its login page and drop the body). The files go into a one-time stash in SHARE_CACHE and the app
// opens /new?shared=<key>, whose form loads the files and deletes the stash. If stashing fails, the
// POST goes to the server, whose /share route saves the photos itself.
const CACHE = 'dinner-log-v3';
const SHARE_CACHE = 'dinner-log-share';   // not versioned: a deploy mustn't drop a pending share
const SHARE_PATH = '/share';
const SHARE_MAX_AGE_MS = 24 * 60 * 60 * 1000;

self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', e => e.waitUntil(
  caches.keys()
    .then(keys => Promise.all(keys.filter(k => k !== CACHE && k !== SHARE_CACHE).map(k => caches.delete(k))))
    .then(sweepShares)
    .catch(() => {})
    .then(() => self.clients.claim())
));

// Stash layout in SHARE_CACHE, keyed under /__share/<key>/ (never requested from the network):
//   file/<i>  one entry per file; its Content-Type is the file's, X-Share-Name the URI-encoded name
//   meta      JSON {created, title, text, url, files}; written last, so a stash without it is incomplete
// Every entry carries X-Share-Created (ms since epoch), which the sweep goes by.
function stashUrl(key, part) {
  return new URL(`/__share/${key}/${part}`, self.location.origin).href;
}

async function sweepShares() {
  const cache = await caches.open(SHARE_CACHE);
  const cutoff = Date.now() - SHARE_MAX_AGE_MS;
  for (const req of await cache.keys()) {
    const res = await cache.match(req);
    const created = Number(res && res.headers.get('X-Share-Created'));
    if (!(created > cutoff)) await cache.delete(req);
  }
}

async function deleteStash(cache, key) {
  const prefix = stashUrl(key, '');
  const reqs = await cache.keys();
  await Promise.all(reqs.filter(r => r.url.startsWith(prefix)).map(r => cache.delete(r)));
}

const field = v => (typeof v === 'string' ? v : '');

async function stashShare(request) {
  const form = await request.formData();
  const files = form.getAll('photos').filter(f => f instanceof File && f.size > 0);
  const key = self.crypto.randomUUID();
  const created = String(Date.now());
  const cache = await caches.open(SHARE_CACHE);
  try {
    await Promise.all(files.map((f, i) => cache.put(stashUrl(key, 'file/' + i), new Response(f, {
      headers: {
        'Content-Type': f.type || 'application/octet-stream',
        'X-Share-Name': encodeURIComponent(f.name || `shared-${i + 1}`),
        'X-Share-Created': created,
      },
    }))));
    const meta = {
      created: Number(created),
      title: field(form.get('title')), text: field(form.get('text')), url: field(form.get('url')),
      files: files.length,
    };
    await cache.put(stashUrl(key, 'meta'), new Response(JSON.stringify(meta), {
      headers: {'Content-Type': 'application/json', 'X-Share-Created': created},
    }));
  } catch (err) {
    await deleteStash(cache, key).catch(() => {});   // never leave half a stash behind
    throw err;
  }
  return key;
}

async function handleShare(e) {
  const fallback = e.request.clone();   // the body can only be read once
  try {
    const key = await stashShare(e.request);
    e.waitUntil(sweepShares().catch(() => {}));
    return Response.redirect(new URL('/new?shared=' + key, self.location.origin).href, 303);
  } catch (err) {
    return fetch(fallback);   // quota, a malformed body...: the server's /share saves the photos itself
  }
}

self.addEventListener('fetch', e => {
  const req = e.request;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;
  if (req.method === 'POST' && url.pathname === SHARE_PATH) {
    e.respondWith(handleShare(e));
    return;
  }
  if (req.method !== 'GET') return;
  const keep = !url.searchParams.has('shared');   // one-off share pages aren't worth caching
  e.respondWith(
    fetch(req)
      .then(res => {
        if (keep && res.ok && res.type === 'basic' && !res.redirected) {
          const copy = res.clone();
          caches.open(CACHE).then(c => c.put(req, copy));
        }
        return res;
      })
      .catch(() => caches.match(req))
  );
});
