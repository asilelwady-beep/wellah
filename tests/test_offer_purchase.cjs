const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const html=fs.readFileSync('index.html','utf8');
assert.ok(html.includes('async function addOfferProduct('),'Offer buttons must use the linked store product');
const code=html.slice(html.indexOf('function bannerPurchaseProduct('),html.indexOf('const OFFER_SLIDE_MS'));
const p={id:12,merchant_id:4,active:1,stock:4,price:20},ctx=vm.createContext({
 state:{banner_offers:[{purchase:{product_id:12,merchant_id:4,available:true}}],merchants:[{id:4,active:1}]},
 searchableProducts:()=>[p],cart:{},window:{appNotify(){}},hasCatalogPrice:p=>p.price>0,
 addProductToCart:async(id,buy,qty)=>{ctx.cart[id]=(ctx.cart[id]||0)+qty;ctx.selected=id},
 selectMerchant:id=>ctx.merchant=id,
});vm.runInContext(code,ctx);
(async()=>{
 await ctx.addOfferProduct(0);assert.equal(ctx.cart[12],1);assert.equal(ctx.selected,12);
 ctx.openOfferMerchant(0);assert.equal(ctx.merchant,4);
 p.merchant_id=9;await ctx.addOfferProduct(0);assert.equal(ctx.cart[12],1,'Never add a product moved to another merchant');
 p.merchant_id=4;p.stock=0;await ctx.addOfferProduct(0);assert.equal(ctx.cart[12],1,'Out of stock cannot be bought');
 p.stock=4;ctx.state.banner_offers[0].purchase.available=false;await ctx.addOfferProduct(0);assert.equal(ctx.cart[12],1);
 // Exercise the existing cart implementation, including the cross-store confirmation.
 p.active=1;p.stock=4;ctx.state.banner_offers[0].purchase.available=true;
 ctx.cart={99:1};ctx.cartBudgets={99:10};ctx.catalogOrder=false;ctx.merchantId=8;
 ctx.cartCount=()=>Object.values(ctx.cart).reduce((a,b)=>a+b,0);
 ctx.soldByWeight=()=>false;ctx.validProductQuantity=()=>true;ctx.render=()=>{};ctx.alert=()=>{};
 ctx.window.appConfirm=async()=>false;
 const start=html.indexOf('async function addProductToCart(');
 assert.notEqual(start,-1);vm.runInContext(html.slice(start,html.indexOf('\n',start)),ctx);
 await ctx.addOfferProduct(0);assert.deepEqual(ctx.cart,{99:1});assert.equal(ctx.merchantId,8);
 ctx.window.appConfirm=async()=>true;
 await ctx.addOfferProduct(0);assert.equal(ctx.cart[12],1);assert.equal(ctx.cart[99],undefined);
 assert.equal(ctx.merchantId,4);assert.equal(ctx.shopAnywhere,false);
 console.log('Passed offer identity, stock and real cart cross-store confirmation.');
})().catch(e=>{console.error(e);process.exitCode=1});
