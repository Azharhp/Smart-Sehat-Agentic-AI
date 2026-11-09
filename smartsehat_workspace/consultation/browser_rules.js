/* Browser port of clinical.evaluate. Configuration is supplied from the same YAML. */
function evaluateConsultation(facts, age, pregnant, config, aiLevel='GREEN') {
  const current=Object.fromEntries(facts.filter(f=>f.confirmed).map(f=>[f.key,f]));
  const v=Object.fromEntries(Object.entries(current).map(([k,f])=>[k,f.value]));
  const t=config.thresholds, levels={GREEN:0,AMBER:1,RED:2};let level='GREEN';const fired=[];
  const fire=(rule_id,severity,text,keys,layer='pathway')=>{if(levels[severity]>levels[level])level=severity;fired.push({rule_id,level:severity,layer,text,fact_ids:keys.filter(k=>current[k]).map(k=>current[k].id)});};
  const group=['young_infant','child','older_child'].includes(age.pathway)?'child':'adult';
  let questions=[...config.danger_signs[group],...(pregnant?config.danger_signs.maternal:[])];
  questions=Object.values(Object.fromEntries(questions.map(q=>[q.key,q])));
  for(const q of questions)if(v[q.key]===true)fire(q.id,'RED',q.en,[q.key],'danger');
  const respiratory=['cough','breathlessness','wheeze'].some(k=>v[k]===true), rr=v.resp_rate,spo2=v.spo2;
  if(spo2!=null&&spo2<t.spo2_red)fire('SPO2-01','RED','Oxygen saturation below configured threshold',['spo2'],'vitals');
  if(age.pathway==='young_infant'){
    if(['cough','fever','breathlessness','diarrhoea','illness'].some(k=>v[k]===true))fire('ILL-INF-01','AMBER','Illness in a young infant',['age','cough','fever','illness']);
    if(rr!=null&&rr>=t.rr_infant)fire('RR-INF-01','RED','Fast breathing in a young infant',['age','resp_rate']);
    if(v.temperature!=null&&(v.temperature>=t.infant_fever||v.temperature<t.infant_low_temperature))fire('TEMP-INF-01','RED','Abnormal temperature in a young infant',['age','temperature']);
  }else if(age.pathway==='child'){
    const threshold=age.days<Math.round(12*config.age.days_per_month)?t.rr_2_11_months:t.rr_12_59_months;
    if(rr!=null&&rr>=threshold)fire(threshold===t.rr_2_11_months?'RR-CH-01':'RR-CH-02','AMBER','Fast breathing for age',['age','resp_rate']);
  }
  if(v.chest_indrawing===true||v.stridor===true)fire('CI-CH-01',t.child_indrawing_level,'Chest indrawing or stridor when calm',['chest_indrawing','stridor']);
  if(respiratory&&['adult','older_adult'].includes(age.pathway)){
    const score=Number(v.confusion===true)+Number(rr!=null&&rr>=t.rr_adult)+Number((v.systolic!=null&&v.systolic<t.crb_systolic)||(v.diastolic!=null&&v.diastolic<=t.crb_diastolic))+Number(age.days>=t.crb_age_days);
    if(score)fire('CRB-AD-01',score>=t.crb_red_score?'RED':'AMBER','CRB-65 score '+score,['age','confusion','resp_rate','systolic','diastolic']);
  }
  if((v.cough_days||0)>=t.tb_cough_days&&['weight_loss','night_sweats','haemoptysis','tb_contact','diabetes'].some(k=>v[k]===true))fire('TB-01','AMBER','TB screen: doctor review for sputum testing',['cough_days','weight_loss','night_sweats','haemoptysis','tb_contact','diabetes']);
  if((v.fever_days||0)>t.fever_days||['neck_stiffness','rash','bleeding','persistent_vomiting','abdominal_pain'].some(k=>v[k]===true))fire('FEV-01',v.neck_stiffness===true||v.bleeding===true?'RED':'AMBER','Fever pathway warning',['fever_days','neck_stiffness','rash','bleeding','persistent_vomiting','abdominal_pain']);
  if(t.muac_min_age_days<=age.days&&age.days<config.age.child_days&&v.muac!=null&&v.muac<t.muac_red_flag)fire('MUAC-01','AMBER','Low MUAC: nutrition review required',['age','muac']);
  if(v.diarrhoea===true&&((v.diarrhoea_days||0)>=t.diarrhoea_days||v.blood_in_stool===true||v.dehydration===true))fire('DIARRHOEA-01','AMBER','Diarrhoea warning: doctor review required',['diarrhoea','diarrhoea_days','blood_in_stool','dehydration']);
  if(v.symptom_course==='worsening'||v.symptom_severity==='severe')fire('COURSE-01','AMBER','Patient reports worsening or severe symptoms; doctor review required',['symptom_course','symptom_severity']);
  if(v.malaria_rdt&&typeof v.malaria_rdt==='object'&&v.malaria_rdt.result==='positive')fire('POC-01','AMBER','Positive recorded malaria test: doctor interpretation required',['malaria_rdt']);
  if(v.followup_danger===true)fire('FU-DANGER','RED','New danger sign at follow-up',['followup_danger'],'followup');
  if(v.followup_worse===true)fire('FU-WORSE','AMBER','Worsening at follow-up: reassessment required',['followup_worse'],'followup');
  const required=questions.map(q=>q.key);
  if(respiratory){required.push('spo2','resp_rate');if(['young_infant','child','older_child'].includes(age.pathway))required.push('weight');if(['adult','older_adult'].includes(age.pathway))required.push('systolic','diastolic');}
  const missing=required.filter(k=>v[k]==null);
  if(missing.length)fire('MISSING-01','AMBER','Critical information unavailable: '+missing.join(', '),missing,'missing');
  if(levels[aiLevel]>levels[level])fire('AI-CONCERN-01',aiLevel,'AI concern raised urgency; doctor review required',[],'ai');
  return {level,fired,missing_critical:missing,version:config.version,review_status:'pending'};
}
if(typeof module!=='undefined')module.exports={evaluateConsultation};
