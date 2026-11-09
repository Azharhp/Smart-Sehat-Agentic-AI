# Two complete fictional consultation cases

Use TWO-CASES.html for the step-by-step guide and copy buttons. All details are fictional role-play inputs; clinical content, dosing validation and external delivery remain unvalidated/simulated.

## Emergency: Ayaan needs referral

Completed reference: http://127.0.0.1:8501/?case=C-d920508bfc

### 1. Create a NEW case

  Open the live app. Sidebar → Workflow → Consultation. Keep this guide in a separate tab.
 Expand Open a saved case / demo controls → click New consultation. This clears the old case from the form.
 Enter the fields in the table below. Do not select Load fictional Ayaan; that older shortcut skips the intake and has incomplete history.
 Check Consent recorded to share this fictional case with the simulated doctor, then click Create consultation.
 Write down the NEW case ID shown by the app. Use that same ID in the doctor queue. The completed reference ID in this guide is a different, already finished case.
      Intake field  Enter
    Fictional name or ID  Ayaan - full emergency example (fictional)
  Village  Synthetic village A
  Age value  14
  Age unit  months
  Sex  Male
  Pregnant  Hidden for Male
  Language  English
  Use date of birth instead  Leave unchecked
    Presenter: “I am entering a fictional patient and confirming sharing consent for this role-play.”


### 2. Answer EVERY danger-screen question

 For this particular story, every danger-screen answer is No at the initial assessment. The emergency case deteriorates later after the doctor requests a repeat oxygen reading.
     Displayed danger question  Answer now
    Cannot drink or breastfeed  No
  Vomits everything  No
  Convulsions or fits  No
  Lethargic or unconscious  No
     Use the ordinary question cards below Quick danger screen. Select No / نہیں for EACH question. You do not need to operate both the quick component and the ordinary cards.
 Click Continue to story after every item is recorded.
  If a patient already has a danger sign, answer Yes immediately and follow Emergency Lane. Do not finish the questionnaire first. We are using an initially negative screen here to demonstrate a later change.
 Presenter: “Danger screening comes first. In this fictional initial screen, none of these signs is reported.”


### 3. Enter the patient story and confirm extracted facts

  Leave Use live AI extraction OFF for this rehearsed route. Typed text provides the recording/transcript fallback without a microphone or provider dependency.
 Paste the exact story below into Editable transcript / مریض کی بات. Click outside the field so the page updates.
 Check I checked the entire transcript against the recording or the patient's words. Click Extract proposed facts.
 For each proposal below, check its quoted evidence and click Confirm. The app reruns after each confirmation; repeat until no proposals remain.
 Click Continue to questions.
   Copy patient story  Cough for 4 days. Fever for 2 days. No vomiting. No diarrhoea. No wheeze. He is drinking normally. His caregiver says he is breathing faster today.
      Expected proposed fact  Confirm this value
    cough  Yes
  fever  Yes
  vomiting  No
  diarrhoea  No
  wheeze  No
  cough_days  4.0
  fever_days  2.0
    Presenter: “These are proposed facts. I check the patient’s words before confirming each one. This route uses the local fallback; I am not claiming a live AI call.”


