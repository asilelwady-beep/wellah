const tripRouteCache=new Map();
function tripRouteTarget(o){return ['assigned','ready'].includes(o.status)?{lat:o.pickup_lat,lon:o.pickup_lon,label:'مكان الاستلام'}:['picked_up','on_way'].includes(o.status)?{lat:o.latitude,lon:o.longitude,label:'عنوان العميل'}:null}
function drawTripRoute(map,o){
 const info=document.getElementById('trip-route-info'),target=tripRouteTarget(o);
 if(info&&target)info.textContent='اضغط الاتجاهات إلى '+target.label+' لفتح الملاحة من موقعك الحالي.';
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
  el.classList.remove('map-expanded');
  document.body.classList.remove('map-open');
  const map=mapViews.find(m=>m.getContainer()===el);
  if(map)requestAnimationFrame(()=>map.invalidateSize());
}
window.addEventListener('keydown',e=>{if(e.key==='Escape')closeExpandedMap()});
function expandMap(map){
  closeExpandedMap();
  map.getContainer().classList.add('map-expanded');
  document.body.classList.add('map-open');
  requestAnimationFrame(()=>map.invalidateSize());
}
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
function baseMap(id,zoom=13){
  const el=document.getElementById(id);
  if(!el)return null;
  if(!window.L){el.textContent='تعذر تحميل الخريطة الآن. تحقق من الاتصال ثم حدّث الصفحة.';return null}
  const map=L.map(el,{scrollWheelZoom:false,zoomControl:false,zoomAnimation:true,fadeAnimation:true}).setView(mapCenter,zoom);
  L.control.zoom({position:'bottomleft'}).addTo(map);
  let webgl=false;try{const canvas=document.createElement('canvas');webgl=!!(canvas.getContext('webgl2')||canvas.getContext('webgl'));}catch(e){}
  if(webgl&&typeof L.maplibreGL==='function'){
    L.maplibreGL({style:'https://tiles.openfreemap.org/styles/liberty',interactive:false,attribution:'© OpenFreeMap · © OpenMapTiles · © OpenStreetMap contributors'}).addTo(map);
  }else{
    L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png',{
      maxZoom:19,attribution:'&copy; OpenStreetMap contributors'
    }).addTo(map);
  }
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
    if(map){
      let marker=null;
      let pickupMarker=null;
      const choose=latlng=>{
        if(mapPinMode==='pickup'&&kind!=='products'){
          chosenPickup={lat:latlng.lat,lon:latlng.lng};
          if(pickupMarker)pickupMarker.setLatLng(latlng);
          else pickupMarker=point(map,latlng.lat,latlng.lng,'مكان الاستلام','#2864c5');
          const label=document.getElementById('selected-pickup');
          if(label)label.textContent='تم تحديد مكان الاستلام على الخريطة';
        }else{
          chosenPoint={latitude:latlng.lat,longitude:latlng.lng};
          if(marker)marker.setLatLng(latlng);
          else marker=point(map,latlng.lat,latlng.lng,'عنوان العميل','#e58029');
          const label=document.getElementById('selected-point');
          if(label)label.textContent=`تم تحديد موقع العنوان (${latlng.lat.toFixed(5)}, ${latlng.lng.toFixed(5)})`;
        }
      };
      window.selectMapPinMode=mode=>{mapPinMode=mode;map.getContainer().scrollIntoView({behavior:'smooth',block:'center'});const label=document.getElementById(mode==='pickup'?'selected-pickup':'selected-point');if(label)label.textContent=mode==='pickup'?'المس الخريطة لتحديد الاستلام':'المس الخريطة لتحديد الوجهة'};
      map.on('click',e=>choose(e.latlng));
      if(chosenPoint){let mode=mapPinMode;mapPinMode='destination';choose({lat:chosenPoint.latitude,lng:chosenPoint.longitude});mapPinMode=mode}
      if(chosenPickup){let mode=mapPinMode;mapPinMode='pickup';choose({lat:chosenPickup.lat,lng:chosenPickup.lon});mapPinMode=mode}
      if(chosenPoint&&chosenPickup)map.fitBounds([[chosenPoint.latitude,chosenPoint.longitude],[chosenPickup.lat,chosenPickup.lon]],{padding:[30,30],maxZoom:16});
      else if(chosenPoint)map.setView([chosenPoint.latitude,chosenPoint.longitude],16);
      window.pickCurrentLocation=()=>{
        mapPinMode='destination';
        if(!navigator.geolocation)return alert('تحديد الموقع غير مدعوم');
        navigator.geolocation.getCurrentPosition(p=>{
          const pos={lat:p.coords.latitude,lng:p.coords.longitude};choose(pos);map.setView(pos,16);
        },e=>alert('تعذر تحديد موقعك: '+e.message),{enableHighAccuracy:true,timeout:15000});
      };
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
    for(const o of state.orders.filter(x=>!['cancelled','delivered'].includes(x.status))){
      if(o.pickup_lat!=null){point(map,o.pickup_lat,o.pickup_lon,'استلام من '+esc(o.merchant_name||o.pickup),'#2864c5');positions.push([o.pickup_lat,o.pickup_lon])}
      if(o.latitude!=null&&o.longitude!=null){point(map,o.latitude,o.longitude,'طلب #'+o.id+' · '+esc(o.address),'#e58029');positions.push([o.latitude,o.longitude])}
    }
    showMapPoints(map,positions);
  }
}
