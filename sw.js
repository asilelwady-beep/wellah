const SHELL='wallaha-shell-v7-polished-launch';
const ASSETS=['/customer','/driver','/admin','/icon-192.png','/icon-512.png'];
self.addEventListener('install',event=>event.waitUntil(caches.open(SHELL).then(cache=>cache.addAll(ASSETS)).then(()=>self.skipWaiting())));
self.addEventListener('activate',event=>event.waitUntil(caches.keys().then(keys=>Promise.all(keys.filter(key=>key!==SHELL).map(key=>caches.delete(key)))).then(()=>self.clients.claim())));
self.addEventListener('fetch',event=>{
 const request=event.request;
 if(request.method!=='GET'||request.url.includes('/api/'))return;
 const url=new URL(request.url);
 if(url.origin!==self.location.origin)return;
 if(!ASSETS.includes(url.pathname))return;
 event.respondWith(fetch(request).then(response=>{if(response.ok){const copy=response.clone();caches.open(SHELL).then(cache=>cache.put(request,copy))}return response}).catch(()=>caches.match(request)));
});