### 4. Answer ALL 28 remaining guided questions

 Answer the single question currently displayed, then click Save answer. Work down this table in order. The app skips facts already confirmed from the story or danger screen. There is no need to use Unknown for these authored examples.
 For text answers, click outside the field if Save answer stays disabled. For Yes/No, select the answer and wait for the page to settle.
     Order  Exact question  Exact answer
    1  What is the main concern today?  Cough and fever; caregiver reports faster breathing today.
  2  When did the symptoms first start?  Cough started 4 days ago; fever started 2 days ago.
  3  Are the symptoms improving, unchanged or worsening?  worsening
  4  How does the patient describe the severity?  moderate
  5  Is breathing difficult?  Yes
  6  Does the chest draw in during breathing?  No
  7  Is there a harsh breathing sound when calm?  No
  8  Is there a rash?  No
  9  Is the patient drinking or feeding normally?  Yes
  10  Is urine reduced?  No
  11  Is the neck stiff?  No
  12  Is there unusual bleeding?  No
  13  Is vomiting persistent?  No
  14  Is there abdominal pain?  No
  15  Is there blood in the sputum?  No
  16  Is there a runny or blocked nose?  Yes
  17  Is there a sore throat?  No
  18  Is there tobacco or cooking-smoke exposure?  No
  19  Has anyone nearby had similar symptoms?  Yes
  20  Are the child's vaccinations up to date?  Yes
  21  Was the child born prematurely?  No
  22  Any travel, local outbreak, animal or occupational exposure?  No recent travel or known outbreak, animal or occupational exposure reported.
  23  Has the patient been admitted before?  No
  24  Is there a known penicillin allergy?  No
  25  List other allergies, or explicitly write none or unknown.  None reported, including no known paracetamol allergy.
  26  List existing illnesses, or none or unknown.  None reported. No known liver or kidney disease.
  27  What has been taken, how much, for how long, and who advised it?  No medicines, herbal remedies or cold-and-flu products taken for this illness.
  28  What question should the doctor answer?  Please review the child's breathing and measurements, and advise whether urgent referral is needed.
     At the end, expect All applicable questions documented and Question completeness 100%.
 Check the coverage caption: Not asked: 0 and Unknown: 0. This means all applicable prototype questions are documented, not that the case is clinically complete.
 Click Continue to measurements.
  Presenter: “The app asks the remaining relevant questions and preserves both positive and negative answers.”


### 5. Save EVERY measurement

 These numbers are authored fictional readings. Say that explicitly; do not describe them as readings from a real patient or a completed live breath count.
  Expand each measurement named below. Enter Value and the shown unit, then click that panel's Save measurement.
 For Temperature choose Unit C. For the other panels the unit is already shown.
 The 60-second counter is optional for this role-play. Use the manually entered fictional respiratory rate below. A real respiratory count must run for the full minute.
      Measurement panel  Value / missing reason  Unit
    Temperature  38  C
  Spo2  94  %
  Pulse  120  /min
  Resp Rate  46  /min
  Systolic  100  mmHg
  Diastolic  65  mmHg
  Weight  10  kg
  Muac  14  cm
    Expected after these entries: AMBER. Fast breathing and reported worsening remain visible for the doctor.
  Leave optional photos, observation edits and point-of-care tests alone for these two stories; the guided observations are already answered. Do not create tests without a reason.
 Click Continue to Dawa Check.


### 6. Confirm medicines and allergies

  No medicine has been taken for this illness in either example. Leave the blank Medicine name row empty. Do not type 'none' as a product name.
 Additional medicine history should contain the text below. Known penicillin allergy → No. Other allergies → the text below. Leave the blank detailed allergy row empty.
 Check I checked all medicine details with the patient/image, or confirm none reported. Click Confirm reconciliation.
 Click Continue to review.
   Copy medicine history  No medicines, herbal remedies or cold-and-flu products taken for this illness.
   Copy allergy answer  None reported, including no known paracetamol allergy.
  Presenter: “I explicitly confirm that no medicine has been taken and check the allergy history before sending the case.”


### 7. Generate packet, read back and SEND

  Check that the collection progress is 100%. Keep Use live considerations call OFF.
 Click Generate verified case packet once. Wait for Packet version and VERIFIED correspondence to the confirmed facts.
 Show the SBAR summary, one fact citation, respiratory rate and oxygen reading. Read or summarize the COMPLETE patient read-back in this fictional role-play.
 Check Patient/caregiver confirms the read-back and Worker confirms the full packet and consent to send.
 If I reviewed the medicine safety flags and will include them in the doctor handoff appears, review and check it as part of the role-play.
 Click Confirm read-back and send to doctor. Expect Doctor tasks / awaiting response and Delivery is simulated.
  Presenter: “The worker and patient confirm the information before the remote doctor handoff. Sending and clinician identity are simulated locally.”


### 8. Switch to DOCTOR and request a repeat reading

  Sidebar → Workflow → Doctor console.
 In Doctor queue · urgency then arrival time, select the NEW case ID you created at step 1. Older RED cases may also be listed.
 Review the packet and rule trace. Click Acknowledge / mark seen.
 Under Send a task back to worker, select Task → Recheck SpO2. Leave Task instructions as Recheck SpO2.
 Click Send simulated task. Do not click Save simulated doctor response yet.
  Presenter: “I am switching roles to the remote doctor, who asks for a repeat oxygen reading.”


