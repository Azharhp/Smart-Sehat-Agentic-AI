"""Standalone offline safety capture and printable patient cards. No external scripts."""
from __future__ import annotations
import html
import json
import base64
from pathlib import Path
from .clinical import config
from .timeutils import display_time


def patient_card(name: str, reply: dict, audio: bytes | None = None, audio_mime: str = "audio/wav", care: dict | None = None) -> str:
    esc = lambda value: html.escape(str(value or "Not recorded"))
    rows = "".join("<tr><td>" + esc(row["medicine"]) + "</td><td>" + esc(row["dose"]) + "</td>" + "".join("<td>" + ("✓" if row.get(time) else "—") + "</td>" for time in ("morning", "noon", "evening", "night")) + "<td>" + esc(row.get("notes", "")) + "</td></tr>" for row in reply.get("medication_schedule", []))
    table = "<table style='width:100%;border-collapse:collapse' border='1' cellpadding='8'><tr><th>Medicine</th><th>Doctor's dose</th><th>☀️ Morning</th><th>🌤 Noon</th><th>🌇 Evening</th><th>🌙 Night</th><th>Notes</th></tr>" + rows + "</table>" if rows else ""
    audio_html = ""
    if audio and reply.get("guidance_audio_confirmed") is True:
        if audio_mime not in ("audio/wav", "audio/mpeg", "audio/webm", "audio/mp4", "audio/ogg"):
            raise ValueError("Unsupported patient guidance audio format.")
        audio_html = "<h3>🔊 Doctor-confirmed recorded guidance</h3><audio controls preload='none' src='data:" + audio_mime + ";base64," + base64.b64encode(audio).decode("ascii") + "'></audio><p>The recording is included in this file and can play without a connection.</p>"
    interim = "Awaiting requested information / test results. This is interim doctor guidance." if reply.get("status") == "awaiting_information" else "Doctor-authorised guidance"
    handoffs = ""
    if care:
        handoffs = "".join("<p>🏥 " + esc(r["facility"]["name"]) + ": reported " + esc(r["status"]) + (" — " + esc(r["notes"]) if r.get("notes") else "") + "</p>" for r in care.get("referrals", []))
        handoffs += "".join("<p>📞 Requested contact: reported " + esc(c["status"]) + (" — " + esc(c["notes"]) if c.get("notes") else "") + "</p>" for c in care.get("calls", []))
    parts = ["<!doctype html><html><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>",
             "<style>body{font:20px system-ui;max-width:760px;margin:30px auto;padding:20px;line-height:1.6}article{border:2px solid #087d82;border-radius:16px;padding:25px}h1{color:#087d82}button{font:inherit;padding:12px}@media print{button{display:none}}</style>",
             "<article><p>SYNTHETIC · RESEARCH PROTOTYPE · SIMULATED DOCTOR</p><h1>SmartSehat · مریض کی رہنمائی</h1>",
             "<h2>" + esc(name) + "</h2><p><strong>" + interim + "</strong></p><p>" + esc(reply.get("advice")) + "</p>",
             "<p dir='rtl'>" + esc(reply.get("local_language")) + "</p>",
             audio_html,
             "<h3>☀️ / 🌙 Medicine schedule</h3><p>" + esc(reply.get("prescription")) + "</p>",
             table,
             "<h3>🥣 Feeding and fluids</h3><p>" + esc(reply.get("feeding")) + "</p>",
             "<h3>💧 ORS instructions from doctor</h3><p>" + esc(reply.get("ors")) + "</p>",
             "<h3>🏥 Referral</h3><p>" + esc(reply.get("referral")) + "</p>",
             handoffs,
             "<h3>🧪 Doctor-requested tests</h3><p>Local: " + esc(", ".join(reply.get("local_tests", []))) + "</p><p>Referral lab: " + esc(", ".join(reply.get("lab_tests", []))) + "</p>",
             "<p>" + ("Doctor requests a call. Contact the care team through the agreed channel." if reply.get("request_call") else "") + "</p>",
             "<h3>⚠️ Return precautions</h3><p>" + esc(reply.get("return_if")) + "</p>",
             "<p>Follow-up: " + esc(display_time(reply["followup_due"]) if reply.get("followup_due") else "Awaiting the doctor's next response") + "</p></article><button onclick='print()'>Print / Save PDF</button></html>"]
    return "".join(parts)


