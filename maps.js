const tripRouteCache=new Map();
function tripRouteTarget(o){return ['assigned','ready'].includes(o.status)?{lat:o.pickup_lat,lon:o.pickup_lon,label:'مكان الاستلام'}:['picked_up','on_way'].includes(o.status)?{lat:o.latitude,lon:o.longitude,label:'عنوان العميل'}:null}
async function drawTripRoute(map,o){
 const info=document.getElementById('trip-route-info'),target=tripRouteTarget(o);if(!info||!target)return;
 const profile=tab==='driver'?state.driver_profile:null,lat=profile?.lat??o.driver_lat,lon=profile?.lon??o.driver_lon,stamp=profile?.location_at??o.driver_location_at;
 if(target.lat==null||target.lon==null){info.textContent='لا يمكن رسم الطريق: حدد نقطة دقيقة لـ'+target.label+'. استخدم زر الاتجاهات للبحث بالعنوان.';return}
 if(lat==null||lon==null||!stamp||Date.now()-Date.parse(stamp)>300000){info.textContent='فعّل مشاركة موقع الطيار ليظهر الطريق إلى '+target.label;return}
 info.textContent='جاري حساب الطريق إلى '+target.label+'…';
 const key=[lat,lon,target.lat,target.lon].map(x=>Number(x).toFixed(5)).join(','),cached=tripRouteCache.get(key);
 try{
  let route=cached&&Date.now()-cached.at<30000?cached.route:null;
  if(!route){const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),12000);try{const response=await fetch('https://router.project-osrm.org/route/v1/driving/'+lon+','+lat+';'+target.lon+','+target.lat+'?overview=full&geometries=geojson',{signal:controller.signal});if(!response.ok)throw Error('route');const result=await response.json();route=result.routes?.[0];if(result.code!=='Ok'||!route?.geometry?.coordinates?.length)throw Error('no route');tripRouteCache.set(key,{at:Date.now(),route});if(tripRouteCache.size>20)tripRouteCache.delete(tripRouteCache.keys().next().value)}finally{clearTimeout(timer)}}
  if(!mapViews.includes(map))return;
  const line=L.polyline(route.geometry.coordinates.map(([x,y])=>[y,x]),{color:['assigned','ready'].includes(o.status)?'#2864c5':'#d94d16',weight:6,opacity:.85}).addTo(map);map.fitBounds(line.getBounds(),{padding:[25,25],maxZoom:17});info.textContent='الطريق إلى '+target.label+' · '+(route.distance/1000).toFixed(1)+' كم · وقت قيادة تقديري '+Math.max(1,Math.round(route.duration/60))+' دقيقة. مسار قيادة؛ راجع ملاءمته لمركبتك.';
 }catch(e){if(mapViews.includes(map))info.textContent='تعذر تحميل طريق الشوارع الآن. استخدم زر الاتجاهات؛ علامات المواقع ما زالت ظاهرة.'}
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

function clearMaps(){for(const map of mapViews)map.remove();mapViews=[]}
function baseMap(id,zoom=13){
  const el=document.getElementById(id);
  if(!el)return null;
  if(!window.L){el.textContent='تعذر تحميل الخريطة الآن. تحقق من الاتصال ثم حدّث الصفحة.';return null}
  const map=L.map(el,{scrollWheelZoom:false,zoomControl:false}).setView(mapCenter,zoom);
  L.control.zoom({position:'bottomleft'}).addTo(map);
  let webgl=false;try{const canvas=document.createElement('canvas');webgl=!!(canvas.getContext('webgl2')||canvas.getContext('webgl'));}catch(e){}\n  if(webgl&&typeof L.maplibreGL==='function'){
    L.maplibreGL({style:'https://tiles.openfreemap.org/styles/liberty',interactive:false,attribution:'© OpenFreeMap · © OpenMapTiles · © OpenStreetMap contributors'}).addTo(map);
  }else{
    L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png',{
      maxZoom:19,attribution:'&copy; OpenStreetMap contributors'
    }).addTo(map);
  }
  const locate=L.control({position:'topleft'});
  locate.onAdd=()=>{const button=L.DomUtil.create('button','map-locate');button.type='button';button.title='اعرض موقعي الحالي';button.setAttribute('aria-label','اعرض موقعي الحالي');button.textContent='⌖';L.DomEvent.disableClickPropagation(button);L.DomEvent.on(button,'click',()=>{if(!navigator.geolocation)return alert('تحديد الموقع غير مدعوم');navigator.geolocation.getCurrentPosition(p=>map.setView([p.coords.latitude,p.coords.longitude],16),()=>alert('تعذر تحديد موقعك؛ تأكد من صلاحية الموقع.'),{enableHighAccuracy:true,timeout:15000})});return button};
  locate.addTo(map);
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
      window.selectMapPinMode=mode=>{mapPinMode=mode;alert(mode==='pickup'?'المس الخريطة عند مكان الاستلام':'المس الخريطة عند عنوان العميل')};
      map.on('click',e=>choose(e.latlng));
      if(chosenPoint){let mode=mapPinMode;mapPinMode='destination';choose({lat:chosenPoint.latitude,lng:chosenPoint.longitude});mapPinMode=mode}
      if(chosenPickup){let mode=mapPinMode;mapPinMode='pickup';choose({lat:chosenPickup.lat,lng:chosenPickup.lon});mapPinMode=mode}
      if(chosenPoint&&chosenPickup)map.fitBounds([[chosenPoint.latitude,chosenPoint.longitude],[chosenPickup.lat,chosenPickup.lon]],{padding:[30,30],maxZoom:16});
      else if(chosenPoint)map.setView([chosenPoint.latitude,chosenPoint.longitude],16);
      window.pickCurrentLocation=()=>{
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