### 9. Switch to WORKER and return the doctor's answer

  Sidebar → Workflow → Split-screen demo. The worker is on the left and doctor on the right; on a narrow screen, scroll below the worker to find the doctor.
 In the worker's pending Recheck SpO2 task, enter Measured value → 89. Leave If unavailable, reason blank.
 Click Return task answer once.
  Expected: RED and EMERGENCY LANE. The new reading is 89%; the old 94% value remains in the history.
 Presenter: “A new abnormal observation interrupts the normal workflow. We arrange the urgent referral now.”


### 10. Arrange the emergency referral immediately

  In the worker's Emergency Lane, choose Oxygen-capable facility → Synthetic THQ hospital.
 Click Create referral and simulated pre-alert now. Expect Referral recorded and a warning that no actual message was sent.
 Use the displayed transport/referral record. Do not continue routine history or use the medicine fields for this emergency demonstration.
 In the doctor panel, click I reviewed the updated worker information. This acknowledges the returned reading and referral changes. If you cannot see it, scroll to Doctor reply / referral / tests.
  Presenter: “Urgent referral has priority. This prototype records a pre-alert and transport plan, while actual delivery and transport remain simulated.”


### 11. Enter the EXACT final doctor response

 Paste the following into the exact doctor fields. They are scripted role-play answers, not a medical plan for a real person.
 Doctor advice
  Copy exact text  FICTIONAL ROLE-PLAY: The newly returned oxygen reading is 89%. Arrange urgent transfer to the selected referral facility now; do not delay transfer to finish paperwork or give a routine medicine demonstration. Follow the local emergency protocol and contact the receiving clinical team.
  Doctor assessment / diagnosis (optional)
  Copy exact text  Fictional doctor assessment: respiratory illness with a low oxygen reading; urgent in-person assessment required. Cause not established by this prototype.
  Return precautions / counselling script
  Copy exact text  This is already the emergency pathway: proceed with the urgent referral. Report any further worsening immediately to the receiving care team.
  Worker counselling script (doctor authored)
  Copy exact text  Explain that the oxygen reading has worsened and urgent assessment is needed. Ask the caregiver to repeat the destination and immediate next action. Tell them the on-screen pre-alert is simulated in this demonstration.
      Other doctor field  Select / enter
    Referral capability  oxygen
  Facility  Synthetic THQ hospital
  Response status  Final plan
  Worker can do now / Needs referral lab  Leave both empty; no tests are requested in these two examples
  Request a phone/video call  Leave unchecked
  Patient guidance in Urdu  Leave blank for the English route
  Doctor's ORS preparation instructions  Leave blank; no diarrhoea in either case
  Guidance recording / AI rewording  Leave unused for this typed route


### 12. Leave routine medicine fields empty

 No routine outpatient medicine is entered for the emergency case. Leave Doctor-selected medicine and Doctor prescription / schedule blank; leave numeric prescription fields at 0 and the medicine grid blank.
 Presenter: “This patient's next step is urgent referral. I will show an ordinary medicine prescription in the separate routine case.”


### 13. SAVE the final response and open the patient card

  Review every field. Response status → Final plan. If the app reports a newer worker update, click I reviewed the updated worker information and review it before saving.
 Click Save simulated doctor response once. Wait for the page to finish updating.
 Sidebar → Workflow → Consultation. Expect Patient card with this same case ID and the final doctor's advice.
 Check the prescription text for Sara or referral destination for Ayaan. Show the warnings and counselling script.
 Use Print-ready pictogram card to download the patient handout if desired. This typed route does not require a microphone, generated audio or a browser Urdu voice.
  Presenter: “The doctor's response returns to the worker in the same encounter and becomes a patient card.”


### 14. Explain the plan and record understanding

  Expand Explain guidance and check understanding.
 Read the doctor response and counselling script aloud in the role-play. Check Worker explained this doctor response.
 Patient/caregiver understanding → Reports understanding. How guidance was explained → spoken read-back.
 Ask the fictional patient/caregiver to repeat the plan: destination and immediate action for Ayaan; amount, minimum gap, maximum, only-when-needed instruction and warning signs for Sara.
 Click Save counselling confirmation. This is the end of the consultation response loop. The next steps demonstrate a separate future follow-up.
  Presenter: “The worker explains the exact doctor's plan and records reported understanding.”


