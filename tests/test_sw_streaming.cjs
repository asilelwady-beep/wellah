const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const handlers={};let finishCache;
const cacheDone=new Promise(r=>finishCache=r);
const ctx=vm.createContext({URL,Request,Response,AbortController,setTimeout,clearTimeout,
 self:{location:new URL('https://walla3ha.com'),addEventListener:(n,f)=>handlers[n]=f},
 caches:{open:async()=>({put:()=>cacheDone,match:async()=>null}),match:async()=>null},fetch:async()=>new Response('new home')});
vm.runInContext(fs.readFileSync('sw.js','utf8'),ctx);
(async()=>{let page;handlers.fetch({request:{method:'GET',mode:'navigate',url:'https://walla3ha.com/customer'},respondWith:p=>page=p,waitUntil(){}});
 let delivered=false;page.then(()=>delivered=true);
 for(let n=0;n<30;n++)await Promise.resolve();
 try{assert.equal(delivered,true,'The page must render before its offline cache write finishes')}finally{finishCache()}
 assert.equal(await (await page).text(),'new home');
 console.log('Passed streaming navigation independent of offline cache writes.');
})().catch(e=>{console.error(e);process.exitCode=1});