def offline_capture() -> str:
    engine = (Path(__file__).parent / "browser_rules.js").read_text(encoding="utf-8")
    configuration = json.dumps(config(), ensure_ascii=False).replace("<", "\\u003c")
    return """<!doctype html><html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>SmartSehat offline safety capture</title><style>body{font:18px system-ui;max-width:760px;margin:20px auto;padding:16px}input,select,button{font:inherit;min-height:46px;padding:8px;margin:5px}label{display:block;margin:12px}#banner{padding:20px;border-radius:10px;font-size:24px}section{border:1px solid #cbd5dd;border-radius:12px;padding:12px;margin:12px 0}.RED{background:#ffe0e0;color:#900}.AMBER{background:#fff0c2}.GREEN{background:#d7f6eb}</style>
<h1>SmartSehat · Offline capture</h1><p>SYNTHETIC DATA ONLY · Research prototype · Clinical review pending</p><p>Save this file on the presentation device before disconnecting. Rules and this form work without the Streamlit server. No real delivery or automatic sync.</p>
<section><label>Fictional ID <input id="name" value="Offline fictional case"></label><label>Age <input id="age" type="number" min="0" value="14"><select id="unit"><option>months</option><option>years</option><option>weeks</option><option>days</option></select></label><label>Pregnant <input id="pregnant" type="checkbox"></label></section>
<div id="banner" role="alert"></div><section id="danger"></section><section><label>Cough <select id="cough"><option value="">Unknown</option><option value="true">Yes</option><option value="false">No</option></select></label>
<label>SpO2 % <input id="spo2" type="number" min="50" max="100"></label><label>Respiratory rate /min <input id="resp_rate" type="number" min="5" max="120"></label><label>Weight kg <input id="weight" type="number" min="0.5" max="300"></label><label>Missing measurement reason <input id="reason"></label></section>
<p id="details"></p><p id="emergency" hidden>EMERGENCY: contact local emergency services/doctor and follow trained local protocols. Do not wait for a form or internet connection.</p><button id="export">Export for worker confirmation</button><p id="status"></p>
<script>""" + engine + "\nconst config=" + configuration + ";\n" + r"""
const $=id=>document.getElementById(id);let memory={};try{memory=JSON.parse(localStorage.getItem('smartsehat-offline')||'{}');}catch(e){}
for(const id of ['name','age','unit','cough','spo2','resp_rate','weight','reason'])if(memory[id]!=null)$(id).value=memory[id];$('pregnant').checked=!!memory.pregnant;
let age,facts,result,groupKey='';
function recalculate(){
 let value=Number($('age').value),unit=$('unit').value;if(!Number.isFinite(value)||value<0)return;
 const factors={days:1,weeks:7,months:config.age.days_per_month,years:config.age.days_per_year};let days=Math.round(value*factors[unit]);
 age={value,unit,days,pathway:days<config.age.infant_days?'young_infant':days<config.age.child_days?'child':days<Math.round(config.age.older_child_years*config.age.days_per_year)?'older_child':days>=config.thresholds.crb_age_days?'older_adult':'adult'};
 const group=['young_infant','child','older_child'].includes(age.pathway)?'child':'adult',pregnant=$('pregnant').checked;
 const key=group+pregnant;if(groupKey!==key){groupKey=key;$('danger').replaceChildren();const qs=Object.values(Object.fromEntries([...config.danger_signs[group],...(pregnant?config.danger_signs.maternal:[])].map(q=>[q.key,q])));for(const q of qs){let label=document.createElement('label');label.textContent=q.en+' / '+q.ur;let select=document.createElement('select');select.dataset.fact=q.key;for(const [value,text]of [['','Unknown'],['false','No'],['true','Yes']]){let o=document.createElement('option');o.value=value;o.textContent=text;select.append(o);}if(memory[q.key]!=null)select.value=memory[q.key];select.onchange=recalculate;label.append(select);$('danger').append(label);}}
 facts=[{id:'F1',key:'age',value:age,confirmed:true,source:'worker'}];let i=2;
 for(const select of document.querySelectorAll('[data-fact]')){memory[select.dataset.fact]=select.value;if(select.value!=='')facts.push({id:'F'+i++,key:select.dataset.fact,value:select.value==='true',confirmed:true,source:'worker'});}
 if($('cough').value!=='')facts.push({id:'F'+i++,key:'cough',value:$('cough').value==='true',confirmed:true,source:'worker'});
 let invalid=false;
 for(const key of ['spo2','resp_rate','weight']){const raw=$(key).value,spec=config.measurements[key];if(raw!==''){const value=Number(raw);if(!Number.isFinite(value)||value<spec.min||value>spec.max){invalid=true;continue;}facts.push({id:'F'+i++,key,value,unit:spec.unit,confirmed:true,source:'device'});}}
 result=evaluateConsultation(facts,age,pregnant,config);$('banner').className=result.level;$('banner').textContent=result.level+' · '+age.pathway;
 $('details').textContent=(invalid?'Invalid measurement rejected. ':'')+result.fired.map(r=>r.rule_id+': '+r.text).join(' | ');$('emergency').hidden=result.level!=='RED';
 for(const id of ['name','age','unit','cough','spo2','resp_rate','weight','reason'])memory[id]=$(id).value;memory.pregnant=pregnant;try{localStorage.setItem('smartsehat-offline',JSON.stringify(memory));}catch(e){$('status').textContent='Browser storage unavailable; export before closing.';}
}
document.querySelectorAll('input,select').forEach(el=>el.addEventListener('input',recalculate));recalculate();
$('export').onclick=()=>{recalculate();const data={format:'smartsehat-offline-1',synthetic:true,name:$('name').value,age,pregnant:$('pregnant').checked,facts,reason_missing:$('reason').value,urgency:result};const blob=new Blob([JSON.stringify(data,null,2)],{type:'application/json'});const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download='smartsehat-offline-case.json';a.click();URL.revokeObjectURL(a.href);};
</script></html>"""
