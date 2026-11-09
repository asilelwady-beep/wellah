const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const maps=fs.readFileSync('maps.js','utf8'),html=fs.readFileSync('index.html','utf8');
const cells=new Map(),routes=[],windowEvents={},dialogEvents={};
let tick,watch,clock=100000,cleared=false,removed=false;
const latLng=(lat,lng)=>({lat,lng,distanceTo(other){return Math.hypot(lat-other.lat,(lng-other.lng)*.87)*111000}});
let bounds;const pins=[];
const map={fitBounds(points){bounds=points},setView(){return this},on(){return this},remove(){removed=true}};
const layer=()=>({addTo(){return this},remove(){},setLatLng(){}});
const dialog={setAttribute(){},showModal(){this.open=true},close(){this.open=false;dialogEvents.close()},remove(){},addEventListener(k,f){dialogEvents[k]=f},querySelector(k){if(!cells.has(k))cells.set(k,{});return cells.get(k)}};
const order={id:4,status:'assigned',pickup_lat:29.85,pickup_lon:31.28,latitude:29.87,longitude:31.29};
const window={addEventListener(k,f){windowEvents[k]=f},removeEventListener(k){delete windowEvents[k]}};
const L={latLng,map:()=>map,tileLayer:layer,circleMarker:point=>{const pin={...layer(),getLatLng:()=>point,bindTooltip(label){pins.push(label);return this}};return pin},marker:layer,polyline:layer,divIcon:()=>({})};window.L=L;
const ctx=vm.createContext({window,L,document:{createElement:()=>dialog,body:{append(){}}},tab:'driver',state:{orders:[order]},Date:{now:()=>clock},history:{state:null,pushState(s){this.state=s},back(){this.state=null}},navigator:{geolocation:{watchPosition(f){watch=f;return 7},clearWatch(id){assert.equal(id,7);cleared=true}}},setInterval(f){tick=f;return 1},clearInterval(){},setTimeout,clearTimeout,AbortController,fetch:async url=>{
  const [origin,end]=url.split('driving/')[1].split('?')[0].split(';').map(p=>p.split(',').map(Number)),mid=[end[0],origin[1]];
  routes.push({origin,end});assert.match(url,/steps=true/);
  return {ok:true,json:async()=>({code:'Ok',routes:[{duration:300,legs:[{steps:[['depart',[origin,mid]],['turn',[mid,end]],['arrive',[end,end]]].map(([type,coordinates])=>({name:'شارع المحطة',maneuver:{type,modifier:'right'},geometry:{coordinates}}))}]}]})};
}});
vm.runInContext(maps,ctx);
const fix=async(lat,lon)=>{watch({timestamp:clock,coords:{latitude:lat,longitude:lon,accuracy:5}});await new Promise(setImmediate)};
(async()=>{
  const button=html.slice(html.indexOf('function driverGoogleMapsUrl('),html.indexOf('\n}',html.indexOf('function driverNavigationButton('))+2);
  vm.runInContext(button,ctx);ctx.driverIcon=()=>'';ctx.esc=s=>s.replaceAll('&','&amp;');
  const target=(o,point)=>new URL(ctx.driverGoogleMapsUrl(o,point)).searchParams.get('destination');
  assert.equal(target(order),'29.85,31.28');
  assert.equal(target({...order,status:'on_way'}),'29.87,31.29');
  assert.equal(target({...order,status:'returning'}),'29.85,31.28');
  assert.equal(target({...order,status:'picked_up',latitude:null,address:'شارع النيل',area:'البدرشين'}),'شارع النيل، البدرشين');
  assert.match(ctx.driverGoogleMapsUrl({...order,shop_anywhere:true}),/maps\/search/);
  assert.equal(target({...order,shop_anywhere:true},latLng(29.8,31.2)),'29.8,31.2');
  assert.match(ctx.driverNavigationButton(order),/href="https:\/\/www.google.com\/maps\/dir/);
  assert.match(ctx.driverNavigationButton(order),/startDriverGuidance\(4\)/);
  await ctx.startDriverGuidance(4);assert.ok(dialog.open);
  await fix(29.84,31.27);assert.match(cells.get('.guidance-text').textContent,/خُد يمين/);
  assert.deepEqual(pins,['مكان الاستلام','عنوان العميل']);
  cells.get('[data-overview]').onclick();
  for(const [lat,lng] of [[29.84,31.27],[29.85,31.28],[29.87,31.29]])assert.ok(bounds.some(p=>p.lat===lat&&p.lng===lng));
  clock+=8000;await fix(29.83,31.265);assert.equal(routes.length,2);
  order.status='picked_up';tick();await new Promise(setImmediate);
  assert.deepEqual(routes.at(-1).end,[31.29,29.87]);
  assert.equal(new URL(cells.get('[data-google]').href).searchParams.get('destination'),'29.87,31.29');
  clock+=8000;await fix(29.87,31.29);assert.match(cells.get('.guidance-text').textContent,/وصلت/);
  windowEvents.popstate();assert.equal(dialog.open,false);assert.ok(cleared&&removed);
  const source=(from,to)=>html.slice(html.indexOf(from),html.indexOf(to,html.indexOf(from)));
  vm.runInContext(source('function returnAction(', 'async function submitQuote('),ctx);
  vm.runInContext(source('async function act(', 'function jumpAdmin('),ctx);
  const opened=[],uploads=[],alerts=[];
  Object.assign(ctx,{$:()=>({files:['photo']}),compressMerchantPhoto:async()=> 'proof',alert:m=>alerts.push(m),
    driverCheckedItems:new Map(),driverShopDrafts:new Map(),api:async(path,payload)=>{
      uploads.push(payload);order.status='on_way';
    }});
  ctx.window.location={assign(url){opened.push(new URL(url).searchParams.get('destination'))}};
  order.driver_id=7;order.status='ready';
  assert.match(ctx.driverMapActions(order),/تم الاستلام/);
  await ctx.act(order.id,'picked_up');
  assert.equal(uploads[0].pickup_photo,'proof');
  assert.deepEqual(opened,['29.87,31.29']);
  for(const status of ['picked_up','on_way']){
    order.status=status;
    const actions=ctx.driverMapActions(order);
    assert.match(actions,/تم التسليم/);
    assert.match(actions,/delivery-photo-4/);
    assert.doesNotMatch(actions,/act\(4,'on_way'\)/);
  }
  ctx.api=async()=>{throw Error('لم يتم حفظ الاستلام')};order.status='ready';
  await ctx.act(order.id,'picked_up');
  assert.equal(opened.length,1);
  assert.match(alerts.at(-1),/حفظ الاستلام/);
  vm.runInContext(source('function driverOrderDetails(', 'function driverProductChecklist('),ctx);
  vm.runInContext(html.split('\n').find(line=>line.startsWith('function renderDriverOld(')),ctx);
  Object.assign(ctx,{selectedTripId:null,status:{},esc:s=>String(s??'').replaceAll('&','&amp;'),
    driverDetailContact:()=>'',customerOrderPriceMarkup:()=>'',money:String,
    driverActions:()=>'',orderChatButton:()=>'',ratingPanel:()=>''});
  order.kind='delivery';order.return_percent=50;order.status='on_way';
  let screen=ctx.renderDriverOld();
  assert.equal(screen.split("act(4,'returning')").length-1,1);
  order.status='returning';
  screen=ctx.renderDriverOld();
  assert.equal(screen.split('https://www.google.com/maps/dir').length-1,1);
  assert.equal(screen.split('startDriverGuidance(4)').length-1,1);
  assert.equal(screen.split("act(4,'returned')").length-1,1);
  assert.ok(!screen.includes("act(4,'returning')"));
  assert.match(screen,/destination=29.85%2C31.28/);
  console.log('Passed: driver CTA, pickup maneuver, deviation reroute, delivery switch, arrival, back and GPS cleanup.');
})().catch(e=>{console.error(e);process.exitCode=1});
