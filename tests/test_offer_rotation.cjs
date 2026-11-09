const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const html=fs.readFileSync('index.html','utf8');
const from=html.indexOf('const OFFER_SLIDE_MS');
const to=html.indexOf('function customerHome()',from);
assert.notEqual(from,-1);
let now=0,timer=null;
const ctx=vm.createContext({HTMLElement:class{},customElements:{define(name,cls){ctx.Carousel=cls}},
  Date:{now:()=>now},document:{hidden:false,addEventListener(){},removeEventListener(){}},
  setTimeout(fn,ms){timer={fn,ms};return 1},clearTimeout(){timer=null},esc:s=>String(s).replaceAll('<','&lt;')});
vm.runInContext(html.slice(from,to),ctx);
function mount(list){
  ctx.state={banner_offers:list.map(title=>({title,detail:''}))};
  const markup=ctx.customerOffersMarkup();
  if(!list.length){assert.equal(markup,'');return}
  assert.ok(markup.includes('walla-offers'));
  const node=new ctx.Carousel(),slides=list.map(()=>({hidden:false,setAttribute(){}}));
  node.querySelectorAll=()=>slides;
  node.querySelector=()=>null;
  node.connectedCallback();
  return {node,at:()=>slides.findIndex(s=>!s.hidden)};
}
mount([]);
const one=mount(['عرض']);assert.equal(one.at(),0);assert.equal(timer,null);one.node.disconnectedCallback();
const titles=['أ','ب','ج','د'];
let c=mount(titles);assert.equal(c.at(),0);
const advance=()=>{now+=timer.ms;timer.fn()};
advance();assert.equal(c.at(),1);
now+=2000;c.node.disconnectedCallback();assert.equal(timer,null);c=mount(titles);
assert.equal(c.at(),1);assert.equal(timer.ms,3000);
advance();assert.equal(c.at(),2);advance();assert.equal(c.at(),3);advance();assert.equal(c.at(),0);
c.node.togglePause();assert.equal(timer,null);
c.node.nextSlide();assert.equal(c.at(),1);assert.equal(timer,null);
c.node.togglePause();assert.ok(timer);c.node.disconnectedCallback();
ctx.state={banner_offers:[{title:'<img>',detail:''}]};
assert.ok(ctx.customerOffersMarkup().includes('&lt;img>'));
ctx.state={banner_offers:[{title:'عرض تجريبي',detail:'٢٥ جنيه',image:'/banner-image/test.jpg'}]};
assert.match(ctx.customerOffersMarkup(),/<img class="offer-artwork" src="\/banner-image\/test.jpg"/);
assert.ok(ctx.customerOffersMarkup().includes('عرض تجريبي · ٢٥ جنيه'));
console.log('Passed empty, single, four-offer rotation, wrap and refresh without reset.');
const features=fs.readFileSync('features.js','utf8');
const savedState={banner_editor:{revision:0,offers:[]},user:{role:'admin'}};
let sent;
const editor=vm.createContext({
  state:savedState,adminPage:'admin-banner-offers',esc:ctx.esc,
  document:{querySelector:()=>null},window:{appNotify(){}},render(){editor.adminBannerOffers()},
  async featureAPI(path,payload){assert.equal(path,'admin/banner-offers');sent=JSON.parse(JSON.stringify(payload));return {revision:1,offers:sent.offers}}
});
vm.runInContext(features.slice(features.indexOf('let bannerDraft='),features.indexOf('function adminNeedsProducts')),editor);
(async()=>{
  assert.match(editor.adminBannerOffers(),/إضافة عرض/);
  for(const title of ['أ','ب','ج','د']){editor.addBannerOffer();editor.editBannerOffer(['أ','ب','ج','د'].indexOf(title),'title',title)}
  editor.moveBannerOffer(3,-1);
  editor.editBannerOffer(1,'visible',false);
  editor.removeBannerOffer(0);
  assert.match(editor.adminBannerOffers(),/value="د"/);
  await editor.publishOffers({preventDefault(){}});
  assert.deepEqual(sent.offers.map(o=>o.title),['ب','د','ج']);assert.equal(sent.offers[0].visible,false);
  assert.equal(savedState.banner_editor.revision,1);
  console.log('Passed owner add, edit, reorder, hide, remove and publish.');
})().catch(e=>{console.error(e);process.exitCode=1});