### 15. Record reported referral progress

  In the worker Patient card, find Referral follow-through · reported status.
 For EACH active referral row, expand Update referral progress, select Reported referral status → admitted.
 Transport, arrival or barrier details → paste the text below. Click Save referral update for each row. Emergency referral and the doctor's referral can create two rows for the same synthetic facility.
 These are fictional caregiver reports; no real hospital has confirmed arrival.
   Copy exact text  FICTIONAL role-play: caregiver reports arrival and admission. Not verified by a real hospital.
  Presenter: “The transfer remains traceable through a reported outcome. The patient's emergency oxygen reading is not being marked as normal or recovered.”


### 16. Demonstrate the FUTURE follow-up and outcome

 This is a simulated later contact, not evidence that 48 real hours passed or that a real patient recovered.
  Expand Follow-up and outcome in the worker Patient card. Click Fast-forward 48 hours ONCE. This advances this case's demonstration clock.
 Enter the exact follow-up answers below, then click Save follow-up.
      Follow-up field  Answer
    Compared with before  same
  Drinking/eating  normal
  Went to the referred facility  Yes
  New danger sign reported  No
  Outcome  admitted
    Expected: Saved step CLOSED with the outcome recorded as admitted. RED can remain on the historical case: admission is a transfer outcome, not proof the emergency resolved.
 Presenter: “The follow-up closes this example with a recorded admitted outcome.”


### 17. Check the COMPLETE answer key / reference backup

 The table below includes every applicable question, including those already answered through the story and danger screen. Questions that are not triggered in this case are intentionally absent. Do not force all 48 catalogue questions on every patient.
     Question  Answer  Where documented
    What is the main concern today?  Cough and fever; caregiver reports faster breathing today.  Guided question
  When did the symptoms first start?  Cough started 4 days ago; fever started 2 days ago.  Guided question
  Are the symptoms improving, unchanged or worsening?  worsening  Guided question
  Is there a cough?  Yes  Confirmed story proposal
  How does the patient describe the severity?  moderate  Guided question
  Is breathing difficult?  Yes  Guided question
  Has there been fever?  Yes  Confirmed story proposal
  For how many days has there been a cough?  4.0  Confirmed story proposal
  For how many days has there been fever?  2.0  Confirmed story proposal
  Does the chest draw in during breathing?  No  Guided question
  Is there a harsh breathing sound when calm?  No  Guided question
  Is there wheezing?  No  Confirmed story proposal
  Has there been vomiting?  No  Confirmed story proposal
  Has there been diarrhoea?  No  Confirmed story proposal
  Have there been fits?  No  Danger screen
  Is there a rash?  No  Guided question
  Is the patient drinking or feeding normally?  Yes  Guided question
  Is urine reduced?  No  Guided question
  Is the neck stiff?  No  Guided question
  Is there unusual bleeding?  No  Guided question
  Is vomiting persistent?  No  Guided question
  Is there abdominal pain?  No  Guided question
  Is there blood in the sputum?  No  Guided question
  Is there a runny or blocked nose?  Yes  Guided question
  Is there a sore throat?  No  Guided question
  Is there tobacco or cooking-smoke exposure?  No  Guided question
  Has anyone nearby had similar symptoms?  Yes  Guided question
  Are the child's vaccinations up to date?  Yes  Guided question
  Was the child born prematurely?  No  Guided question
  Any travel, local outbreak, animal or occupational exposure?  No recent travel or known outbreak, animal or occupational exposure reported.  Guided question
  Has the patient been admitted before?  No  Guided question
  Is there a known penicillin allergy?  No  Guided question
  List other allergies, or explicitly write none or unknown.  None reported, including no known paracetamol allergy.  Guided question
  List existing illnesses, or none or unknown.  None reported. No known liver or kidney disease.  Guided question
  What has been taken, how much, for how long, and who advised it?  No medicines, herbal remedies or cold-and-flu products taken for this illness.  Guided question
  What question should the doctor answer?  Please review the child's breathing and measurements, and advise whether urgent referral is needed.  Guided question
    Completed reference case: C-d920508bfc. This is already finished; it does not restart the manual workflow.
  Open completed LIVE reference  Open completed patient card OFFLINE  Download complete audit record


## Routine: Sara receives a doctor prescription

Completed reference: http://127.0.0.1:8501/?case=C-47b395a387

