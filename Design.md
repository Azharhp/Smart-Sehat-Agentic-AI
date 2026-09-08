# SmartSehat — Complete UI Design Brief for Google Stitch

This document is a design specification, not a claim that every proposed layout already exists. It maps the current application into a polished screen set. Preserve existing actions, permissions, confirmations, clinical wording, and data meaning. Layouts, navigation styling, and responsive cards below are design proposals. Do not invent new clinical capabilities.

## 1. Paste this master prompt into Google Stitch

Design SmartSehat, a beautiful, practical healthcare application for frontline health workers in Pakistan. It helps workers capture patient complaints in text or uploaded voice recordings, confirm the history, complete structured questions, review rule-based findings, reconcile medicines, and prepare a referral or doctor review report. A separate district officer view shows aggregate activity and oversight.

Create a consistent family of desktop and mobile screens using the screen inventory below. Use a calm teal-and-white visual identity, generous whitespace, readable typography, soft bordered cards, clear step progression, and prominent next actions. Make complex clinical information understandable without showing JSON, code blocks, raw dictionaries, or database field names. Use labeled values, medicine cards, findings, checklists, and chronological activity items. Support English and Urdu with correct text direction. Use synthetic patient data only.

Keep urgent warnings prominent and use text plus icons as well as color. Clearly distinguish AI drafts, worker-confirmed information, and deterministic findings. Missing information must never look like a negative answer. No screen should imply that a green result guarantees safety or that an AI medicine proposal is approved treatment. Keep the current functionality; do not add appointment booking, billing, live doctor chat, automatic prescribing, or unsupported diagnostic tools.

Produce the shared design system first, then screens S01–S20 and the state variants listed below. Use the same patient header and components throughout. Include desktop 1440 px, tablet 768 px, and mobile 390 px layouts. Exportable report pages should also work on A4 paper.

## 2. Product and roles

- **Frontline worker / paramedic:** patient consultation, voice/text history review, structured assessment, medicine review, referral documentation, manual recording of doctor advice.
- **District Health Officer:** authorized aggregate district dashboard and oversight.
- **Doctor:** currently receives a report outside the app; there is no implemented doctor portal or verified live chat. Doctor advice is manually entered by a worker and must be labeled accordingly.
- **Demo authentication:** account selector and Start demo session; authenticated identity is not a freely editable field.
- Navigation must reflect actual permissions. Do not expose patient workflows to an aggregate-only district role.

## 3. Visual system

| Element | Design direction |
|---|---|
| Brand | SmartSehat; subtitle: Clear information. Better coordinated care. |
| Primary | Deep teal #087F83; verify text contrast before finalizing |
| Background | Soft blue-gray #F2F8FA |
| Surface | White #FFFFFF |
| Main text | Dark slate #183641 |
| Secondary text | Slate #526770 |
| Borders | Light blue-gray #DBE8EB |
| Urgent | Dark red text, pale red surface, warning icon and explicit action |
| Review needed | Dark amber text and pale amber surface |
| Neutral information | Blue/teal text with pale information surface |
| Typography | Inter or equivalent for English; Noto Sans Arabic or equivalent for Urdu |
| Scale | 30–36 px page titles; 20–24 px sections; 16 px body; 13–14 px metadata |
| Spacing | 8 px base scale; 24 px between cards; 16–24 px card padding |
| Shape | 12–16 px card corners; subtle shadows; 10 px button corners |
| Icons | Consistent outline icons: patient, microphone, document, image, shield, alert, download |

Avoid oversized decorative hero sections inside clinical workflows. Prioritize the current patient, current step, findings, and next action. Use restrained decoration and no stock medical imagery that competes with the task.

## 4. Shared shell and navigation

Desktop: left sidebar around 250 px; main content with a comfortable reading width. Optional right-side encounter summary on wide screens. Mobile: compact header, navigation drawer, stacked cards, and an action area that never obscures warnings.

Sidebar content:

- SmartSehat logo/name.
- Signed-in name, role, active facility, Sign out.
- Active facility selector when applicable.
- Patient selector for authorized users; patient name and reference.
- Recent authorized encounters and encounter selector where provided.
- Consultation, Dawa Check, District View according to permissions.
- Compact mode badge: Real providers or Offline demo.
- Provider readiness in a secondary expandable area; technical provider names should not dominate clinical screens.

