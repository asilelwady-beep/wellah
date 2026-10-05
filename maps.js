const tripRouteCache=new Map();
function tripRouteTarget(o){return ['assigned','ready'].includes(o.status)?(o.shop_anywhere?null:{lat:o.pickup_lat,lon:o.pickup_lon,label:'مكان الاستلام'}):['picked_up','on_way'].includes(o.status)?{lat:o.latitude,lon:o.longitude,label:'عنوان العميل'}:null}
function drawTripRoute(map,o){
 const info=document.getElementById('trip-route-info'),target=tripRouteTarget(o);
 if(info)info.textContent=target?'اضغط الاتجاهات إلى '+target.label+' لفتح الملاحة من موقعك الحالي.':o.shop_anywhere&&['assigned','ready'].includes(o.status)?'اختار سوبر ماركت قريب، راجع صور المنتجات، ثم أكد شراءها.':'تابع حالة الطلب على الخريطة.';
}

/* Map UI for the three signed-in roles. Coordinates are stored only with orders. */
let mapViews=[];
let chosenPoint=null;
let chosenPickup=null;
let mapPinMode='destination';
let merchantPoint=null;
let trackingOrderId=null;
const mapCenter=[29.8513,31.2744];
window.pickCurrentLocation=()=>alert('الخريطة لم تُحمّل بعد. تحقق من الاتصال ثم حدّث الصفحة.');
window.selectMapPinMode=()=>alert('الخريطة لم تُحمّل بعد. تحقق من الاتصال ثم حدّث الصفحة.');