### 1. Create a NEW case

  Open the live app. Sidebar → Workflow → Consultation. Keep this guide in a separate tab.
 Expand Open a saved case / demo controls → click New consultation. This clears the old case from the form.
 Enter the fields in the table below. Do not select Load fictional Ayaan; that older shortcut skips the intake and has incomplete history.
 Check Consent recorded to share this fictional case with the simulated doctor, then click Create consultation.
 Write down the NEW case ID shown by the app. Use that same ID in the doctor queue. The completed reference ID in this guide is a different, already finished case.
      Intake field  Enter
    Fictional name or ID  Sara - full routine example (fictional)
  Village  Synthetic village B
  Age value  28
  Age unit  years
  Sex  Female
  Pregnant  Leave unchecked
  Language  English
  Use date of birth instead  Leave unchecked
    Presenter: “I am entering a fictional patient and confirming sharing consent for this role-play.”


### 2. Answer EVERY danger-screen question

 For this particular story, every danger-screen answer is No at the initial assessment. The emergency case deteriorates later after the doctor requests a repeat oxygen reading.
     Displayed danger question  Answer now
    Severe breathlessness  No
  Cannot speak full sentences  No
  Confusion  No
  Chest pain  No
  Fainting  No
  Blue lips  No
  Fits  No
  Heavy bleeding  No
  Signs of shock  No
     Use the ordinary question cards below Quick danger screen. Select No / نہیں for EACH question. You do not need to operate both the quick component and the ordinary cards.
 Click Continue to story after every item is recorded.
  If a patient already has a danger sign, answer Yes immediately and follow Emergency Lane. Do not finish the questionnaire first. We are using an initially negative screen here to demonstrate a later change.
 Presenter: “Danger screening comes first. In this fictional initial screen, none of these signs is reported.”


### 3. Enter the patient story and confirm extracted facts

  Leave Use live AI extraction OFF for this rehearsed route. Typed text provides the recording/transcript fallback without a microphone or provider dependency.
 Paste the exact story below into Editable transcript / مریض کی بات. Click outside the field so the page updates.
 Check I checked the entire transcript against the recording or the patient's words. Click Extract proposed facts.
 For each proposal below, check its quoted evidence and click Confirm. The app reruns after each confirmation; repeat until no proposals remain.
 Click Continue to questions.
   Copy patient story  Cough for 2 days. No fever. No breathlessness. No vomiting. No diarrhoea. No wheeze. My nose is runny, my throat is sore and I have mild body aches. I am eating and drinking normally.
      Expected proposed fact  Confirm this value
    cough  Yes
  fever  No
  breathlessness  No
  vomiting  No
  diarrhoea  No
  wheeze  No
  cough_days  2.0
    Presenter: “These are proposed facts. I check the patient’s words before confirming each one. This route uses the local fallback; I am not claiming a live AI call.”


### 4. Answer ALL 21 remaining guided questions

 Answer the single question currently displayed, then click Save answer. Work down this table in order. The app skips facts already confirmed from the story or danger screen. There is no need to use Unknown for these authored examples.
 For text answers, click outside the field if Save answer stays disabled. For Yes/No, select the answer and wait for the page to settle.
     Order  Exact question  Exact answer
    1  What is the main concern today?  Mild cough, runny nose, sore throat and body aches.
  2  When did the symptoms first start?  Symptoms started 2 days ago.
  3  Are the symptoms improving, unchanged or worsening?  unchanged
  4  How does the patient describe the severity?  mild
  5  Does the chest draw in during breathing?  No
  6  Is there a harsh breathing sound when calm?  No
  7  Is there a rash?  No
  8  Is the patient drinking or feeding normally?  Yes
  9  Is urine reduced?  No
  10  Is there blood in the sputum?  No
  11  Is there a runny or blocked nose?  Yes
  12  Is there a sore throat?  Yes
  13  Is there tobacco or cooking-smoke exposure?  No
  14  Has anyone nearby had similar symptoms?  Yes
  15  Has the patient been admitted before?  No
  16  Is there a known penicillin allergy?  No
  17  List other allergies, or explicitly write none or unknown.  None reported, including no known paracetamol allergy.
  18  List existing illnesses, or none or unknown.  None reported. No known liver or kidney disease. No alcohol dependence or binge drinking reported.
  19  Is the patient breastfeeding?  No
  20  What has been taken, how much, for how long, and who advised it?  No medicines, herbal remedies or cold-and-flu products taken for this illness.
  21  What question should the doctor answer?  Please review the symptoms and normal measurements, and provide symptom-relief advice and return precautions.
     At the end, expect All applicable questions documented and Question completeness 100%.
 Check the coverage caption: Not asked: 0 and Unknown: 0. This means all applicable prototype questions are documented, not that the case is clinically complete.
 Click Continue to measurements.
  Presenter: “The app asks the remaining relevant questions and preserves both positive and negative answers.”


