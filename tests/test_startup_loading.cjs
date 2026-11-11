const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const html = fs.readFileSync('index.html', 'utf8');
const markup = html.slice(html.indexOf('<div id="app">'), html.indexOf('</main>'));
assert.match(markup, /id="startup-loading"[\s\S]*class="app-action-spinner"/);
assert.ok(html.indexOf(markup) < html.indexOf('<script src="/maps.js'));

function scenario(cache, authenticated=false){
  const views = [];
  const spinner = {}, button = {}, label = {};
  const app = {replaceChildren(node){node.parentNode=this;}};
  const panel = {parentNode:app,querySelector(s){return {'.app-action-spinner':spinner,button,'[data-startup-message]':label}[s];}};
  let complete,fail;
  const response = new Promise((resolve,reject)=>{complete=resolve;fail=reject;});
  const ctx = vm.createContext({
    window:{},URLSearchParams,document:{getElementById:()=>panel,visibilityState:'visible'},navigator:{onLine:true},expectedAppRole:'customer',token:authenticated?'test':'',
    location:{pathname:'/customer'},tab:'customer',previewMode:false,state:null,cachedCatalogActive:false,cachedReadOnly:false,offline:false,connectionMessage:'',
    $:()=>app,esc:String,readCustomerCatalogCache:()=>cache,saveCustomerCatalogCache(){},customerPreviewState:c=>c,
    fetchJsonWithTimeout:()=>response,fetchCustomerSnapshot:()=>response,clearStartupRetry(){},scheduleStartupRetry(){},acceptSharedRideDestination(){},
    restoreCustomerUiResume(){},renderLiveCustomer(){ctx.render()},render(){panel.parentNode=null;views.push(ctx.connectionStatusHTML(ctx.connectionMessage));}
  });
  function run(start,end){vm.runInContext(html.slice(html.indexOf(start),html.indexOf(end,html.indexOf(start))),ctx);}
  run('function mergeCustomerCatalog(', 'async function fetchCustomerSnapshot');
  run('const startupLoadingPanel=', 'let startupRetryTimer');
  run('async function browsePreview(', 'function leavePreview');
  run('async function performRefresh(', 'let driverAudio=');
  return {ctx,panel,views,spinner,button,label,complete,fail};
}

(async()=>{
  for(const cache of [null,{products:[{id:1}]}]){
    const s=scenario(cache);
    const pending=s.ctx.performRefresh();
    if(cache){
      assert.equal(s.panel.parentNode,null);
      assert.match(s.views[0],/app-action-spinner/);
    }else{
      assert.ok(s.panel.parentNode);
      assert.match(s.label.textContent,/المنتجات/);
      assert.equal(s.spinner.hidden,false);
    }
    s.complete({response:{ok:true},data:{products:[{id:2}]}});
    await pending;
    assert.equal(s.panel.parentNode,null);
    assert.equal(s.views.at(-1),'');
    assert.equal(s.ctx.state.products[0].id,2);
  }
  const ready=scenario({products:[{id:1}]},true);
  Object.assign(ready.ctx,{appTokenKey:'test',localStorage:{setItem(){throw new Error('QuotaExceededError')},removeItem(){}},
    loadCustomerAddress(){},checkShiftSecondReminder(){},cleanupCustomerArchive(){},syncDriverAutomation(){},
    lastSeenRole:'customer',lastSeenOrderId:null,customerView:'home',keepMerchantEditorOnRefresh:()=>false});
  const success=ready.ctx.performRefresh();
  ready.complete({response:{ok:true},data:{user:{id:7,role:'customer'},orders:[],products:[{id:3}],categories:[],merchants:[],draft_catalog:[]}});
  await success;
  assert.equal(ready.ctx.connectionMessage,'','Local cache failure must not report a network failure');
  assert.equal(ready.ctx.cachedReadOnly,false);
  assert.equal(ready.ctx.cachedCatalogActive,false);
  assert.equal(ready.ctx.state.products[0].id,3);
  const signed=scenario(null,true);
  const pending=signed.ctx.performRefresh();
  assert.equal(signed.spinner.hidden,false);
  signed.fail(new Error('offline'));
  await pending;
  assert.equal(signed.spinner.hidden,false);
  assert.equal(signed.button.hidden,false);
  const guest=scenario(null);
  const loading=guest.ctx.performRefresh();
  guest.ctx.navigator.onLine=false;
  guest.fail(new Error('offline'));
  await loading;
  assert.ok(guest.panel.parentNode);
  assert.match(guest.label.textContent,/الإنترنت غير متاح/);
  assert.equal(guest.spinner.hidden,false);
  assert.equal(guest.button.hidden,false);
  const failedCached=scenario({products:[{id:1}]});
  const update=failedCached.ctx.performRefresh();failedCached.fail(new Error('offline'));await update;
  assert.doesNotMatch(failedCached.views.at(-1),/catalog-updating|app-action-spinner/);
  assert.match(failedCached.views.at(-1),/إعادة المحاولة/);
  console.log('Passed initial loading markup, slow catalog, cached browsing, error recovery, and account loading.');
})().catch(e=>{console.error(e);process.exitCode=1;});
