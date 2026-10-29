const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const html=fs.readFileSync('index.html','utf8'),sw=fs.readFileSync('sw.js','utf8');
assert.ok(!html.includes("controllerchange',()=>location.reload()"));assert.ok(!sw.includes('self.skipWaiting()'));assert.ok(!sw.includes('self.clients.claim()'));
const stack=[{wallaCustomerApp:true,wallaCustomerView:'checkout',wallaCustomerDepth:1}],history={state:stack[0],replaceState(s){stack[stack.length-1]=s;this.state=s},pushState(s){stack.push(s);this.state=s},go(delta){stack.splice(stack.length+delta);this.state=stack.at(-1);ctx.deliveryStep=this.state.wallaDeliveryStep}};
const ctx=vm.createContext({history,tab:'customer',deliveryStep:1,shipmentType:'ملابس',chosenPickup:{lat:29.8},chosenPoint:{latitude:29.9},checkoutDraft:{pickup:'pickup',address:'address'},$:()=>null,saveCheckoutDraft(){},appNotify(){},render(){},refreshCheckoutQuote(){},window:{scrollTo(){}},location:{href:'https://walla3ha.com/customer'}});
vm.runInContext(html.slice(html.indexOf('function goDeliveryStep('),html.indexOf('\nfunction ',html.indexOf('function goDeliveryStep(')+1)),ctx);
ctx.goDeliveryStep(2);ctx.goDeliveryStep(3);assert.equal(stack.length,3);assert.equal(ctx.deliveryStep,3);ctx.goDeliveryStep(1);assert.equal(ctx.deliveryStep,1);assert.equal(stack.length,1);
console.log('Passed: update cannot force reload, wizard history returns to requested step, map library preloaded.');
assert.ok(html.includes('as="script" href="/leaflet.js?v=1.9.4"'));
