const SHELL='wallaha-shell-v98-checkout-discount-20261029';
const ASSETS=['/','/customer','/driver','/admin','/maps.js','/leaflet.js','/leaflet.css','/features.js','/icon.svg','/icon-192.png','/icon-512.png'];
const PRODUCT_ASSET=/^\/(?:product-photo-[0-9]+\.jpg|product-illustration\/[0-9]+\.svg)$/;
self.addEventListener('install',event=>{
 event.waitUntil((async()=>{
  const cache=await caches.open(SHELL);
  await Promise.all(ASSETS.map(async path=>{
   try{const response=await fetch(path,{cache:'reload'});if(response.ok)await cache.put(path,response)}catch(e){}
  }));
  await self.skipWaiting();
 })());
});
self.addEventListener('activate',event=>event.waitUntil(caches.keys().then(keys=>Promise.all(keys.filter(key=>key!==SHELL).map(key=>caches.delete(key)))).then(()=>self.clients.claim())));
self.addEventListener('fetch',event=>{
 const request=event.request;
 if(request.method!=='GET'||request.url.includes('/api/'))return;
 const url=new URL(request.url);
 if(url.origin!==self.location.origin||(!ASSETS.includes(url.pathname)&&!PRODUCT_ASSET.test(url.pathname)))return;
 const versionedScript=url.searchParams.has('v')&&/\.js$/.test(url.pathname);
 const update=caches.open(SHELL).then(cache=>{
  const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),10000);
  return fetch(request,{signal:controller.signal,cache:'no-cache'}).then(async response=>{
   if(response.ok){
    await cache.put(versionedScript?request:new Request(url.origin+url.pathname),response.clone());
    if(PRODUCT_ASSET.test(url.pathname)){
     const keys=await cache.keys(),images=keys.filter(key=>PRODUCT_ASSET.test(new URL(key.url).pathname));
     for(const old of images.slice(0,Math.max(0,images.length-180)))await cache.delete(old);
    }
   }
   return response;
  }).catch(()=>null).finally(()=>clearTimeout(timer));
 });
 event.waitUntil(update.then(()=>undefined));
 event.respondWith((async()=>{
  const cached=await caches.match(request,{ignoreSearch:!versionedScript});
  if(versionedScript&&cached)return cached;
  return (request.mode==='navigate'||/\.(?:js|css)$/.test(url.pathname))?(await update||cached||Response.error()):(cached||await update||Response.error());
 })());
});
