/* Focused browser-component logic check. No browser or network dependency. */
const fs=require('fs'),vm=require('vm'),path=require('path'),assert=require('assert');
const base=path.join(__dirname,'../smartsehat_workspace/consultation/components');
function script(name){return fs.readFileSync(path.join(base,name,'index.html'),'utf8').match(/<script>([\s\S]*?)<\/script>/)[1];}
const elements={},messages=[];let tick=0,onRender;
const ctx={parent:{postMessage:m=>messages.push(m)},document:{getElementById:id=>elements[id]??=( {disabled:false,textContent:'',value:''})},window:{addEventListener:(name,fn)=>onRender=fn},Date:{now:()=>tick},setInterval:()=>1,clearInterval:()=>{}};
vm.createContext(ctx);vm.runInContext(script('breath'),ctx);
onRender({data:{type:'streamlit:render',args:{case_id:'test',threshold:40}}});
elements.start.onclick();for(let i=0;i<46;i++)elements.tap.onclick();
assert.equal(messages.filter(m=>m.type==='streamlit:setComponentValue').length,0,'No partial-minute result');
tick=60000;vm.runInContext('render()',ctx);
let result=messages.find(m=>m.type==='streamlit:setComponentValue');assert.equal(result.value.count,46);assert.equal(result.value.complete,true);assert.equal(result.value.duration_seconds,60);assert.equal(result.value.case_id,'test');assert.equal(elements.tap.disabled,true);
elements.reset.onclick();assert.equal(messages.at(-1).value,null);
new vm.Script(script('speech'));
console.log('Breath timer: full-minute gate, count, completion, reset passed. Speech component syntax passed.');
