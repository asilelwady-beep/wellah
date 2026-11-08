const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const html = fs.readFileSync('index.html','utf8');
const field = html.match(/<input id="reg-phone"[^>]*>/)[0];
assert.match(field, /inputmode="tel"/);
assert.match(field, /placeholder="012XXXXXXXX"/);
assert.match(field, /oninput="normalizeRegistrationPhoneInput\(this\)"/);
assert.doesNotMatch(field, /maxlength="11"/);

const js = fs.readFileSync('features.js','utf8');
const ctx = vm.createContext({});
vm.runInContext(js.slice(js.indexOf('function registrationPhoneText('),js.indexOf('function showRegisterError(')),ctx);
const local='01200000001';
for(const text of [local,'+201200000001','+20 01200000001','0020 12 0000 0001',
                   '٢٠١٢٠٠٠٠٠٠٠١','+۲۰ ۰۱۲۰۰۰۰۰۰۰۱']){
  assert.equal(ctx.localRegistrationPhone(ctx.registrationPhoneText(text)),local,text);
}
for(const text of ['+971200000001','012345','012000000011','01400000001','1200000001']){
  assert.equal(ctx.localRegistrationPhone(ctx.registrationPhoneText(text)),'',text);
}
const el={value:'+٢٠ ١٢#',selectionStart:4,setSelectionRange(start){this.selectionStart=start;}};
ctx.normalizeRegistrationPhoneInput(el);
assert.equal(el.value,'+2012');
assert.equal(el.selectionStart,3);
console.log('Passed masked placeholder, typing, Arabic digits, national and international numbers and invalid lengths.');

async function checkPasswordValidation(){
  const values={'reg-name':'عميل اختبار','reg-phone':'+20 01100000001','reg-pass':'abcdefgh',
    'reg-confirm':'abcdefgh','reg-recovery':'إجابة تجريبية'};
  let errorField='',notice='',requests=0;
  function field(id){
    return {value:values[id],classList:{add(){}},setAttribute(){},addEventListener(){},focus(){errorField=id;},
      closest(){return null;},insertAdjacentElement(position,node){notice=node.textContent;}};
  }
  const ctx=vm.createContext({$:field,document:{getElementById:()=>true,querySelectorAll:()=>[],
    createElement:()=>({setAttribute(){}})},featureAPI:async(path,data)=>{
      requests++;
      assert.equal(data.phone,'01100000001');
    },alert(){},render(){}});
  vm.runInContext(js.slice(js.indexOf('function clearRegisterError('),js.indexOf('function forgotPassword(')),ctx);
  await ctx.register();
  assert.equal(errorField,'reg-pass');
  assert.match(notice,/10/);
  assert.equal(requests,0);
  values['reg-pass']='abcdefghij';
  await ctx.register();
  assert.equal(errorField,'reg-confirm');
  assert.equal(requests,0);
  values['reg-confirm']=values['reg-pass'];
  await ctx.register();
  assert.equal(requests,1);
  console.log('Passed short password, mismatched confirmation and valid registration.');
}
checkPasswordValidation().catch(e=>{console.error(e);process.exitCode=1;});
