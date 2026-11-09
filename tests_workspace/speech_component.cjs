/* Browser speech guards with fake voices/recognition. No network or microphone. */
const fs=require('fs'),vm=require('vm'),path=require('path'),assert=require('assert');
const source=fs.readFileSync(path.join(__dirname,'../smartsehat_workspace/consultation/components/speech/index.html'),'utf8').match(/<script>([\s\S]*?)<\/script>/)[1];
const elements={},messages=[],spoken=[];let render,available=[{name:'English only',lang:'en-US',localService:true}],recognizer,cancelled=0;
const element=()=>({value:'',textContent:'',disabled:false,hidden:false,children:[],replaceChildren(){this.children=[];},append(child){this.children.push(child);}});
class Recognition{constructor(){recognizer=this;this.aborted=false;this.stopped=false;}start(){}abort(){this.aborted=true;}stop(){this.stopped=true;}}
const synthesis={getVoices:()=>available,cancel:()=>cancelled++,speak:utterance=>spoken.push(utterance)};
const context={parent:{postMessage:m=>messages.push(m)},document:{getElementById:id=>elements[id]??=element(),createElement:()=>element()},window:{addEventListener:(name,fn)=>render=fn,speechSynthesis:synthesis,SpeechRecognition:Recognition},SpeechSynthesisUtterance:class{constructor(text){this.text=text;}}};
vm.createContext(context);vm.runInContext(source,context);
const display=args=>render({data:{type:'streamlit:render',args}});
display({case_id:'C1',language:'ur-PK',readback:'Current exact summary',read_only:true});
assert.equal(elements.speak.disabled,true,'No English fallback for Urdu');elements.speak.onclick();assert.equal(spoken.length,0);assert.equal(elements.mic.hidden,true);
available.push({name:'Urdu fixture',lang:'ur-PK',localService:true});synthesis.onvoiceschanged();elements.speak.onclick();assert.equal(spoken[0].voice.name,'Urdu fixture');assert.equal(spoken[0].text,'Current exact summary');
display({case_id:'C1',language:'ur-PK'});elements.mic.onclick();recognizer.onresult({resultIndex:0,results:[[{transcript:'Recognized fixture'}]]});
const draft=elements.text.value;display({case_id:'C1',language:'ur-PK'});assert.equal(elements.text.value,draft,'Rerun preserves unsent story');
display({case_id:'C2',language:'ur-PK'});assert.equal(elements.text.value,'','Case change clears former transcript');assert.equal(recognizer.aborted,true);
elements.mic.onclick();elements.stop.onclick();assert.equal(recognizer.stopped,true);assert.ok(cancelled>0);elements.text.value='Confirmed draft';elements.use.onclick();assert.equal(messages.at(-1).value,'Confirmed draft');
console.log('Speech: matching voice, read-only mode, draft preservation, case reset and stop guards passed. No actual voice accuracy measured.');
