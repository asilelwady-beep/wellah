const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const html=fs.readFileSync('index.html','utf8');
function code(from,to){return html.slice(html.indexOf(from),html.indexOf(to,html.indexOf(from)));}
const order={id:6,driver_id:3,status:'returning',driver_name:'Test driver',driver_vehicle:'سكوتر',driver_photo:'profile.jpg',driver_phone:'mobile'};
let dialog, polls=0;
const closeButton={}, history={state:{},pushState(s){this.state=s;},replaceState(s){this.state=s;},back(){this.state={};}};
const ctx=vm.createContext({state:{user:{id:2},orders:[order]},tab:'driver',document:{
  createElement:()=>({dataset:{},setAttribute(){},querySelector:()=>closeButton,
    addEventListener(type,cb){if(type==='close')this.onClose=cb;},showModal(){this.open=true;},
    close(){this.open=false;this.onClose();},remove(){dialog=null;}}),body:{append(el){dialog=el;}}
},$:id=>id==='trip-conversation'?dialog:null,history,window:{addEventListener(){},removeEventListener(){}},
  saveChatDrafts(){},pollOrderChat(){polls++;},chatSending:new Set(),chatDrafts:new Map(),chatCache:new Map(),
  chatPushButton:()=>'',esc:String,driverIcon:()=>'',driverAvatar:photo=>photo,contactLink:s=>s});
vm.runInContext(code('function chatKey(','function saveChatDrafts('),ctx);
vm.runInContext(code('function customerDriverCard(','function trackingPanel('),ctx);
for(const role of ['driver','customer']){
  ctx.tab=role;
  assert.match(ctx.orderChatButton(order),/openOrderConversation\(6\)/);
  ctx.openOrderConversation(6);
  assert.equal(dialog.open,true);assert.match(dialog.innerHTML,/id="chat-input-6"/);
  assert.match(dialog.innerHTML,/sendOrderChat\(6\)/);
  assert.ok(!html.includes('href="#order-chat-'));
  closeButton.onclick();assert.equal(dialog,null);
}
assert.equal(polls,2);
assert.match(ctx.customerDriverCard(order),/Test driver/);
assert.match(ctx.customerDriverCard(order),/profile.jpg/);
assert.match(ctx.customerDriverCard(order),/سكوتر/);
assert.equal(ctx.customerDriverCard({...order,status:'offered'}),'');
ctx.openOrderConversation(6);order.status='delivered';ctx.syncOrderConversation();
assert.equal(dialog,null);assert.equal(ctx.orderChatButton(order),'');
console.log('Passed opening and closing chat on both sides, return-trip messaging, profile rendering and order completion.');
