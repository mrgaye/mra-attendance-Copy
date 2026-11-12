const CACHE_VERSION = "mra-attendance-v1";

self.addEventListener("install", (event) => {
    self.skipWaiting();
});

self.addEventListener("activate", (event) => {
    event.waitUntil(
        (async () => {
            const cacheNames = await caches.keys();
            await Promise.all(
                cacheNames.map((name) => caches.delete(name))
            );
            await self.clients.claim();
        })()
    );
});

self.addEventListener("fetch", (event) => {
    // Network-only: never cache authenticated pages,
    // employee information, GPS data, or attendance selfies.
    return;
});
