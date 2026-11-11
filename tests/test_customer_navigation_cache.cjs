const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const worker=fs.readFileSync('sw.js','utf8');
async function openHome(path,mode){
  const handlers={},writes=[];
  const url='https://walla3ha.com'+path;
  const cached=new Response('old banner');
  const context=vm.createContext({URL,Request,Response,AbortController,setTimeout,clearTimeout,
    self:{location:new URL(url),addEventListener:(name,fn)=>handlers[name]=fn},
    caches:{open:async()=>({match:async()=>cached,put:async(r,s)=>writes.push(await s.text())}),match:async()=>cached},
    fetch:async()=>{
      if(mode==='offline')throw new Error('offline');
      return new Response(mode==='error'?'temporarily unavailable':'new banner',{status:mode==='error'?503:200});
    }});
  vm.runInContext(worker,context);
  let response,update;
  handlers.fetch({request:{method:'GET',mode:'navigate',url},respondWith:p=>response=p,waitUntil:p=>update=p});
  const text=await (await response).text();await update;
  return {text,writes};
}
(async()=>{
  for(const path of ['/','/customer?app_version=1.2']){
    assert.deepEqual(await openHome(path,'online'),{text:'new banner',writes:['new banner']});
    assert.deepEqual(await openHome(path,'offline'),{text:'old banner',writes:[]});
    assert.deepEqual(await openHome(path,'error'),{text:'old banner',writes:[]});
  }
  console.log('Customer home loads the current online page and uses the cached page on network or server failure.');
})().catch(e=>{console.error(e);process.exitCode=1});
