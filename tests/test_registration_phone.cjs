const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const html = fs.readFileSync('index.html','utf8');
const field = html.match(/<input id="reg-phone"[^>]*>/)[0];
assert.match(field, /inputmode="numeric"/);
assert.match(field, /maxlength="11"/);
assert.match(field, /oninput="normalizeRegistrationPhoneInput\(this\)"/);
assert.match(field, /onpaste="pasteRegistrationPhone\(event,this\)"/);

const js = fs.readFileSync('features.js','utf8');
const errors = [];
const ctx = vm.createContext({Event,showRegisterError:error=>errors.push(error.message)});
vm.runInContext(js.slice(js.indexOf('function registrationDigits('),js.indexOf('function showRegisterError(')),ctx);
function input(value,start=value.length,end=start){
  return {value,selectionStart:start,selectionEnd:end,
    setSelectionRange(start,end){this.selectionStart=start;this.selectionEnd=end;},
    setRangeText(text,start,end){this.value=this.value.slice(0,start)+text+this.value.slice(end);},
    dispatchEvent(){ctx.normalizeRegistrationPhoneInput(this);}
  };
}
function paste(el,text){
  ctx.pasteRegistrationPhone({clipboardData:{getData:()=>text},preventDefault(){}},el);
}
const el=input('٠١*٢#',4);
ctx.normalizeRegistrationPhoneInput(el);
assert.equal(el.value,'012');
assert.equal(el.selectionStart,3);
const number=input('01000000000',0,11);
paste(number,'۰۱۱ ۲۳۴۵ ۶۷۸۹');
assert.equal(number.value,'01123456789');
const blank=input('');
paste(blank,'+201012345678');
assert.equal(blank.value,'');
assert.equal(errors.length,1);
console.log('Passed registration keypad, Arabic and Persian digits, symbols, caret, paste and international number rejection.');
