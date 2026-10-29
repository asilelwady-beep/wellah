const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const html=fs.readFileSync('index.html','utf8');
const nodes={'quote-fee':{textContent:'',innerHTML:''},'quote-refresh':{},payment:{value:'cash'}};
let responses=[],calls=0;
const context=vm.createContext({window:{addEventListener(){}},document:{visibilityState:'visible',addEventListener(){}},setInterval(){},state:{user:{id:1}},customerView:'checkout',kind:'delivery',deliveryStep:3,previewMode:false,deliveryQuote:null,deliveryQuoteRouteKey:'',points:{latitude:29.8,longitude:31.2,pickup_lat:29.81,pickup_lon:31.21},quotePoints(){return context.points},resolveOrderLocation:async()=>{},api:async()=>{calls++;return await responses.shift()()},$:id=>nodes[id],cartSubtotal:()=>0,money:n=>String(n)+' ج',refreshCheckoutTotal(){},appNotify(){},esc:s=>s,kmPricing:()=>true});
vm.runInContext(html.split('\n').find(l=>l.startsWith('function quoteOrderDiscountCents(')),context);
vm.runInContext(html.slice(html.indexOf('function discountPriceMarkup('),html.indexOf('function checkoutDiscountBreakdown(')),context);
vm.runInContext(html.slice(html.indexOf('let routeQuoteSequence='),html.indexOf('function customerFeaturedDiscountNotice(')),context);
const quote=(fee=30)=>({fee,original_fee:30,quote_token:'signed',expires:Date.now()/1000+600});
(async()=>{
 responses.push(async()=>quote());await context.calculateDeliveryQuote();assert.equal(calls,1);
 responses.push(async()=>quote(15));await context.calculateDeliveryQuote();assert.equal(calls,2);assert.match(nodes['quote-fee'].innerHTML,/<s /);assert.match(nodes['quote-fee'].innerHTML,/15 ج/);
 let done;responses.push(()=>new Promise(r=>done=r));const a=context.calculateDeliveryQuote(),b=context.calculateDeliveryQuote();await new Promise(r=>setImmediate(r));assert.equal(calls,3);done(quote(0));await Promise.all([a,b]);assert.match(nodes['quote-fee'].innerHTML,/0 ج/);
 responses.push(async()=>{throw Error('تعذر تحديث السعر')});await assert.rejects(context.calculateDeliveryQuote());assert.equal(nodes['quote-fee'].textContent,'تعذر تحديث السعر');assert.equal(nodes['quote-refresh'].disabled,false);
 responses.push(async()=>quote(20));await context.calculateDeliveryQuote();assert.match(nodes['quote-fee'].innerHTML,/20 ج/);
 let oldDone;responses.push(()=>new Promise(r=>oldDone=r));const old=context.calculateDeliveryQuote();await new Promise(r=>setImmediate(r));context.points.latitude=29.9;responses.push(async()=>quote(25));await context.calculateDeliveryQuote();oldDone(quote(1));await old;assert.match(nodes['quote-fee'].innerHTML,/25 ج/);
 console.log('Passed: latest discount overrides memory, crossed original, zero final, in-flight deduplication, error and recovery, stale route ignored.');
})().catch(e=>{console.error(e);process.exitCode=1});
