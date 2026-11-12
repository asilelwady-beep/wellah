const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const html=fs.readFileSync('index.html','utf8');
const saved=new Map();let reduced=true;
const ctx=vm.createContext({HTMLElement:class{setAttribute(k,v){this[k]=v}},customElements:{define(n,c){ctx.Banner=c}},
 matchMedia:()=>({matches:reduced}),localStorage:{getItem:k=>saved.get(k),setItem:(k,v)=>saved.set(k,v)}});
vm.runInContext(html.slice(html.indexOf('function bannerPurchaseProduct('),html.indexOf('function customerHome()')),ctx);
assert.equal(ctx.offerMotionEnabled(),false,'Respect reduced motion until the customer opts in');
const banner=new ctx.Banner(),button={setAttribute(k,v){this[k]=v}};
banner.querySelector=()=>button;banner.motionEnabled=ctx.offerMotionEnabled();banner.paintMotion();
assert.equal(button.textContent,'تحريك الصورة');
banner.toggleMotion();assert.equal(banner['data-offer-motion'],'on');assert.equal(button['aria-pressed'],'true');
assert.equal(ctx.offerMotionEnabled(),true,'An explicit opt-in survives a rerender even with reduced motion');
banner.toggleMotion();assert.equal(ctx.offerMotionEnabled(),false);assert.equal(button['aria-pressed'],'false');
saved.clear();reduced=false;assert.equal(ctx.offerMotionEnabled(),true);
ctx.localStorage.getItem=()=>{throw Error('Storage unavailable')};assert.equal(ctx.offerMotionEnabled(),true);
console.log('Passed reduced-motion defaults, explicit opt-in, persistence, stop and unavailable storage.');