function closeExpandedMap(){
  const el=document.querySelector('.map.map-expanded');
  if(!el)return;
  if(el._searchPanel){
    const {panel,form,results}=el._searchPanel;
    el.before(form);
    el.after(results);
    panel.remove();
    el._searchPanel=null;
  }
  el.classList.remove('map-expanded');
  document.body.classList.remove('map-open');
  const map=mapViews.find(m=>m.getContainer()===el);
  if(map)requestAnimationFrame(()=>map.invalidateSize());
}
window.addEventListener('keydown',e=>{if(e.key==='Escape')closeExpandedMap()});
function expandMap(map){
  closeExpandedMap();
  const el=map.getContainer();
  el.classList.add('map-expanded');
  document.body.classList.add('map-open');
  if(el.id==='customer-map'||el.id==='pickup-map'){
    const pickup=el.id==='pickup-map';
    const form=document.querySelector(pickup?'.map-search:has(#pickup-map-search)':'.map-search:has(#map-search)');
    const results=document.getElementById(pickup?'pickup-map-search-results':'map-search-results');
    if(form&&results){
      const panel=document.createElement('section');
      panel.className='map-search-panel';
      panel.setAttribute('aria-label',pickup?'بحث وتحديد نقطة الاستلام':'بحث وتحديد نقطة الوصول');
      const title=document.createElement('h3');
      title.textContent=pickup?'حدد نقطة الاستلام':'حدد نقطة الوصول';
      const hint=document.createElement('p');
      hint.textContent='ابحث بالاسم، اختر النتيجة، ثم راجع الدبوس على الخريطة.';
      const done=document.createElement('button');
      done.type='button';
      done.className='map-panel-done';
      done.textContent='تأكيد الدبوس والعودة';
      done.addEventListener('click',closeExpandedMap);
      panel.append(title,hint,form,results,done);
      L.DomEvent.disableClickPropagation(panel);
      L.DomEvent.disableScrollPropagation(panel);
      el.append(panel);
      el._searchPanel={panel,form,results};
    }
  }
  requestAnimationFrame(()=>map.invalidateSize({pan:false}));
  setTimeout(()=>map.invalidateSize({pan:false}),250);
  setTimeout(()=>map.invalidateSize({pan:false}),650);
}
function validMapPoint(lat,lon){
  lat=Number(lat);lon=Number(lon);
  return Number.isFinite(lat)&&Number.isFinite(lon)&&lat>=-90&&lat<=90&&lon>=-180&&lon<=180?{lat,lon}:null;
}
function parseMapCoordinates(raw){
  const text=String(raw||'').trim();
  const direct=text.match(/^(?:geo:)?\s*(-?\d{1,2}\.\d+)\s*[,،]\s*(-?\d{1,3}\.\d+)/i);
  if(direct)return validMapPoint(direct[1],direct[2]);
  const link=text.match(/https?:\/\/[^\s<>]+/i);
  if(!link)return null;
  let url;try{url=new URL(link[0])}catch{return null}
  const data=url.href.match(/!3d(-?\d{1,2}\.\d+)!4d(-?\d{1,3}\.\d+)/i);
  if(data)return validMapPoint(data[1],data[2]);
  for(const name of ['q','query','ll','daddr','destination']){
    const val=url.searchParams.get(name),match=val?.match(/(-?\d{1,2}\.\d+)\s*[,،]\s*(-?\d{1,3}\.\d+)/);
    if(match)return validMapPoint(match[1],match[2]);
  }
  const mlat=url.searchParams.get('mlat'),mlon=url.searchParams.get('mlon');
  if(mlat&&mlon)return validMapPoint(mlat,mlon);
  const at=url.href.match(/@(-?\d{1,2}\.\d+)\s*,\s*(-?\d{1,3}\.\d+)/);
  return at?validMapPoint(at[1],at[2]):null;
}
async function searchMapAddress(mode='destination'){
  const pickup=mode==='pickup';
  const input=document.getElementById(pickup?'pickup-map-search':'map-search'),results=document.getElementById(pickup?'pickup-map-search-results':'map-search-results');
  const setPoint=pickup?window.setCustomerPickupPoint:window.setCustomerMapPoint;
  if(!input||!results||!setPoint)return;
  const query=input.value.trim();
  results.replaceChildren();
  const status=document.createElement('p');results.appendChild(status);
  if(!query){status.textContent='اكتب اسم مكان أو شارع، أو الصق رابط لوكيشن';return}
  let point=parseMapCoordinates(query);
  if(point){setPoint(point.lat,point.lon,'المكان من رابط اللوكيشن');status.textContent='تم تحديد المكان من اللوكيشن. راجع العلامة على الخريطة.';return}
  let address=query;
  if(/^https?:\/\//i.test(query)){
    status.textContent='جارٍ قراءة رابط اللوكيشن…';
    try{
      const response=await fetch('/api/resolve-map-link?url='+encodeURIComponent(query));
      const data=await response.json();if(!response.ok)throw Error(data.error||'تعذر قراءة الرابط');
      point=parseMapCoordinates(data.url);
      if(!point)throw Error('الرابط لا يحتوي نقطة دقيقة؛ افتحه وانسخ رابط المكان أو حدد العلامة على الخريطة');
      setPoint(point.lat,point.lon,'المكان من رابط اللوكيشن');
      status.textContent='تم تحديد المكان من اللوكيشن. راجع العلامة على الخريطة.';
    }catch(error){status.textContent=error.message}
    return;
  }
  status.textContent='جارٍ البحث عن العنوان…';
  try{
    const response=await fetch('/api/geocode?q='+encodeURIComponent(query));
    const data=await response.json();if(!response.ok)throw Error(data.error||'تعذر البحث');
    const places=data.results||[];
    if(!places.length){status.textContent='لم أجد نتيجة مؤكدة. جرّب اسم المكان مع القرية، أو الصق رابط Google Maps.';return}
    status.textContent='اختار المكان الصحيح من النتائج:';
    for(const place of places.slice(0,5)){
      const button=document.createElement('button');
      button.type='button';
      button.className='map-place-result';
      const icon=document.createElement('span');icon.className='map-place-icon';icon.textContent='📍';
      const label=document.createElement('span');label.textContent=place.label||address;
      button.append(icon,label);
      button.addEventListener('click',()=>{
        setPoint(place.lat,place.lon,place.label||address);
        status.textContent='تم اختيار '+(place.label||address)+' — تأكد أن العلامة على مدخل المكان الصحيح قبل حساب السعر.';
        for(const item of results.querySelectorAll('button'))item.setAttribute('aria-pressed',String(item===button));
      });
      results.appendChild(button);
    }
  }catch(error){status.textContent=error.message||'تعذر البحث؛ حدد النقطة على الخريطة'}
}
window.searchCustomerMapAddress=()=>searchMapAddress('destination');
window.searchPickupMapAddress=()=>searchMapAddress('pickup');