Patient/encounter strip: patient display name/reference, available age and sex, facility, encounter reference, current status. Missing values read “Not recorded.” Switching patients or encounters must never show another encounter's draft or uploaded image. Distinguish selected patient from an already open encounter when they differ.

## 5. Complete screen inventory

### S01 — Demo sign-in

Centered compact welcome card with brand, short explanation, Demo account dropdown, and Start demo session. Explain development authentication visibly but briefly. Include no-accounts state with a clear setup message. No invented email/password authentication or account registration. Demo account defaults to a paramedic when available; district accounts remain selectable.

### S02 — Consultation start / encounter selection

Show selected patient summary and recent encounters. Primary action: Create consultation encounter for selected patient. Existing encounter state shows its status and Continue current encounter / Start new encounter for selected patient. Where Continue is already the current state, show it as disabled or a current-state indicator. Include no-patient and no-authorized-patient states. Do not imply starting a new encounter deletes the previous one.

### S03 — Complaint capture: typed

Stepper: Complaint → History → Structured information → Assessment → Review and handoff.

Fields: Complaint source with Typed complaint / Audio recording; Declared language with Unknown, Urdu, English, Hindi / Devanagari, Mixed; multiline original patient statement.

Primary action: Start guided consultation (structured intake). Secondary expandable section: Attach encounter inputs. Show a small assurance that the original statement remains preserved after edits. A collapsed Developer / legacy consultation path contains the existing Start consultation workflow action; keep it visually secondary.

### S04 — Complaint capture: voice and attachments

Same screen family as S03 with Audio recording selected. Current implementation accepts a WAV upload; do not imply built-in live microphone recording exists. Show file picker/drop zone, filename and retained-upload state. Add separate optional chest X-ray JPG/JPEG/PNG uploader and confirmation checkbox: “I confirm the image is a chest X-ray for this encounter.”

States: no file, selected file, upload validation error, retained upload, unconfirmed X-ray, processing, transcription unavailable with manual-history fallback. If designing preview/audio playback, mark it as a proposed convenience rather than a required existing control.

### S05 — History review and acceptance

Two clear panels: Original statement (read-only) and Reviewed statement (editable). Urdu content uses correct direction and remains selectable. Source, language, and history version appear as compact metadata.

Primary action: Accept this clinical history, disabled when reviewed text is blank. Accepted variant shows Original, Corrected, and Accepted history, confirmation actor and time. Show transcription failure above the manual entry field when applicable. Never overwrite or hide the original evidence.

### S06 — Structured clinical information

Show suggested pathway and its reason, followed by explicit confirmation of the proposed pathway. The currently supported guided pathway is respiratory-focused; do not design a full specialty catalog.

Group questions into Symptoms, Urgent signs, Measurements, and Background information. Boolean answers: Yes / No / Unknown. Numeric answers: Enter value / Unknown, with a separate unit selector where applicable. List/status questions retain backend-defined choices and distinguish unknown from confirmed none. Conditional questions appear only when relevant; do not prefill unanswered questions with No.

Include these configured questions:

| Group | Fields |
|---|---|
| Respiratory symptoms | Current cough; cough duration in days/weeks; blood in sputum; current fever; fever duration in days/weeks; night sweats; unintentional weight loss; severe breathlessness/inability to breathe |
| Urgent signs | Chest pain; pain spreading to left arm/jaw; sweating with chest pain; unconscious/unresponsive; active fitting/convulsions; pregnancy; bleeding if pregnant |
| Measurements | Confirmed age in months/years; temperature in C/F; oxygen saturation in %; respiratory rate in breaths/min; systolic and diastolic blood pressure in mmHg |
| Background | Confirmed allergy history; confirmed current medicines |

Primary action: Confirm structured information and assess. Include validation for missing pathway confirmation, unknown important information, and invalid numeric input. Keep missing-data indicators factual; do not fabricate scores or clinical thresholds.

### S07 — Unsupported scope / ambiguous pathway

