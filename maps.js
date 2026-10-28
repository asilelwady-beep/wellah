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
let merchantEditorDraft=null;
let merchantMapView=null;
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
      title.textContent=pickup?(kind==='delivery'?'حدد مكان المرسل':'حدد نقطة الاستلام'):(kind==='delivery'?'حدد مكان المستلم':'حدد نقطة الوصول');
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
      const response=await (window.appFetchWithTimeout||fetch)('/api/resolve-map-link?url='+encodeURIComponent(query),{},10000);
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
    const response=await (window.appFetchWithTimeout||fetch)('/api/geocode?q='+encodeURIComponent(query),{},10000);
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
      results.appendChild(b