### 5. Save EVERY measurement

 These numbers are authored fictional readings. Say that explicitly; do not describe them as readings from a real patient or a completed live breath count.
  Expand each measurement named below. Enter Value and the shown unit, then click that panel's Save measurement.
 For Temperature choose Unit C. For the other panels the unit is already shown.
 The 60-second counter is optional for this role-play. Use the manually entered fictional respiratory rate below. A real respiratory count must run for the full minute.
      Measurement panel  Value / missing reason  Unit
    Temperature  36.8  C
  Spo2  98  %
  Pulse  78  /min
  Resp Rate  16  /min
  Systolic  118  mmHg
  Diastolic  76  mmHg
  Weight  60  kg
  Muac  Leave Value blank; enter reason: Not included in this fictional adult assessment.  cm
    Expected after these entries: GREEN. GREEN means no configured urgency warning fires in these entered facts; it is not a guarantee of medical safety.
  Leave optional photos, observation edits and point-of-care tests alone for these two stories; the guided observations are already answered. Do not create tests without a reason.
 Click Continue to Dawa Check.


### 6. Confirm medicines and allergies

  No medicine has been taken for this illness in either example. Leave the blank Medicine name row empty. Do not type 'none' as a product name.
 Additional medicine history should contain the text below. Known penicillin allergy → No. Other allergies → the text below. Leave the blank detailed allergy row empty.
 Check I checked all medicine details with the patient/image, or confirm none reported. Click Confirm reconciliation.
 Click Continue to review.
   Copy medicine history  No medicines, herbal remedies or cold-and-flu products taken for this illness.
   Copy allergy answer  None reported, including no known paracetamol allergy.
  Presenter: “I explicitly confirm that no medicine has been taken and check the allergy history before sending the case.”


### 7. Generate packet, read back and SEND

  Check that the collection progress is 100%. Keep Use live considerations call OFF.
 Click Generate verified case packet once. Wait for Packet version and VERIFIED correspondence to the confirmed facts.
 Show the SBAR summary, one fact citation, respiratory rate and oxygen reading. Read or summarize the COMPLETE patient read-back in this fictional role-play.
 Check Patient/caregiver confirms the read-back and Worker confirms the full packet and consent to send.
 If I reviewed the medicine safety flags and will include them in the doctor handoff appears, review and check it as part of the role-play.
 Click Confirm read-back and send to doctor. Expect Doctor tasks / awaiting response and Delivery is simulated.
  Presenter: “The worker and patient confirm the information before the remote doctor handoff. Sending and clinician identity are simulated locally.”


### 8. Switch to DOCTOR and request a repeat reading

  Sidebar → Workflow → Doctor console.
 In Doctor queue · urgency then arrival time, select the NEW case ID you created at step 1. Older RED cases may also be listed.
 Review the packet and rule trace. Click Acknowledge / mark seen.
 Under Send a task back to worker, select Task → Recheck SpO2. Leave Task instructions as Recheck SpO2.
 Click Send simulated task. Do not click Save simulated doctor response yet.
  Presenter: “I am switching roles to the remote doctor, who asks for a repeat oxygen reading.”


### 9. Switch to WORKER and return the doctor's answer

  Sidebar → Workflow → Split-screen demo. The worker is on the left and doctor on the right; on a narrow screen, scroll below the worker to find the doctor.
 In the worker's pending Recheck SpO2 task, enter Measured value → 98. Leave If unavailable, reason blank.
 Click Return task answer once.
  Expected: GREEN remains. The repeat 98% reading is attached to the doctor's request.
 Presenter: “The repeat observation remains within this fictional routine pathway. The doctor still makes the treatment decision.”


