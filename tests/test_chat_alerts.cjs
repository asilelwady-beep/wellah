const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const html=fs.readFileSync('index.html','utf8');
const order={id:5,driver_id:7,status:'on_way'},key='11:5:7';
const elements=new Map();let tones=0,reads=0;
const ctx=vm.createContext({state:{user:{id:11},orders:[order]},token:'test',tab:'driver',liveChatStages:['on_way'],
  chatKey:()=>key,chatSeenIds:new Map(),chatUnread:new Map(),chatCache:new Map([[key,[{id:15}]]]),
  $:id=>elements.get(id),document:{visibilityState:'visible',createElement:()=>({setAttribute(){},remove(){elements.delete(this.id);}}),
    body:{append(el){elements.set(el.id,el);}}},
  playMessageTone(){tones++;},chatAPI:async request=>{assert.equal(request.mode,'read');assert.equal(request.through,15);reads++;return {unread_count:0};}});
vm.runInContext(html.slice(html.indexOf('function drawChatNotifications('),html.indexOf('let pushBoundSession')),ctx);
(async()=>{
  const incoming={unread_count:1,latest_incoming:15};
  await ctx.updateChatUnread(order,key,incoming);
  assert.match(elements.get('incoming-chat-toast').innerHTML,/فتح المحادثة/);
  await ctx.updateChatUnread(order,key,incoming);assert.equal(tones,1);assert.equal(reads,0);
  elements.set('trip-conversation',{dataset:{key}});
  ctx.document.visibilityState='hidden';await ctx.updateChatUnread(order,key,incoming);assert.equal(reads,0);
  ctx.document.visibilityState='visible';await ctx.updateChatUnread(order,key,incoming);
  assert.equal(reads,1);assert.ok(!elements.has('incoming-chat-toast'));
  const handlers={};let notification,url;
  const sw=vm.createContext({self:{location:{origin:'https://walla3ha.com'},registration:{showNotification(title,data){notification={title,data};}},
    addEventListener:(k,f)=>{handlers[k]=f;}},URL,clients:{openWindow(href){url=href;}}});
  vm.runInContext(fs.readFileSync('sw.js','utf8'),sw);
  handlers.push({data:{json:()=>({role:'driver',order:5})},waitUntil(){}});
  assert.match(notification.title,/العميل/);handlers.notificationclick({notification:{data:notification.data.data,close(){}},waitUntil(){}});
  assert.equal(url,'https://walla3ha.com/driver?chat_order=5');
  console.log('Passed first unread alert, sticky and deduplicated display, visible-only reads, notification click to order.');
})().catch(e=>{console.error(e);process.exitCode=1});