Prominent informational warning explaining why a supported assessment is unavailable or requires confirmation. Show the recorded complaint and recommendation reason. Include Clinical review / referral reason and Record unsupported scope for clinical review. No success checkmark suggesting a completed safe assessment. Ambiguous pathway variant requires worker confirmation rather than silently choosing a diagnosis.

### S08 — Assessment overview

Top status banner, concise findings, evidence under each finding, information still needed, unresolved evidence, limitations, and next-step context. Optional side summary shows selected pathway and source history.

Required variants:

- RED: urgent clinical assessment and configured referral workflow required.
- YELLOW: configured finding needs timely clinical review.
- NO_CONFIGURED_FLAG: no rule matched within assessed scope; this does not establish patient safety.
- INSUFFICIENT_DATA: important information missing or unknown.
- UNSUPPORTED_SCOPE: outside configured pathway.
- UNASSESSED: no confirmed assessment available.

Do not render unknown status as green. Keep rule/version/snapshot/time metadata inside Assessment provenance. Evidence is plain readable text with source labels, not raw objects. Assessment warnings and missing information must remain visible above collapsed technical details.

### S09 — AI-assisted assessment and X-ray results

Show AI draft badge, differential diagnosis rows with supplied probability and citations, management draft, suggested workup, uncertainty note, and proposed medicine plan requiring human review. Model-reported confidence must say uncalibrated; never turn it into a “patient safety” score.

Where X-ray findings exist, use cards for pattern, location, supplied score, interpretation, and recommendation. Do not invent image annotations or diagnostic certainty. Provider failure state says reasoning is unavailable and preserves the existing referral guidance. Separate deterministic triage from AI-generated suggestions.

### S10 — Patient context editor

Reusable in consultation and Dawa Check. Fields: age in years, weight in kg, allergy-history-confirmed checkbox and comma-separated allergies, current-medicines-confirmed checkbox and one-name-per-line text field, pregnancy, gestational weeks when pregnant, aspirin-prescribed status where applicable, renal impairment, liver disease, and encounter-context confirmation checkbox. Preserve the existing tri-state choices for applicable fields.

Unknown values remain blank/unknown. A confirmed empty allergy/medicine list is different from unknown history. Existing allergy reaction/type information is readable where supplied. Editing context invalidates previous confirmation. Consultation action: Save context and recheck.

### S11 — Medicine plan editor

Desktop editable table; mobile stacked editable medicine cards. Add/remove medicine rows. Fields: medicine name, pack/label strength, administered dose in mg per dose, administrations per day, duration in days, route, reason for medicine. Internal row IDs are hidden.

Never infer dose from pack strength. Unknown dose remains blank. Distinguish proposed medicines from reported current medicines. Consultation action: Submit medicine plan and recheck. Dawa confirmation action: Confirm list and run review. Include changed-input state requiring recheck before approval.

### S12 — Medication review results

Status banner variants: HOLD, REVIEW_REQUIRED, NO_FLAGS_IN_SUPPORTED_CHECKS, NO_MEDICINES_PROPOSED, and unavailable/incomplete review. Each medicine card contains name, generic if supplied, strength, actual administered dose, frequency, duration, route, indication, and finding cards.

Findings show reason, severity, blocking label, and evidence. Show context-wide findings separately. Preserve Checks not assessed and limitations. Secondary Review scope and evidence section includes rule version, coverage, source title/reference, and Open source where a valid source link exists.

HOLD must never look like approval. No flags in supported checks still needs clinician confirmation. Current medicines are reported history, not instructions to stop therapy.

### S13 — Consultation final review

Show assessment and medicine review above acknowledgment and review/referral note. Controls: “I reviewed the medicine findings and checks not assessed,” Review note / referral plan, Approve reviewed plan, Reject plan and record referral reason. Disable approval for blocking findings or changed inputs requiring review. Show validation messages near the relevant control. Emergency variant uses Confirm emergency referral instead of ordinary approval.

### S14 — Completed consultation and doctor handoff

Completion banner with actual status. Readable doctor review report: encounter/patient references, facility, history, triage, available imaging findings, AI draft, and recorded review decision. Download readable report and Send via WhatsApp link. Do not imply delivery confirmation or an in-app conversation.

