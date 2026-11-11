const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const html=fs.readFileSync('index.html','utf8');
const ctx=vm.createContext({URLSearchParams,state:null,TextDecoder,AbortController,fetch,Response,setTimeout,clearTimeout});
const start=html.indexOf('function mergeCustomerCatalog(');
assert.ok(start>=0,'Customer snapshots must restore omitted catalog rows');
vm.runInContext(html.slice(start,html.indexOf('window.appFetchWithTimeout=',start)),ctx);
const live={id:1,name:'one',image:'',catalog_preview:1,price:20};
const extra={id:2,name:'two',price:10};
const full=ctx.mergeCustomerCatalog({products:[live],draft_catalog:[extra],draft_catalog_ids:[2,1],categories:[{name:'X'}],merchants:[{id:4}],customer_catalog_revision:'a',customer_catalog_unchanged:false},null);
assert.deepEqual(Array.from(full.draft_catalog,p=>p.id),[2,1]);
assert.equal(full.draft_catalog[1].image,'/product-illustration/1.svg');
const delta=ctx.mergeCustomerCatalog({customer_catalog_revision:'a',customer_catalog_unchanged:true,banner_offers:[{title:'new'}]},full);
assert.equal(delta.products,full.products);
assert.equal(delta.draft_catalog,full.draft_catalog);
assert.equal(delta.banner_offers[0].title,'new');
assert.throws(()=>ctx.mergeCustomerCatalog({customer_catalog_unchanged:true},null));

// Download may exceed the idle deadline overall while chunks keep arriving.
// A stalled read must still be aborted, allowing the retry loop to recover.
(async()=>{
 let aborted=false;
 ctx.fetch=async(url,opts)=>{opts.signal.addEventListener('abort',()=>{aborted=true});return new Response(new ReadableStream({start(c){c.enqueue(new TextEncoder().encode('{"ok":'));setTimeout(()=>c.enqueue(new TextEncoder().encode('true')),20);setTimeout(()=>{c.enqueue(new TextEncoder().encode('}'));c.close()},40)}}))};
 assert.equal((await ctx.fetchCustomerSnapshot('/test',{},30)).data.ok,true);
 assert.equal(aborted,false);
 ctx.fetch=async(url,opts)=>{opts.signal.addEventListener('abort',()=>{aborted=true});return new Response(new ReadableStream({start(){}}))};
 await assert.rejects(ctx.fetchCustomerSnapshot('/test',{},20),{name:'TimeoutError'});
 assert.equal(aborted,true);
 console.log('Customer catalog reassembly, lightweight updates, healthy slow streams and stalled reads passed.');
})().catch(e=>{console.error(e);process.exitCode=1});
