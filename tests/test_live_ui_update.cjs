const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const html=fs.readFileSync('index.html','utf8');
assert.ok(html.includes('function checkAppUpdate('),'A resumed app must check the UI revision, not only its catalog');
const code=html.slice(html.indexOf('const APP_UPDATE_CHECK_MS'),html.indexOf('// End live UI updates'));
function setup(){
 let timer,requests=0,navigations=[],remote='new123',reject=false,resolvePage;const storage=new Map();
 const ctx=vm.createContext({URL,AbortController,Date,Promise,console,
  location:{href:'https://walla3ha.com/customer?app_version=1.1',pathname:'/customer',replace:url=>navigations.push(url)},
  navigator:{onLine:true},document:{visibilityState:'visible',addEventListener(){},activeElement:null,querySelector:s=>s.includes('meta')?{content:'old123'}:null},
  window:{addEventListener(){}},sessionStorage:{getItem:k=>storage.get(k)||null,setItem:(k,v)=>storage.set(k,v),removeItem:k=>storage.delete(k)},
  merchantId:4,shopAnywhere:false,catalogOrder:false,kind:'products',serviceKey:'products',productSearch:'',selectedCatalogProductId:1,cartBudgets:{},tab:'customer',token:'',cachedReadOnly:false,expectedAppRole:'customer',customerView:'home',categoryFilter:'الكل',homeQuery:'',cart:{},checkoutFiles:{},submitting:false,trackingOrderId:null,state:{orders:[]},
  fetchJsonWithTimeout:async()=>{requests++;if(reject)throw Error('offline');return {response:{ok:true},data:{revision:remote}}},
  fetchCustomerSnapshot:async()=>new Promise(r=>{resolvePage=()=>r({response:{ok:true},data:'<meta name="wallaha-ui-revision" content="'+remote+'">'})}),
  setTimeout(fn,ms){timer={fn,ms};return 1},clearTimeout(){},showStartupLoading(){},
 });
 vm.runInContext(code,ctx);
 return {ctx,navigations,get timer(){return timer},get requests(){return requests},set reject(v){reject=v},set remote(v){remote=v},finish:()=>resolvePage(),ready:()=>!!resolvePage};
}
const drain=async()=>{for(let n=0;n<10;n++)await Promise.resolve()};
(async()=>{
 let s=setup();s.remote='old123';await s.ctx.checkAppUpdate();assert.equal(s.navigations.length,0);assert.ok(s.timer);
 s=setup();let p=s.ctx.checkAppUpdate();await drain();assert.ok(s.ready());s.finish();await p;assert.equal(s.navigations.length,1);assert.match(s.navigations[0],/ui_revision=new123/);
 for(const field of ['submitting','customerView','checkoutFiles']){
  s=setup();s.ctx[field]=({submitting:true,customerView:'checkout',checkoutFiles:{proof:{}}})[field];
  await s.ctx.checkAppUpdate();assert.equal(s.navigations.length,0,field+' prevents reload');
 }
 s=setup();p=s.ctx.checkAppUpdate();await drain();s.ctx.customerView='checkout';s.finish();await p;assert.equal(s.navigations.length,0,'Recheck safety after download');
 s=setup();s.reject=true;await s.ctx.checkAppUpdate();assert.ok(s.timer);s.reject=false;p=s.timer.fn();await drain();s.finish();await p;assert.equal(s.navigations.length,1,'Retry succeeds without reopening');
 s=setup();p=s.ctx.checkAppUpdate();const second=s.ctx.checkAppUpdate();assert.equal(s.requests,1,'Deduplicate update checks');await drain();s.finish();await Promise.all([p,second]);
 s=setup();s.ctx.cart={1:2};s.ctx.state={user:{id:7},orders:[{status:'assigned'}]};s.ctx.customerView='store';p=s.ctx.checkAppUpdate();await drain();s.finish();await p;assert.equal(s.navigations.length,1,'Cart and an existing order do not indefinitely block updates while browsing');s.ctx.cart={};s.ctx.merchantId=0;s.ctx.customerView='home';s.ctx.restoreCustomerUiResume();assert.equal(s.ctx.cart[1],2);assert.equal(s.ctx.merchantId,4);assert.equal(s.ctx.customerView,'store');
 s=setup();s.ctx.cart={1:2};s.ctx.state={user:{id:7}};assert.ok(s.ctx.saveCustomerUiResume());s.ctx.cart={};s.ctx.state={user:{id:8}};s.ctx.restoreCustomerUiResume();assert.equal(Object.keys(s.ctx.cart).length,0,'Never restore another account cart');
 s=setup();s.ctx.sessionStorage.setItem=()=>{throw Error('storage full')};p=s.ctx.checkAppUpdate();await drain();s.finish();await p;assert.equal(s.navigations.length,0,'Do not discard a cart if it cannot be saved');
 console.log('Passed UI revision, retry, single-flight, and order/cart/form protection.');
})().catch(e=>{console.error(e);process.exitCode=1});