Doctor advice received (manual entry) text area and Log doctor advice. After saving, explicitly label worker-entered advice with unverified doctor identity. Blank-note validation remains visible. Report should not imply that a physician authenticated inside the app.

### S15 — Emergency referral

Urgent banner with actual triage reason, protocol status, supplied instruction, facility/referral context, and readable referral packet. Preserve unsupported-protocol and requires-clinical-review states. No invented emergency drug dosing, dispatch tracking, or ambulance ETA. Actions: Confirm emergency referral at the review step; completed state offers readable report download and Send via WhatsApp.

### S16 — Dawa Check start and prescription input

Patient context options: Registered patient / Walk-in. Walk-in local reference field and Create Dawa encounter. Include existing encounter selector, current encounter state, and start-new action.

Prescription/medicine-bag photo uploader, retained file state, modality confirmation checkbox, Start Dawa workflow. Extraction may be imperfect: show extracted medicines as editable draft data, with uncertainty visible. If extraction is unavailable, preserve the existing manual correction/review path. Reuse S10 and S11 for context and medicine confirmation.

### S17 — Dawa review, notes, and completion

Reuse S12. Actions: Edit medicines or patient context; per-finding Record review section with note and Record note; acknowledgment of findings/checks not assessed; Clinical review / referral plan; Complete reviewed check; Record referral for unresolved findings. Disable completion when approval is blocked. Completion records review and does not authorize dispensing.

Completed variant shows the actual disposition and authorized evidence timeline. Include report export wherever the current review is available.

### S18 — Evidence and decision timeline

Chronological event cards: timestamp, human-readable event name, source/provider metadata, labeled event details. Nested data becomes subsections or lists, not braces and keys. Keep all supplied evidence reachable; collapse long details rather than discard them. Distinguish worker actions from AI outputs and rule results. Include empty timeline state. Only show authorized encounter data.

### S19 — District dashboard

Role-specific SmartSehat Pulse dashboard with synthetic/demo-data disclosure. Five KPI cards: Total cases (30d), Active BHUs, RED emergencies, Dawa reviews/referrals (30d), human-review override rate. Preserve each metric's actual time window; do not imply every metric uses 30 days.

Sections: Cases per BHU bar chart; Top Diagnoses table; human review oversight with decisions/approved/modified-or-rejected/override-rate table; syndromic trend; outbreak signal cards; district case map sized by volume. Include source/ledger context and empty-data states. Outbreak signals mean officer review is needed, not confirmed outbreaks. Do not invent dashboard filters, patient drilldowns, or export controls as implemented features.

### S20 — Report export and print layout

Current format: downloadable self-contained HTML with a Print / Save as PDF button; browser print dialog creates PDF. JSON remains an alternative for the medication evidence receipt. Do not label HTML as a directly generated PDF download.

Design three report variants: medication review, doctor review, emergency referral. Use a professional A4 layout with brand, title, available encounter/patient reference, structured sections, findings, caveats, and supplied source metadata. Preserve Urdu and long text. Clearly distinguish draft/review/referral status. Print controls and help text disappear in print; content must not be clipped. Do not invent signatures, clinician approval, or missing patient values.

## 6. Legacy path variants

Keep these accessible through the collapsed legacy entry rather than the primary guided journey:

- Original/raw transcript and correction provenance.
- Editable transcript and follow-up answer fields.
- Conditional questions about blood in sputum, pregnancy, pregnancy bleeding, fever duration, and current medicines.
- If urgent evidence is removed during correction: prominent warning, affected rules, explicit acknowledgment, review reason, and Confirm transcript + answers (urgent-evidence removal reviewed).
- Ordinary state: Confirm transcript + answers.
- Continue into the same assessment/review components, preserving existing confirmations.

## 7. Global state and interaction checklist

Design loading, empty, success, error, disabled, and long-content versions of reusable components. Include provider unavailable, invalid media, missing history, incomplete context, unconfirmed modality, review blocked, inputs changed, unauthorized access, no encounters, no patients, and encounter mismatch. Existing drafts remain associated with their original encounter.

