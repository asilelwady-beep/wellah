const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync('maps.js', 'utf8');

function setup(bridge) {
  const calls = [], clicks = [], timers = new Map();
  let nextTimer = 1;
  const el = {id:'customer-map',isConnected:true};
  const map = {
    _mapPane:{}, getContainer:()=>el, getCenter:()=>({lat:29.85,lng:31.27}),
    getZoom:()=>16, eachLayer(){}, setView(){}, fire:(name,value)=>clicks.push({name,value})
  };
  const window = {addEventListener(){}};
  if (bridge !== undefined) window.WallahaNative = bridge;
  const context = vm.createContext({window,console,URL,Map,Set,Number,JSON,Math,Date,
    document:{querySelector:()=>null,body:{classList:{remove(){}}}},
    setInterval:fn=>{const id=nextTimer++;timers.set(id,fn);return id},
    clearInterval:id=>timers.delete(id),setTimeout(){},clearTimeout(){},
    requestAnimationFrame:fn=>fn(),state:{},tab:'customer',kind:'delivery'
  });
  vm.runInContext(source,context);
  context.map = map;
  vm.runInContext('mapViews.push(map)',context);
  return {context,map,el,clicks,timers,calls};
}

{
  const {context,map}=setup();
  assert.equal(typeof context.openNativeMap,'function','native map integration must exist');
  assert.equal(context.openNativeMap(map),false,'browsers retain web maps');
}
function nativeSetup() {
  const opened=[],updated=[],closed=[];
  const s=setup({mapsSdkAvailable:()=>true,openMap:raw=>opened.push(JSON.parse(raw)),
    updateMap:raw=>updated.push(JSON.parse(raw)),closeMap:id=>closed.push(id)});
  return {...s,opened,updated,closed};
}
{
  const s=nativeSetup();
  assert.equal(s.context.openNativeMap(s.map),true);
  assert.equal(s.opened[0].mapId,'customer-map');
  assert.equal(s.opened[0].lat,29.85);
  s.context.window.WallahaMaps.receive({requestId:s.opened[0].requestId,status:'selected',lat:29.9,lon:31.3,zoom:17});
  assert.equal(s.clicks.length,1);
  assert.equal(s.clicks[0].name,'click','uses existing checkout selection and quote handler');
  assert.equal(s.clicks[0].value.latlng.lat,29.9);
  assert.equal(s.timers.size,0);
}
{
  const s=nativeSetup();s.map.getCenter=()=>({lat:29.85,lng:391.27});
  assert.equal(s.context.openNativeMap(s.map),true);
  assert.ok(Math.abs(s.opened[0].lon-31.27)<0.000001,'world-copy longitude must wrap before native validation');
}
{
  const s=nativeSetup();s.map.getCenter=()=>({lat:Infinity,lng:31.27});
  assert.equal(s.context.openNativeMap(s.map),false,'invalid snapshot must fall back rather than report an open screen');
  assert.equal(s.opened.length,0);assert.equal(s.timers.size,0);
}
{
  const s=setup();vm.runInContext('Object.hasOwn=undefined',s.context);
  assert.equal(s.context.openNativeMap(s.map),false,'older WebViews retain expansion without Object.hasOwn');
}
for (const invalid of [null,'29.9',NaN,Infinity,91]) {
  const s=nativeSetup();s.context.openNativeMap(s.map);
  s.context.window.WallahaMaps.receive({requestId:s.opened[0].requestId,status:'selected',lat:invalid,lon:31.3});
  assert.equal(s.clicks.length,0,'invalid native latitude must not change checkout');
}
{
  const s=nativeSetup();s.context.openNativeMap(s.map);
  s.context.window.WallahaMaps.receive({requestId:s.opened[0].requestId,status:'cancelled',lat:30,lon:31});
  assert.equal(s.clicks.length,0,'back/cancel preserves chosen point');
}
{
  const s=nativeSetup();s.context.openNativeMap(s.map);
  s.context.window.WallahaMaps.receive({requestId:'stale',status:'selected',lat:30,lon:31});
  assert.equal(s.clicks.length,0);
  s.el.isConnected=false;
  s.context.window.WallahaMaps.receive({requestId:s.opened[0].requestId,status:'selected',lat:30,lon:31});
  assert.equal(s.clicks.length,0,'a detached checkout cannot receive a pin');
}
{
  const s=nativeSetup();s.el.id='driver-map';s.context.openNativeMap(s.map);
  s.context.window.WallahaMaps.receive({requestId:s.opened[0].requestId,status:'selected',lat:30,lon:31});
  assert.equal(s.clicks.length,0,'tracking maps cannot select checkout coordinates');
}
{
  const s=nativeSetup();s.context.openNativeMap(s.map);
  for (const tick of s.timers.values()) tick();
  assert.equal(s.updated.length,1,'live overlay receives marker/route snapshots');
  s.context.closeNativeMap();
  assert.equal(s.closed[0],s.opened[0].requestId);
  assert.equal(s.timers.size,0);
  s.context.window.WallahaMaps.receive({requestId:s.opened[0].requestId,status:'selected',lat:30,lon:31});
  assert.equal(s.clicks.length,0);
}
{
  const s=setup({mapsSdkAvailable:()=>false});
  assert.equal(s.context.openNativeMap(s.map),false,'unconfigured SDK uses the existing map');
  s.context.fetch=()=>{throw Error('Android SDK builds must not request the billed web adapter')};
  s.context.loadGoogleMaps().then(result=>assert.equal(result,false));
}
{
  const s=setup({startDriverTracking(){}});
  assert.equal(s.context.openNativeMap(s.map),false,'older APKs continue to work');
}
console.log('Passed: native map selection, cancellation, stale/invalid callbacks, live updates and web fallback.');
