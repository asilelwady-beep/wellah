const SHELL='wallaha-shell-v125-live-ui';
const ASSETS=['/','/customer','/driver','/admin','/maps.js','/leaflet.js','/leaflet.css','/features.js','/icon.svg','/icon-192.png','/icon-512.png'];
const PRODUCT_ASSET=/^\/(?:product-photo-[0-9]+\.jpg|product-illustration\/[0-9]+\.svg)$/;
self.addEventListener('install',event=>{
 event.waitUntil((async()=>{
  // A slow image or map must never keep an update waiting indefinitely.
  let timer;
  const controller=new AbortController();
  const preload=(async()=>{
   const response=await fetch('/customer',{cache:'reload',signal:controller.signal});
   if(response.ok)await (await caches.open(SHELL)).put('/customer',response);
  })().catch(()=>{});
  await Promise.race([preload,new Promise(resolve=>{timer=setTimeout(()=>{controller.abort();resolve()},8000)})]);
  clearTimeout(timer);
  // Update the network handler without reloading any open page or order.
  await self.skipWaiting();
 })());
});
self.addEventListener('activate',event=>event.waitUntil((async()=>{
 const keys=await caches.keys();
 const previous=keys.filter(key=>key.startsWith('wallaha-shell-')&&key!==SHELL);
 // Keep the preceding shell available to documents that are still open.
 await Promise.all(previous.slice(0,-1).map(key=>caches.delete(key)));
})()));
self.addEventListener('fetch',event=>{
 const request=event.request;
 if(request.method!=='GET'||request.url.includes('/api/'))return;
 const url=new URL(request.url);
 if(url.origin!==self.location.origin||(!ASSETS.includes(url.pathname)&&!PRODUCT_ASSET.test(url.pathname)))return;
 const customerNavigation=['/','/customer'].includes(url.pathname)&&(request.mode==='navigate'||url.searchParams.has('ui_revision'));
 const stableMapAsset=['/leaflet.js','/leaflet.css'].includes(url.pathname);
 const versionedScript=url.searchParams.has('v')&&/\.js$/.test(url.pathname);
 const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),10000);
 const update=fetch(request,{signal:controller.signal,cache:'no-cache'})
  .then(response=>{
   // Clone before handing the streaming response to the page.
   return {response,copy:response.ok?response.clone():null};
  }).catch(()=>null).finally(()=>clearTimeout(timer));
 event.waitUntil(update.then(async result=>{
  if(!result?.copy)return;
  const cache=await caches.open(SHELL);
  await cache.put(versionedScript?request:new Request(url.origin+url.pathname),result.copy);
  if(PRODUCT_ASSET.test(url.pathname)){
   const keys=await cache.keys(),images=keys.filter(key=>PRODUCT_ASSET.test(new URL(key.url).pathname));
   for(const old of images.slice(0,Math.max(0,images.length-180)))await cache.delete(old);
  }
 }).catch(()=>{}));
 event.respondWith((async()=>{
  const matchOptions={ignoreSearch:stableMapAsset||!versionedScript};
  const cached=await (await caches.open(SHELL)).match(request,matchOptions)||await caches.match(request,matchOptions);
  if(customerNavigation){
    const fresh=(await update)?.response;
    if(fresh&&fresh.status<500)return fresh;
    return cached||fresh||Response.error();
  }
  if((versionedScript||stableMapAsset||(request.mode==='navigate'&&url.pathname==='/admin'))&&cached)return cached;
  return (request.mode==='navigate'||/\.(?:js|css)$/.test(url.pathname))?((await update)?.response||cached||Response.error()):(cached||(await update)?.response||Response.error());
 })());
});

self.addEventListener('push',event=>{
  let data;
  try{data=event.data.json()}catch{return}
  if(!['customer','driver'].includes(data.role)||!Number.isSafeInteger(data.order)||data.order<=0)return;
  const sender=data.role==='customer'?'الطيار':'العميل';
  event.waitUntil(self.registration.showNotification('ولعه · رسالة من '+sender,{
    body:'رسالة جديدة في طلب #'+data.order+' · اضغط لفتح المحادثة',icon:'/icon-192.png',
    tag:'chat-'+data.role+'-'+data.order,renotify:true,data:{role:data.role,order:data.order}
  }));
});
self.addEventListener('notificationclick',event=>{
  const data=event.notification.data;
  if(!data||!['customer','driver'].includes(data.role)||!Number.isSafeInteger(data.order))return;
  event.notification.close();
  const url=new URL('/'+data.role,self.location.origin);
  url.searchParams.set('chat_order',String(data.order));
  event.waitUntil(clients.openWindow(url.href));
});
