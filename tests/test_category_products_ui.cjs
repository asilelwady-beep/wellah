const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const root = path.join(__dirname, '..');
const index = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
const features = fs.readFileSync(path.join(root, 'features.js'), 'utf8');
function definition(source, name) {
  const start = source.search(new RegExp('(?:async )?function '+name+'\\('));
  assert.ok(start >= 0, name);
  const end = source.indexOf('\n', source.indexOf('\nfunction ', start) + 1);
  const next = source.slice(start).search(/\n(?:async )?function |\nconst |\nlet /);
  return source.slice(start, next < 0 ? source.length : start + next);
}
const context = vm.createContext({
  state: {
    products: [{id:1,name:'أرز',category:'سوبر ماركت',price:40,stock:100}, {id:2,name:'خيار',category:'خضار',price:20,stock:100}],
    archived_products: [{id:3,name:'أرز محذوف',category:'سوبر ماركت',price:35}],
    categories: [{name:'سوبر ماركت',active:true},{name:'خضار',active:true},{name:'قسم فارغ',active:true}],
    merchants: [{id:1,name:'مخزن السوق',category:'سوبر ماركت',active:true},{id:2,name:'مخزن الخضار',category:'خضار',active:true}],
    staff_permissions: null,
  },
  adminProductCategory: '', adminProductTrash:false, adminProductQuery:'',adminProductLimit:40,adminPage:'admin-categories',
  matchesSearch:(value,query)=>value.includes(query),hasCatalogPrice:p=>p.price>0,
  esc:value=>String(value??'').replaceAll('&','&amp;').replaceAll('"','&quot;').replaceAll('<','&lt;').replaceAll('>','&gt;'),
  options:(values,selected)=>values.map(v=>`<option ${v===selected?'selected':''}>${v}</option>`).join(''),
  render(){},
});
for (const name of ['adminProductItems','adminProductRows']) vm.runInContext(definition(index,name),context);
for (const name of ['adminNeedsProducts','openAdminPage','openAdminCategoryProducts','changeAdminProductCategory','toggleAdminProductTrash','adminProductStudio','archivedAdminProductRows','adminCatalogCards']) vm.runInContext(definition(features,name),context);
(async()=>{
  await context.openAdminCategoryProducts('خضار');
  assert.equal(context.adminPage,'admin-products');
  assert.deepEqual(Array.from(context.adminProductItems(),p=>p.id),[2]);
  let html=context.adminProductStudio();
  assert.ok(html.includes('منتجات خضار'));
  assert.ok(html.includes('id="p-category" disabled'));
  assert.ok(html.includes('<option selected>خضار</option>'));
  assert.ok(html.includes('مخزن الخضار')&&!html.includes('مخزن السوق'));
  assert.ok(html.includes('حذف المنتج'));
  context.adminProductQuery='أرز';
  assert.equal(context.adminProductItems().length,0);
  context.changeAdminProductCategory('سوبر ماركت');
  context.toggleAdminProductTrash();
  assert.deepEqual(Array.from(context.adminProductItems(),p=>p.id),[3]);
  html=context.adminProductRows();
  assert.ok(html.includes('استرجاع المنتج')&&!html.includes('price-3'));
  context.changeAdminProductCategory('قسم فارغ');
  assert.equal(context.adminProductItems().length,0);
  context.toggleAdminProductTrash();
  html=context.adminProductStudio();
  assert.ok(html.includes('إلى قسم فارغ'));
  await context.openAdminPage('admin-products');
  assert.equal(context.adminProductCategory,'');
  assert.equal(context.adminProductTrash,false);
  assert.equal(context.adminProductItems().length,2);
  html=context.adminCatalogCards('categories');
  assert.ok(html.includes('فتح منتجات سوبر ماركت')&&html.includes('عرض منتجات القسم'));
  console.log('Passed: category navigation, exact filtering, scoped search, empty categories, creation defaults, delete/restore controls, and all-products reset.');
})().catch(e=>{console.error(e);process.exitCode=1});