### 10. Doctor reviews the returned information

  In the doctor half of Split-screen demo, expand Doctor reply / referral / tests if needed.
 Click I reviewed the updated worker information before filling the final response. This accepts the new repeat reading for the current draft.
 Keep Use AI to draft simpler Urdu wording OFF. Leave Patient guidance in Urdu empty for this English route; no translation or audio approval is needed.
  The example assumes no known paracetamol allergy, liver/kidney disease, alcohol dependence or binge drinking, no other medicines and a fictional adult weight of 60 kg. These are authored assumptions, not an actual clinical evaluation.


### 11. Enter the EXACT final doctor response

 Paste the following into the exact doctor fields. They are scripted role-play answers, not a medical plan for a real person.
 Doctor advice
  Copy exact text  FICTIONAL ROLE-PLAY: The history and repeat measurements do not show a configured emergency warning in this example. Use the written symptom-relief prescription only if needed for the reported aches or sore throat, rest, and drink fluids normally. Seek reassessment if symptoms worsen or do not improve. This is a fictional doctor's entered plan; the app did not diagnose or select the dose.
  Doctor assessment / diagnosis (optional)
  Copy exact text  Fictional doctor impression: mild upper-respiratory symptoms; no definitive diagnosis established by the prototype.
  Return precautions / counselling script
  Copy exact text  Seek urgent assessment for new breathing difficulty, chest pain, confusion, fainting, blue lips, inability to drink, or rapid worsening. Seek clinical reassessment if symptoms persist or worsen.
  Worker counselling script (doctor authored)
  Copy exact text  Explain the one-tablet amount, six-hour minimum gap, maximum four tablets in 24 hours, one-day example duration, only-when-needed instruction, and avoiding duplicate paracetamol products. Ask Sara to repeat these instructions and the warning signs. Explain that the app's dose validation remains unavailable.
      Other doctor field  Select / enter
    Referral capability  No referral
  Facility  None
  Response status  Final plan
  Worker can do now / Needs referral lab  Leave both empty; no tests are requested in these two examples
  Request a phone/video call  Leave unchecked
  Patient guidance in Urdu  Leave blank for the English route
  Doctor's ORS preparation instructions  Leave blank; no diarrhoea in either case
  Guidance recording / AI rewording  Leave unused for this typed route


### 12. Enter the doctor's EXAMPLE medicine prescription

 The medicine and quantities here are authored for an adult fictional role-play. The app is recording a doctor's entered plan; its reviewed dose table is empty, so it cannot validate the dose or supply a prescription.
     Exact prescription field  Enter
    Doctor-selected medicine (optional, exact brand or generic)  Paracetamol
  Selected medicine formulation  tablet
  Printed strength of selected product  500 mg
  Doctor-selected administration route  oral
  Doctor-entered amount per dose in mg (0 = not entered)  500
  Doctor-entered maximum doses in any 24 hours (0 = not entered)  4
  Doctor-entered course length in days (0 = not entered)  1
  Doctor-selected indication / reason for this medicine  Body aches or sore-throat pain
  Doctor has prescribed this medicine only when needed  Check
  Patient medicine schedule grid  Leave the blank row empty; use the complete written prescription below, because this is only-when-needed, not four fixed scheduled times
    Doctor prescription / schedule (optional)
  Copy fictional written prescription  FICTIONAL ROLE-PLAY ONLY: Paracetamol 500 mg tablet: take ONE tablet by mouth only if needed for aches or sore-throat pain. Leave at least 6 hours between doses. Maximum FOUR tablets (2,000 mg) in any 24 hours for this one-day example. Do not take another product containing paracetamol. Stop when the pain settles. Seek clinical review if symptoms persist or worsen. This example is not a prescription for a real person.
  Doctor's feeding / fluid advice
  Copy exact text  Drink fluids normally and eat as tolerated in this fictional example.
   After the final edit, click outside the field and let the page update. Read the prescription flags, including unavailable/unvalidated dose checking.
 In the fictional doctor role, check Doctor has independently reviewed the prescription flags and unvalidated dose check. It can reset after a prescription edit; check it again only after reviewing the current fields.
  Reference: the NHS adult paracetamol page supports its use for pain, common tablet strengths, dose spacing and avoiding duplicate products. This scripted one-day example is more limited than the general adult maximum; it does not establish suitability for an actual patient.
  NHS: paracetamol for adults — medicine reference
 Presenter: “The doctor selects the medicine and enters the prescription. The prototype does not claim validated dose checking.”


