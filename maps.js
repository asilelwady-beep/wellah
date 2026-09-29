/* Map UI for the three signed-in roles. Coordinates are stored only with orders. */
let mapViews=[];
let chosenPoint=null;
let trackingOrderId=null;
const mapCenter=[29.8513,31.2744];
window.pickCurrentLocation=()=>alert('الخريطة لم تُحمّل بعد. تحقق من الاتصال ثم حدّث الصفحة.');

function clearMaps(){for(const map of mapViews)map.remove();mapViews=[]}
function baseMap(id,zoom=13){
  const el=document.getElementById(id);
  if(!el)return null;
  if(!window.L){el.textContent='تعذر تحميل الخريطة الآن. تحقق من الاتصال ثم حدّث الصفحة.';return null}
  const map=L.map(el,{scrollWheelZoom:false}).setView(mapCenter,zoom);
  L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png',{
    maxZoom:19,attribution:'&copy; OpenStreetMap contributors'
  }).addTo(map);
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
  if(tab==='customer'){
    const map=baseMap('customer-map');
    if(map){
      let marker=null;
      const choose=latlng=>{
        chosenPoint={latitude:latlng.lat,longitude:latlng.lng};
        if(marker)marker.setLatLng(latlng);
        else marker=point(map,latlng.lat,latlng.lng,'عنوان الاستلام/الوصول','#e58029');
        const label=document.getElementById('selected-point');
        if(label)label.textContent=`تم تحديد موقع العنوان (${latlng.lat.toFixed(5)}, ${latlng.lng.toFixed(5)})`;
      };
      map.on('click',e=>choose(e.latlng));
      if(chosenPoint)choose({lat:chosenPoint.latitude,lng:chosenPoint.longitude});
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
      if(o.latitude!=null){point(track,o.latitude,o.longitude,'عنوان الطلب: '+esc(o.address),'#e58029');positions.push([o.latitude,o.longitude])}
      if(o.driver_lat!=null){point(track,o.driver_lat,o.driver_lon,'آخر موقع للمندوب · '+esc(o.driver_location_at||''));positions.push([o.driver_lat,o.driver_lon])}
      showMapPoints(track,positions);
    }
  }else if(tab==='admin'){
    const map=baseMap('admin-map');if(!map)return;
    const positions=[];
    for(const o of state.orders.filter(x=>!['cancelled','delivered'].includes(x.status)&&x.latitude!=null)){
      point(map,o.latitude,o.longitude,'طلب #'+o.id+' · '+esc(o.area)+' · '+esc(o.address),'#e58029');
      positions.push([o.latitude,o.longitude]);
    }
    for(const d of state.drivers.filter(x=>x.lat!=null&&x.lon!=null)){
      point(map,d.lat,d.lon,'المندوب '+esc(d.name)+' · آخر تحديث '+esc(d.location_at||''));
      positions.push([d.lat,d.lon]);
    }
    showMapPoints(map,positions);
  }else if(tab==='driver'){
    const map=baseMap('driver-map');if(!map)return;
    const positions=[];
    for(const o of state.orders.filter(x=>!['cancelled','delivered'].includes(x.status)&&x.latitude!=null)){
      point(map,o.latitude,o.longitude,'طلب #'+o.id+' · '+esc(o.address),'#e58029');
      positions.push([o.latitude,o.longitude]);
    }
    showMapPoints(map,positions);
  }
}