function clearMaps(){closeExpandedMap();for(const map of mapViews){if(map._gpsWatch!=null)navigator.geolocation?.clearWatch(map._gpsWatch);map.remove()}mapViews=[]}
function showMyLocationOnMap(id){
  const map=mapViews.find(m=>m.getContainer().id===id);
  if(!map||!navigator.geolocation){alert('GPS غير متاح على هذا الجهاز');return}
  expandMap(map);
  if(map._gpsWatch!=null)navigator.geolocation.clearWatch(map._gpsWatch);
  let first=true;
  map._gpsWatch=navigator.geolocation.watchPosition(pos=>{
    if(!mapViews.includes(map))return;
    const coords=[pos.coords.latitude,pos.coords.longitude];
    if(!map._gpsMarker)map._gpsMarker=L.circleMarker(coords,{radius:11,color:'#fff',weight:4,fillColor:'#1686ef',fillOpacity:1,className:'live-gps-dot'}).addTo(map).bindPopup('موقعي الحالي');
    else map._gpsMarker.setLatLng(coords);
    if(first){map.flyTo(coords,17,{duration:0.7});first=false}
  },()=>alert('تعذر الوصول إلى موقعك. فعّل GPS واسمح للموقع بالوصول.'),{enableHighAccuracy:true,maximumAge:3000,timeout:15000});
}
let googleMapsReadyPromise=null;
function loadGoogleMaps(){
  if(googleMapsReadyPromise)return googleMapsReadyPromise;
  googleMapsReadyPromise=fetch('/api/maps-config').then(r=>r.ok?r.json():{}).then(config=>{
    const key=config.google_maps_key;
    if(!key)return false;
    const googleReady=window.google?.maps?Promise.resolve():new Promise((resolve,reject)=>{
      const callback='__wallahGoogleMapsReady';
      const timer=setTimeout(()=>reject(new Error('Google Maps timed out')),15000);
      window[callback]=()=>{clearTimeout(timer);delete window[callback];resolve()};
      const script=document.createElement('script');
      script.src='https://maps.googleapis.com/maps/api/js?key='+encodeURIComponent(key)+'&v=weekly&language=ar&region=EG&loading=async&callback='+callback;
      script.async=true;
      script.onerror=()=>{clearTimeout(timer);delete window[callback];reject(new Error('Google Maps unavailable'))};
      document.head.appendChild(script);
    });
    return googleReady.then(()=>new Promise((resolve,reject)=>{
      if(L.gridLayer?.googleMutant)return resolve(true);
      const script=document.createElement('script');
      script.src='https://unpkg.com/leaflet.gridlayer.googlemutant@latest/Leaflet.GoogleMutant.js';
      script.onload=()=>resolve(!!L.gridLayer?.googleMutant);
      script.onerror=()=>reject(new Error('Google map adapter unavailable'));
      document.head.appendChild(script);
    }));
  }).catch(()=>false);
  return googleMapsReadyPromise;
}
function baseMap(id,zoom=13){
  const el=document.getElementById(id);
  if(!el)return null;
  if(!window.L){el.textContent='تعذر تحميل الخريطة الآن. تحقق من الاتصال ثم حدّث الصفحة.';return null}
  const map=L.map(el,{scrollWheelZoom:false,zoomControl:false,zoomAnimation:true,fadeAnimation:true}).setView(mapCenter,zoom);
  L.control.zoom({position:'bottomleft'}).addTo(map);
  const baseLayer=L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png',{
    maxZoom:19,attribution:'&copy; OpenStreetMap contributors'
  }).addTo(map);
  loadGoogleMaps().then(ready=>{
    if(!ready||!mapViews.includes(map))return;
    try{
      const googleLayer=L.gridLayer.googleMutant({type:'roadmap',maxZoom:21});
      googleLayer.once('load',()=>{if(mapViews.includes(map)){map.removeLayer(baseLayer);el.dataset.mapProvider='google'}});
      googleLayer.addTo(map);
    }catch(error){console.warn('Google Maps layer unavailable',error)}
  });
  const locate=L.control({position:'topleft'});
  locate.onAdd=()=>{const button=L.DomUtil.create('button','map-locate');button.type='button';button.title='اعرض موقعي الحالي';button.setAttribute('aria-label','اعرض موقعي الحالي');button.textContent='⌖';L.DomEvent.disableClickPropagation(button);L.DomEvent.on(button,'click',()=>showMyLocationOnMap(id));return button};
  locate.addTo(map);
  const expand=L.control({position:'topright'});
  expand.onAdd=()=>{const button=L.DomUtil.create('button','map-expand');button.type='button';button.title='تكبير الخريطة';button.setAttribute('aria-label','تكبير الخريطة');button.textContent='⛶';L.DomEvent.disableClickPropagation(button);L.DomEvent.on(button,'click',()=>{if(el.classList.contains('map-expanded'))closeExpandedMap();else expandMap(map)});return button};
  expand.addTo(map);
  const close=document.createElement('button');close.type='button';close.className='map-close';close.textContent='✕ إغلاق الخريطة';close.setAttribute('aria-label','إغلاق الخريطة');close.addEventListener('click',e=>{e.stopPropagation();closeExpandedMap()});el.appendChild(close);
  mapViews.push(map);
  requestAnimationFrame(()=>map.invalidateSize());
  return map;
}
function point(map,lat,lon,title,color='#087b5b'){
  if(!Number.isFinite(Number(lat))||!Number.isFinite(Number(lon)))return null;
  return L.circleMarker([Number(lat),Number(lon)],{
    radius:10,color:'#fff',weight:2,fillColor:color,fillOpacity:1
  }).addTo(map).bindPopup(title);
}
function showMapPoints(map,points){
  if(points.length===1)map.setView(points[0],15);
  else if(points.length>1)map.fitBounds(points,{padding:[30,30],maxZoom:15});
}
window.showNearbyMapRoads=async()=>{
  const map=mapViews.find(view=>view.getContainer().id==='customer-map');
  const status=document.getElementById('map-roads-status');
  if(!map||!status)return;
  if(map.getZoom()<14){status.textContent='كبّر الخريطة على القرية أو الحارة أولاً.';return}
  status.textContent='جارٍ تحميل الشوارع والحارات حول الجزء الظاهر من الخريطة…';
  const center=map.getCenter();
  try{
    const response=await fetch('/api/map-roads?lat='+center.lat.toFixed(5)+'&lon='+center.lng.toFixed(5));
    const data=await response.json();
    if(!response.ok)throw Error(data.error||'تعذر تحميل الشوارع');
    if(map._roadOverlay)map.removeLayer(map._roadOverlay);
    const layer=L.layerGroup().addTo(map);
    map._roadOverlay=layer;
    for(const road of data.roads){
      const line=L.polyline(road.points,{color:'#d12e43',weight:5,opacity:0.8}).addTo(layer);
      const name=road.name||'حارة غير مسماة على الخريطة';
      line.bindTooltip(name);
      line.on('click',e=>{
        L.DomEvent.stopPropagation(e);
        window.setCustomerMapPoint(e.latlng.lat,e.latlng.lng,
          road.name?road.name+'، البدرشين':'حارة محددة على الخريطة، البدرشين');
        status.textContent='تم تحديد '+name+' — راجع العلامة وأضف رقم المنزل أو علامة مميزة.';
      });
    }
    status.textContent=data.roads.length?
      'ظهر '+data.roads.length+' شارعًا وحارة مسجلة في هذا الجزء. اضغط على الخط لتحديد مكانك الدقيق، وحرّك الخريطة ثم كرر البحث لباقي القرية.':
      'لا توجد شوارع مسجلة في هذا الجزء؛ حدد المكان يدويًا وأضف علامة مميزة.';
  }catch(error){status.textContent=error.message||'تعذر تحميل الشوارع'}
};
function initMaps(){
  if(!state)return;
  const tripMap=baseMap('trip-map');
  if(tripMap){
    const active=state.orders.filter(o=>!['cancelled','delivered'].includes(o.status));
    const o=state.orders.find(o=>o.id===selectedTripId)||(tab==='driver'?active.find(o=>['assigned','ready','picked_up','on_way'].includes(o.status)):null);
    const positions=[];
    if(o){for(const [lat,lon,label,color] of [[o.pickup_lat,o.pickup_lon,'الاستلام: '+esc(o.merchant_address||o.pickup),'#2864c5'],[o.latitude,o.longitude,'التسليم: '+esc(o.address),'#e58029'],[o.driver_lat,o.driver_lon,'آخر موقع للطيار: '+esc(o.driver_location_at||''),'#087b5b']]){if(lat!=null&&lon!=null){point(tripMap,lat,lon,label,color);positions.push([lat,lon])}}}
    showMapPoints(tripMap,positions);
    if(o)drawTripRoute(tripMap,o);
  }
  if(tab==='customer'){
    const map=baseMap('customer-map');
    const pickupMap=kind==='products'?null:baseMap('pickup-map');
    if(map){
      let marker=null;
      let pickupMarker=null;
      const choose=(latlng,mode='destination')=>{
        if(mode==='pickup'&&pickupMap){
          chosenPickup={lat:latlng.lat,lon:latlng.lng};
          if(pickupMarker)pickupMarker.setLatLng(latlng);
          else pickupMarker=point(pickupMap,latlng.lat,latlng.lng,'مكان الاستلام','#2864c5');
          const label=document.getElementById('selected-pickup');
          if(label)label.textContent='تم تحديد مكان الانطلاق';const pickup=document.getElementById('pickup');if(pickup&&!pickup.value.trim())pickup.value='نقطة الانطلاق على الخريطة';
        }else{
          chosenPoint={latitude:latlng.lat,longitude:latlng.lng};
          if(marker)marker.setLatLng(latlng);
          else marker=point(map,latlng.lat,latlng.lng,'عنوان العميل','#e58029');
          const label=document.getElementById('selected-point');
          if(label)label.textContent='تم تحديد المكان على الخريطة';if(kind!=='products'){const address=document.getElementById('address'),destination=document.getElementById('destination');if(address&&!address.value.trim())address.value='الوجهة على الخريطة';if(destination&&!destination.value.trim())destination.value='الوجهة على الخريطة'}
        }
      };
      window.clearCustomerMapPoint=()=>{
        chosenPoint=null;
        if(marker){map.removeLayer(marker);marker=null}
        const selected=document.getElementById('selected-point');
        if(selected)selected.textContent='اختر نتيجة البحث أو المس الموقع الصحيح على الخريطة';
      };
      window.setCustomerMapPoint=(lat,lon,label)=>{
        const point=validMapPoint(lat,lon);
        if(!point||!mapViews.includes(map))return;
        choose({lat:point.lat,lng:point.lon},'destination');
        const address=document.getElementById('address'),destination=document.getElementById('destination');
        if(address)address.value=label;
        if(destination)destination.value=label;
        const area=document.getElementById('area');
        if(area){
          const name=String(label||'');
          const match=(state.areas||[]).find(v=>v!=='مدينة البدرشين'&&name.includes(v))||(name.includes('البدرشين')?'مدينة البدرشين':null);
          if(match){area.value=match;area.dispatchEvent(new Event('change'))}
        }
        const chosen=document.getElementById('selected-point');
        if(chosen)chosen.textContent='📍 '+label;
        map.flyTo([point.lat,point.lon],17,{duration:0.6});
      };
      window.setCustomerPickupPoint=(lat,lon,label)=>{
        const p=validMapPoint(lat,lon);
        if(!p||!pickupMap||!mapViews.includes(pickupMap))return;
        choose({lat:p.lat,lng:p.lon},'pickup');
        const input=document.getElementById('pickup');
        if(input)input.value=label;
        const chosen=document.getElementById('selected-pickup');
        if(chosen)chosen.textContent='📍 '+label;
        pickupMap.flyTo([p.lat,p.lon],17,{duration:0.6});
      };
      window.selectMapPinMode=mode=>{mapPinMode=mode;(mode==='pickup'&&pickupMap?pickupMap:map).getContainer().scrollIntoView({behavior:'smooth',block:'center'});const label=document.getElementById(mode==='pickup'?'selected-pickup':'selected-point');if(label)label.textContent=mode==='pickup'?'المس الخريطة لتحديد الاستلام':'المس الخريطة لتحديد الوجهة'};
      map.on('click',e=>{
        choose(e.latlng,'destination');
        const selected=document.getElementById('selected-point');
        if(selected)selected.textContent='📍 النقطة المحددة يدويًا — راجع العنوان المكتوب قبل تأكيد الطلب';
      });
      if(pickupMap)pickupMap.on('click',e=>choose(e.latlng,'pickup'));
      if(chosenPoint)choose({lat:chosenPoint.latitude,lng:chosenPoint.longitude},'destination')
      if(chosenPickup)choose({lat:chosenPickup.lat,lng:chosenPickup.lon},'pickup')
      if(chosenPoint)map.setView([chosenPoint.latitude,chosenPoint.longitude],16);
      if(chosenPickup&&pickupMap)pickupMap.setView([chosenPickup.lat,chosenPickup.lon],16);
      window.pickCurrentLocation=()=>{
        mapPinMode='destination';
        if(!navigator.geolocation)return alert('تحديد الموقع غير مدعوم');
        navigator.geolocation.getCurrentPosition(p=>{
          if(!mapViews.includes(map))return;
          const pos={lat:p.coords.latitude,lng:p.coords.longitude};choose(pos);map.flyTo(pos,17,{duration:0.7});
        },()=>{const label=document.getElementById('selected-point');if(label)label.textContent='اسمح بالوصول إلى الموقع أو المس مكانك على الخريطة'}, {enableHighAccuracy:true,maximumAge:0,timeout:15000});
      };
      if(customerView==='checkout'&&!autoCheckoutLocationAttempted){
        autoCheckoutLocationAttempted=true;
        if(!navigator.geolocation){const label=document.getElementById(kind==='products'?'selected-point':'selected-pickup');if(label)label.textContent='الموقع غير متاح؛ المس مكانك على الخريطة'}
        else navigator.geolocation.getCurrentPosition(p=>{
          if(!mapViews.includes(map))return;
          const pos={lat:p.coords.latitude,lng:p.coords.longitude};
          if(kind==='products'){if(chosenPoint)return;choose(pos,'destination');map.flyTo(pos,17,{duration:0.7})}
          else{if(chosenPickup)return;choose(pos,'pickup');pickupMap?.flyTo(pos,17,{duration:0.7})}
        },()=>{const label=document.getElementById(kind==='products'?'selected-point':'selected-pickup');if(label)label.textContent='اسمح بالوصول إلى الموقع أو المس مكانك على الخريطة'}, {enableHighAccuracy:true,maximumAge:0,timeout:15000});
      }
    }
    const o=state.orders.find(x=>x.id===trackingOrderId);
    const track=baseMap('tracking-map');
    if(track&&o){
      const positions=[];
      if(o.pickup_lat!=null){point(track,o.pickup_lat,o.pickup_lon,'المحل: '+esc(o.merchant_name||o.pickup),'#2864c5');positions.push([o.pickup_lat,o.pickup_lon])}
      if(o.latitude!=null){point(track,o.latitude,o.longitude,'عنوان الطلب: '+esc(o.address),'#e58029');positions.push([o.latitude,o.longitude])}
      if(o.driver_lat!=null){point(track,o.driver_lat,o.driver_lon,'آخر موقع للمندوب · '+esc(o.driver_location_at||''));positions.push([o.driver_lat,o.driver_lon])}
      showMapPoints(track,positions);
    }
  }else if(tab==='admin'){
    const map=baseMap('admin-map');if(!map)return;
    const positions=[];
    for(const m of state.merchants.filter(x=>x.active)){
      point(map,m.lat,m.lon,'المحل '+esc(m.name)+' · '+esc(m.address),'#2864c5');
      positions.push([m.lat,m.lon]);
    }
    for(const o of state.orders.filter(x=>!['cancelled','delivered'].includes(x.status))){
      point(map,o.latitude,o.longitude,'طلب #'+o.id+' · '+esc(o.area)+' · '+esc(o.address),'#e58029');
      positions.push([o.latitude,o.longitude]);
    }
    for(const d of state.drivers.filter(x=>x.lat!=null&&x.lon!=null)){
      point(map,d.lat,d.lon,'المندوب '+esc(d.name)+' · آخر تحديث '+esc(d.location_at||''));
      positions.push([d.lat,d.lon]);
    }
    showMapPoints(map,positions);
    const merchantMap=baseMap('merchant-map');
    if(merchantMap){
      let marker=null;
      const choose=latlng=>{
        merchantPoint={lat:latlng.lat,lon:latlng.lng};
        if(marker)marker.setLatLng(latlng);
        else marker=point(merchantMap,latlng.lat,latlng.lng,'مكان المحل','#2864c5');
        const label=document.getElementById('merchant-point');
        if(label)label.textContent='تم تحديد موقع المحل على الخريطة';
      };
      merchantMap.on('click',e=>choose(e.latlng));
      if(merchantPoint)choose({lat:merchantPoint.lat,lng:merchantPoint.lon});
    }
  }else if(tab==='driver'){
    const map=baseMap('driver-map');if(!map)return;
    const positions=[];
    const active=state.orders.filter(x=>!['cancelled','delivered'].includes(x.status));
    const chosen=active.find(o=>o.id===selectedTripId);
    for(const o of (chosen?[chosen]:active)){
      const popup='<strong>طلب #'+o.id+'</strong> · '+esc(o.area)+'<br>'+esc(o.address)+'<br><button type="button" onclick="openTrip('+o.id+')">تفاصيل الطلب</button>'+(o.status==='offered'?'<button type="button" onclick="act('+o.id+',\'accept_offer\')">قبول</button>':'');
      if(o.pickup_lat!=null){point(map,o.pickup_lat,o.pickup_lon,'استلام من '+esc(o.shop_anywhere?o.pickup:o.merchant_name||o.pickup),'#2864c5');positions.push([o.pickup_lat,o.pickup_lon])}
      if(o.latitude!=null&&o.longitude!=null){point(map,o.latitude,o.longitude,popup,'#e58029');positions.push([o.latitude,o.longitude])}
    }
    const profile=state.driver_profile;
    if(profile?.lat!=null&&profile?.lon!=null){point(map,profile.lat,profile.lon,'موقعي الحالي','#087b5b');if(!positions.length)positions.push([profile.lat,profile.lon])}
    showMapPoints(map,positions);
  }
}