### 13. SAVE the final response and open the patient card

  Review every field. Response status → Final plan. If the app reports a newer worker update, click I reviewed the updated worker information and review it before saving.
 Click Save simulated doctor response once. Wait for the page to finish updating.
 Sidebar → Workflow → Consultation. Expect Patient card with this same case ID and the final doctor's advice.
 Check the prescription text for Sara or referral destination for Ayaan. Show the warnings and counselling script.
 Use Print-ready pictogram card to download the patient handout if desired. This typed route does not require a microphone, generated audio or a browser Urdu voice.
  Presenter: “The doctor's response returns to the worker in the same encounter and becomes a patient card.”


### 14. Explain the plan and record understanding

  Expand Explain guidance and check understanding.
 Read the doctor response and counselling script aloud in the role-play. Check Worker explained this doctor response.
 Patient/caregiver understanding → Reports understanding. How guidance was explained → spoken read-back.
 Ask the fictional patient/caregiver to repeat the plan: destination and immediate action for Ayaan; amount, minimum gap, maximum, only-when-needed instruction and warning signs for Sara.
 Click Save counselling confirmation. This is the end of the consultation response loop. The next steps demonstrate a separate future follow-up.
  Presenter: “The worker explains the exact doctor's plan and records reported understanding.”


### 15. Demonstrate the FUTURE follow-up and outcome

 This is a simulated later contact, not evidence that 48 real hours passed or that a real patient recovered.
  Expand Follow-up and outcome in the worker Patient card. Click Fast-forward 48 hours ONCE. This advances this case's demonstration clock.
 Enter the exact follow-up answers below, then click Save follow-up.
      Follow-up field  Answer
    Compared with before  better
  Drinking/eating  normal
  Went to the referred facility  Unknown
  New danger sign reported  No
  Outcome  recovered
    Expected: Saved step CLOSED with the outcome recorded as recovered. The routine recovery is an authored fictional follow-up answer, not an observed treatment outcome.
 Presenter: “The follow-up closes this example with a recorded recovered outcome.”


### 16. Check the COMPLETE answer key / reference backup

 The table below includes every applicable question, including those already answered through the story and danger screen. Questions that are not triggered in this case are intentionally absent. Do not force all 48 catalogue questions on every patient.
     Question  Answer  Where documented
    What is the main concern today?  Mild cough, runny nose, sore throat and body aches.  Guided question
  When did the symptoms first start?  Symptoms started 2 days ago.  Guided question
  Are the symptoms improving, unchanged or worsening?  unchanged  Guided question
  Is there a cough?  Yes  Confirmed story proposal
  How does the patient describe the severity?  mild  Guided question
  Is breathing difficult?  No  Confirmed story proposal
  Has there been fever?  No  Confirmed story proposal
  For how many days has there been a cough?  2.0  Confirmed story proposal
  Does the chest draw in during breathing?  No  Guided question
  Is there a harsh breathing sound when calm?  No  Guided question
  Is there wheezing?  No  Confirmed story proposal
  Has there been vomiting?  No  Confirmed story proposal
  Has there been diarrhoea?  No  Confirmed story proposal
  Have there been fits?  No  Danger screen
  Is there a rash?  No  Guided question
  Is the patient drinking or feeding normally?  Yes  Guided question
  Is urine reduced?  No  Guided question
  Is there blood in the sputum?  No  Guided question
  Is there a runny or blocked nose?  Yes  Guided question
  Is there a sore throat?  Yes  Guided question
  Is there tobacco or cooking-smoke exposure?  No  Guided question
  Has anyone nearby had similar symptoms?  Yes  Guided question
  Has the patient been admitted before?  No  Guided question
  Is there a known penicillin allergy?  No  Guided question
  List other allergies, or explicitly write none or unknown.  None reported, including no known paracetamol allergy.  Guided question
  List existing illnesses, or none or unknown.  None reported. No known liver or kidney disease. No alcohol dependence or binge drinking reported.  Guided question
  Is the patient breastfeeding?  No  Guided question
  What has been taken, how much, for how long, and who advised it?  No medicines, herbal remedies or cold-and-flu products taken for this illness.  Guided question
  What question should the doctor answer?  Please review the symptoms and normal measurements, and provide symptom-relief advice and return precautions.  Guided question
    Completed reference case: C-47b395a387. This is already finished; it does not restart the manual workflow.
  Open completed LIVE reference  Open completed patient card OFFLINE  Download complete audit record
