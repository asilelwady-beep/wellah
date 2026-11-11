const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const html=fs.readFileSync('index.html','utf8');
const start=html.indexOf('// Live customer data');
assert.ok(start>=0,'Customer changes need an independent live refresh loop');
const code=html.slice(start,html.indexOf('// End live customer data',start));
function setup(){
 let timer,release,failed=false,refreshes=0,renders=0,calls=0;
 const ctx=vm.createContext({Number,Map,Set,Array,Promise,Date,
   state:{user:{id:7,role:'customer'},customer_live_revision:10,products:[{id:1,price:20}],draft_catalog:[{id:1,price:20}],banner_offers:[]},
   tab:'customer',expectedAppRole:'customer',cachedReadOnly:false,cachedCatalogActive:false,previewMode:false,submitting:false,refreshInFlight:null,cart:{1:2},cartBudgets:{},customerView:'home',kind:'products',
   document:{visibilityState:'visible',activeElement:{id:'search',value:'سكر'},querySelector:()=>null,addEventListener(){}},window:{addEventListener(){}},
   setTimeout(fn,ms){timer={fn,ms};return 1},clearTimeout(){},
   fetchJsonWithTimeout:async()=>{calls++;if(failed)throw Error('offline');return new Promise(resolve=>{release=data=>resolve({response:{ok:true},data})})},
   refresh:async()=>{refreshes++},checkAppUpdate:async()=>{},saveCustomerCatalogCache(){},renderLiveCustomer(){renders++},
 });
 vm.runInContext(code,ctx);
 const renderLiveCustomer=ctx.renderLiveCustomer;
 ctx.renderLiveCustomer=()=>{renders++};
 return {ctx,renderLiveCustomer,send:data=>release(data),get timer(){return timer},get calls(){return calls},get refreshes(){return refreshes},get renders(){return renders},set failed(v){failed=v}};
}
const delta={ui_revision:'new123',changed:true,reload_state:false,customer_live_revision:11,customer_catalog_revision:'catalog-new',changed_product_ids:[1],products:[{id:1,price:23}],draft_catalog:[{id:1,price:23}],banner_offers:[{title:'Live offer'}]};
(async()=>{
 let s=setup(),p=s.ctx.pollCustomerLive();s.send(delta);await p;
 assert.equal(s.ctx.state.products[0].price,23);assert.equal(s.ctx.state.banner_offers[0].title,'Live offer');assert.equal(s.ctx.cart[1],2);assert.equal(s.renders,1,'Focused search does not block new data');assert.ok(s.timer.ms<=2000);
 p=s.ctx.pollCustomerLive();s.send({...delta,customer_live_revision:12,products:[],draft_catalog:[]});await p;assert.equal(s.ctx.state.products.length,0,'Removed products leave the catalog');
 s=setup();p=s.ctx.pollCustomerLive();const other=s.ctx.pollCustomerLive();assert.equal(s.calls,1);s.send({...delta,reload_state:true});await Promise.all([p,other]);assert.equal(s.refreshes,1);
 s=setup();p=s.ctx.pollCustomerLive();s.ctx.state={...s.ctx.state,customer_live_revision:15};s.send(delta);await p;assert.equal(s.ctx.state.customer_live_revision,15,'Late response cannot overwrite newer refresh');
 s=setup();s.failed=true;await s.ctx.pollCustomerLive();assert.ok(s.timer);s.failed=false;p=s.timer.fn();s.send(delta);await p;assert.equal(s.ctx.state.products[0].price,23,'Reconnect catches up without reopening');
 s=setup();s.ctx.submitting=true;await s.ctx.pollCustomerLive();assert.equal(s.calls,0);assert.equal(s.ctx.state.customer_live_revision,10,'Pending purchase is preserved, then next poll catches up');
 s=setup();let focused=false,selection,scroll;
 s.ctx.document.activeElement={id:'search',value:'سكر',selectionStart:2,selectionEnd:2,matches:()=>true,getAttribute:()=>null};
 const replacement={value:'',focus:()=>{focused=true},setSelectionRange:(a,b)=>{selection=[a,b]}};
 s.ctx.document.getElementById=()=>replacement;s.ctx.render=()=>{};s.ctx.window.scrollY=123;s.ctx.window.scrollTo=p=>{scroll=p.top};
 s.renderLiveCustomer();assert.equal(replacement.value,'سكر');assert.ok(focused);assert.deepEqual(selection,[2,2]);assert.equal(scroll,123);
 s=setup();let totalCalls=0,quoteCalls=0;const total={textContent:''};
 s.ctx.customerView='checkout';s.ctx.document.querySelector=()=>({});s.ctx.document.querySelectorAll=()=>[{dataset:{productId:'1'},querySelector:()=>total}];
 s.ctx.searchableProducts=()=>[{id:1,price:23}];s.ctx.productPrice=p=>p.price;s.ctx.money=n=>String(n);
 s.ctx.refreshCheckoutTotal=()=>{totalCalls++};s.ctx.refreshCheckoutQuote=()=>{quoteCalls++};s.ctx.render=()=>{throw Error('Checkout form must stay mounted')};
 s.renderLiveCustomer();assert.equal(total.textContent,'46');assert.equal(totalCalls,1);assert.equal(quoteCalls,1);
 console.log('Live updates: price/offer delta, focused search, deletion, single flight, races, reconnect and checkout guard passed.');
})().catch(e=>{console.error(e);process.exitCode=1});
