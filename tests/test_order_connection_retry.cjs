const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const html=fs.readFileSync('index.html','utf8');
const ctx=vm.createContext({token:'test',tab:'customer',refresh:async()=>{},setTimeout:fn=>{ctx.delays++;fn()},delays:0,calls:0});
vm.runInContext(html.split('\n').find(l=>l.startsWith('async function apiRequest(')),ctx);
(async()=>{
 const payload={client_request_id:'same-order-id'};const bodies=[];
 ctx.fetchJsonWithTimeout=async(url,options)=>{ctx.calls++;bodies.push(options.body);if(ctx.calls<=4)throw new TypeError('offline');return {response:{ok:true},data:{id:12}}};
 assert.equal((await ctx.apiRequest('order',payload)).id,12);assert.equal(ctx.calls,5);assert.equal(new Set(bodies).size,1);
 ctx.calls=0;ctx.delays=0;ctx.fetchJsonWithTimeout=async()=>{ctx.calls++;return {response:{ok:true},data:{id:13}}};
 assert.equal((await ctx.apiRequest('order',payload)).id,13);assert.equal(ctx.delays,0);assert.equal(ctx.calls,1);
 ctx.fetchJsonWithTimeout=async()=>({response:{ok:false,status:400},data:{error:'invalid'}});
 await assert.rejects(ctx.apiRequest('order',payload),/invalid/);assert.equal(ctx.delays,0);
 console.log('Passed: reconnect after four failures, same idempotency key, no success delay, validation errors stop retries.');
})().catch(e=>{console.error(e);process.exitCode=1});
