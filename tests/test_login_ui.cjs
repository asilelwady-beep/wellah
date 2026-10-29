const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync('index.html','utf8'),start=source.indexOf('let loginBusy=false;'),end=source.indexOf('\nasync function ',source.indexOf('async function login(){')+1);
const next=end<0?source.indexOf('\nfunction ',start):end;
const phone={value:'owner',focus(){}},pass={value:'password',focus(){}};
const button={innerHTML:'دخول',isConnected:true,setAttribute(){},removeAttribute(){}};
const listeners={},calls=[];let release;
const ctx=vm.createContext({document:{addEventListener:(k,f)=>listeners[k]=f,querySelector:()=>button},$:id=>id==='login-phone'?phone:id==='login-pass'?pass:{value:'security'},expectedAppRole:'admin',alert:()=>{},localStorage:{setItem(){}},appTokenKey:'test',refresh:async()=>{},api:async(path,data)=>{calls.push({path,data});await new Promise(r=>release=r);return {token:'test'}},adminSecuritySetupView(){}});
vm.runInContext(source.slice(start,next),ctx);
(async()=>{
 let prevented=false;listeners.keydown({key:'Enter',target:{id:'login-pass'},preventDefault(){prevented=true}});
 assert.ok(prevented);assert.equal(calls.length,1);assert.equal(button.disabled,true);
 listeners.keydown({key:'Enter',target:{id:'login-pass'},preventDefault(){}});assert.equal(calls.length,1);
 release();await new Promise(r=>setImmediate(r));assert.equal(button.disabled,false);
 phone.value='';await ctx.login();assert.equal(calls.length,1);
 assert.equal(calls[0].path,'admin/login');assert.equal(calls[0].data.security_key,'security');
 console.log('Passed: Enter submits, repeated Enter deduplicates, empty credentials rejected, security key preserved, button restored.');
})().catch(e=>{console.error(e);process.exitCode=1});