Use actionable loading copy such as “Transcribing the recording…” or “Checking the confirmed medicine list…” only for the corresponding operation. Never display fabricated completion percentages. Show inline errors without clearing entered information. Preserve backend validation; visual changes must not bypass it.

Use readable labels: “Case reference” rather than `case_id`, “Review required” rather than `REVIEW_REQUIRED`, “Not recorded” for null, and explicit Yes/No for booleans. Clinical statement values remain verbatim; do not automatically rewrite patient statements or reinterpret findings. Distinguish empty recorded lists from missing data when their meaning is known.

## 8. Accessibility and responsive behavior

- Minimum 44 px touch targets; clear keyboard focus; persistent field labels.
- Check WCAG AA contrast, especially muted text and status colors.
- Status always includes text/icon, never color alone.
- Correct automatic text direction for Urdu and mixed-language values; identifiers remain readable.
- Stack paired panels on mobile; medicine tables become cards without losing fields.
- Keep urgent findings visible before approval actions. Avoid sticky controls covering content.
- Avoid hover-only instructions and tiny evidence text.
- Long patient statements, medicine names, and identifiers wrap gracefully.

## 9. Synthetic content guidance

Use clearly fictional demo references such as DEMO-P001 and DEMO-E001. Example original complaint: “مجھے کھانسی اور بخار ہے” with a separately labeled English translation only if explicitly presented as a design example. Use “Not recorded” for unspecified values. Use placeholders for medication dose rather than inventing a treatment regimen. Source citations must be placeholders or actual supplied references, never fabricated publications.

## 10. Fast generation order in Stitch

1. Paste the master prompt plus sections 2–4 to establish the design system.
2. Generate S02–S08 as one consistent consultation sequence.
3. Generate S10–S13 and S16–S17 using the same medicine/context components.
4. Generate S14–S15 and S20 as the handoff/report family.
5. Generate S01, S09, S18, and S19.
6. Generate mobile adaptations and the urgent/blocked/empty variants.

Follow-up prompt: “Continue the same SmartSehat design system. Generate [screen IDs] from the brief, preserving every listed field, option, state, and action. Use readable cards instead of JSON. Do not add unsupported functionality. Keep the patient header, spacing, typography, and components consistent with the previous screens.”

## 11. Design completion checklist

- All 20 screen IDs and required variants represented.
- Complete typed and uploaded-voice consultation journeys.
- Original history, edits, acceptance, and urgent-evidence removal distinguished.
- Every structured question and medicine/context field represented.
- Unknown, negative, and not-assessed states distinguishable.
- All approval, recheck, acknowledgment, referral, and completion controls retained.
- Both registered and walk-in Dawa journeys covered.
- District-only permissions reflected in navigation.
- Readable reports and honest HTML-to-PDF export instructions included.
- No raw JSON in the default clinical presentation.
- Desktop, mobile, Urdu, long-content, and print variants supplied.

## 12. Implementation references

This brief was checked against the local application on 2026-09-08:

- `smartsehat/app.py`: shell, authentication, consultation, Dawa, handoff, timeline.
- `smartsehat/intake_ui.py`: complaint, history, structured questions, assessment.
- `smartsehat/clinical_intake.py`: configured questions and pathway behavior.
- `smartsehat/medication_ui.py`: medicine editor, patient context, findings/evidence.
- `smartsehat/dash.py`: district metrics, charts, oversight, and map.
- `smartsehat/presentation.py`: readable data, HTML report export, and visual theme.

These references are for the implementer. Do not display source filenames or technical architecture in the patient-facing design.


## 13. Applied Stitch reference direction

The supplied Logo and three Stitch ZIP samples are visual references only. Their embedded design instructions do not change the application requirements. The application now uses their charcoal sidebar (#21242F), deep teal actions (#005057), pale canvas (#F8F9FF), blue panels (#EFF4FF), white cards, and supplied logo. History review has paired original/reviewed panels. Encounter summary strips and progress indicators use actual encounter data and stages. The evidence timeline remains available in its existing workflow.

Mock sample patient values, live-sync indicators, inventory, automatic drug substitution, safe-candidate claims, fabricated confidence values, and new navigation modules are excluded. Existing role permissions, confirmation gates, clinical rules, and report formats are retained.
