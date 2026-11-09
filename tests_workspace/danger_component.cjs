/* Interaction/interrupt checks only; no clinical or real-phone timing claim. */
const fs=require('fs'),vm=require('vm'),path=require('path'),assert=require('assert');
const html=fs.readFileSync(path.join(__dirname,'../smartsehat_workspace/consultation/components/danger/index.html'),'utf8');
const script=html.match(/<script>([\s\S]*?)<\/script>/)[1];
const elements={},handlers={},messages=[],played=[];let tick=0,cancelled=0,paused=0;
function element(){return {children:[],style:{},append(...items){this.children.push(...items);},replaceChildren(){this.children=[];},setAttribute(k,v){this[k]=v;},textContent:'',disabled:false};}
const context={parent:{postMessage:m=>messages.push(m)},performance:{now:()=>tick},
 document:{body:{scrollHeight:500},getElementById:id=>elements[id]??=element(),createElement:element},
 window:{addEventListener:(name,fn)=>handlers[name]=fn,speechSynthesis:{cancel:()=>cancelled++,speak:()=>{},getVoices:()=>[{lang:'en-US'}],addEventListener:()=>{}}},
 SpeechSynthesisUtterance:function(text){this.text=text;},Audio:function(source){played.push(source);this.play=()=>({catch:()=>{}});this.pause=()=>paused++;}};
vm.createContext(context);vm.runInContext(script,context);
const questions=[{key:'a',en:'Synthetic danger A',ur:'مثال'},{key:'b',en:'Synthetic danger B',ur:'مثال'}];
function render(case_id,scope,answers={}){handlers.message({data:{type:'streamlit:render',args:{case_id,scope,answers,questions,language:'ur-PK'}}});}
const outputs=()=>messages.filter(m=>m.type==='streamlit:setComponentValue');
render('case-A','scope-1');
assert.equal(elements.grid.children.length,1,'The default phone view shows one question without a long scroll');
assert(elements.grid.children.every(c=>c.children.at(-1).disabled),'Do not use an English voice for Urdu');
tick=2100;vm.runInContext("choose('a',false)",context);
assert.equal(outputs().length,0,'A partial negative screen must not complete');
assert.equal(elements.grid.children[0].children[0].textContent,'Synthetic danger B','No advances to the next unanswered item');
elements.previous.onclick();assert.equal(elements.grid.children[0].children[0].textContent,'Synthetic danger A');
elements.view.onclick();assert.equal(elements.grid.children.length,2,'All-questions mode remains available');
tick=5250;vm.runInContext("choose('b',true)",context);
assert.equal(outputs().length,1);assert.equal(outputs()[0].value.case_id,'case-A');assert.equal(outputs()[0].value.answers.b,true);
assert.equal(outputs()[0].value.elapsed_seconds,5.25);assert(elements.status.textContent.includes('DANGER REPORTED'));
vm.runInContext("choose('a',true)",context);assert.equal(outputs().length,1,'Submitted screen cannot emit again');
render('case-B','scope-2');tick=6000;vm.runInContext("choose('a',true)",context);
assert.equal(outputs().length,2,'The first positive sign interrupts an incomplete screen');
assert.equal(Object.keys(outputs()[1].value.answers).length,1,'Answers from the earlier case must not leak');
render('case-C','scope-3');tick=6500;vm.runInContext("choose('a',null)",context);
assert.equal(outputs().length,2);tick=7000;vm.runInContext("choose('b',false)",context);
assert.equal(outputs().length,3);assert.strictEqual(outputs()[2].value.answers.a,null,'Unknown must stay unknown');
assert(cancelled>0,'Submitting/resetting cancels speech');
questions[0].audio='data:audio/wav;base64,synthetic-fixture';
render('case-D','scope-4');assert(!elements.grid.children[0].children.at(-1).disabled,'Prepared Urdu recording works without an Urdu browser voice');
elements.grid.children[0].children.at(-1).onclick();assert.equal(played[0],questions[0].audio);
elements.stop.onclick();assert.equal(paused,1,'Stop also stops prepared recording playback');
assert(!elements.status.textContent.includes('DANGER REPORTED'),'A different case must not retain an old emergency message');
console.log('Danger screen: immediate interrupt, sequential/all views, recorded-audio fallback, stop, complete answers, case reset and timing payload passed.');
