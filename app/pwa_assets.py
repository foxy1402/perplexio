import json


def manifest_json() -> str:
    manifest = {
        "name": "Perplexio",
        "short_name": "Perplexio",
        "start_url": "/",
        "scope": "/",
        "display": "standalone",
        "background_color": "#07090d",
        "theme_color": "#07090d",
        "description": "Search-grounded assistant with web, social, and file intelligence.",
        "icons": [
            {
                "src": "/icons/icon.png",
                "sizes": "512x512",
                "type": "image/png",
                "purpose": "any",
            },
            {
                "src": "/icons/maskable.png",
                "sizes": "512x512",
                "type": "image/png",
                "purpose": "maskable",
            },
        ],
    }
    return json.dumps(manifest, ensure_ascii=True)


def icon_svg(maskable: bool = False) -> str:
    if maskable:
        return """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512">
<rect width="512" height="512" fill="#07090d"/>
<circle cx="256" cy="256" r="170" fill="#0e9f7c"/>
<circle cx="256" cy="256" r="122" fill="#111722"/>
<path d="M190 302V206h42c23 0 38 10 38 30 0 11-6 20-16 25 14 4 22 15 22 30 0 22-16 35-43 35h-43zm24-56h15c12 0 18-5 18-14 0-9-7-13-19-13h-14v27zm0 40h18c13 0 20-5 20-15s-7-15-20-15h-18v30z" fill="#eaf0ff"/>
</svg>"""
    return """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512">
<rect width="512" height="512" rx="96" fill="#07090d"/>
<circle cx="256" cy="256" r="160" fill="#16c79a"/>
<circle cx="256" cy="256" r="116" fill="#111722"/>
<path d="M190 302V206h42c23 0 38 10 38 30 0 11-6 20-16 25 14 4 22 15 22 30 0 22-16 35-43 35h-43zm24-56h15c12 0 18-5 18-14 0-9-7-13-19-13h-14v27zm0 40h18c13 0 20-5 20-15s-7-15-20-15h-18v30z" fill="#eaf0ff"/>
</svg>"""


def service_worker_js() -> str:
    return """
const CACHE_NAME = "perplexio-pwa-v2";
const APP_SHELL = ["/", "/manifest.webmanifest", "/icons/icon.png", "/icons/maskable.png"];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(APP_SHELL)).then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  if (event.request.method !== "GET") return;
  const reqUrl = new URL(event.request.url);
  const sameOrigin = reqUrl.origin === self.location.origin;
  const isPrivateApi = reqUrl.pathname.startsWith("/api/") || reqUrl.pathname.startsWith("/auth/");
  if (!sameOrigin || isPrivateApi) {
    event.respondWith(fetch(event.request));
    return;
  }
  event.respondWith(
    fetch(event.request)
      .then((resp) => {
        if (APP_SHELL.includes(reqUrl.pathname)) {
          const copy = resp.clone();
          caches.open(CACHE_NAME).then((cache) => cache.put(event.request, copy));
        }
        return resp;
      })
      .catch(() => caches.match(event.request).then((cached) => cached || caches.match("/")))
  );
});
""".strip()
