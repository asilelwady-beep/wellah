const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const handlers={},timers=[],deleted=[];let activated=0;
const ctx=vm.createContext({AbortController,URL,Request,Response,
 self:{addEventListener:(n,f)=>handlers[n]=f,skipWaiting:async()=>activated++},
 caches:{keys:async()=>['unrelated','wallaha-shell-v122','wallaha-shell-v124-fresh-customer-home','wallaha-shell-v125-live-ui'],delete:async k=>deleted.push(k)},
 fetch:()=>new Promise(()=>{}),setTimeout(fn){timers.push(fn);return 1},clearTimeout(){}});
vm.runInContext(fs.readFileSync('sw.js','utf8'),ctx);
(async()=>{
 let installed;handlers.install({waitUntil:p=>installed=p});
 assert.equal(timers.length,1,'Installation has a deadline');timers[0]();await installed;
 assert.equal(activated,1,'A stalled connection cannot leave the update waiting forever');
 let active;handlers.activate({waitUntil:p=>active=p});await active;
 assert.deepEqual(deleted,['wallaha-shell-v122'],'Preserve previous shell and unrelated caches');
 console.log('Passed bounded installation, immediate worker activation and cache preservation.');
})().catch(e=>{console.error(e);process.exitCode=1});
