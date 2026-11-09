# Consultation blueprint implementation audit

Updated October 22, 2026 after the implementation pass. Compared with the supplied final-round blueprint, one-week plan and detailed workflow. This supersedes the earlier code audit that identified missing structured history, graph orchestration, full SBAR/read-back, doctor tasks, scheduling, encryption and exports.

The later `blueprint_comparison.md` provides a complete step matrix and tracks addressed/open issues. Cache preparation, repeated-handoff timing, availability/failover, district/month context and selected-prescription regimen matching software are now present within the local prototype. Actual reviewed regional/dose entries and broader free-text/multiple-medicine clinical interpretation remain pending. Use that comparison when deciding whether the entire blueprint is finished.

The latest implementation is recorded in `consultation_integrations.md`: quick browser danger capture, opt-in experimental regional transcription with exact-text review, lossless resumable case packages, an inactive explicit delivery gateway and complete source-bound patient-guidance audio. The grouped run passed 134 checks, followed by nine focused checks after final refinements; the current suite has 137 tests. Fixed audio now covers 66 English and 21/65 Urdu prompts. Older counts/limits below describe earlier passes; the latest integration/comparison records take precedence.

## Implemented since the earlier audit

- Expanded the question catalogue from 38 to 48, with chief concern, onset/course/severity, respiratory negatives, diarrhoea and exposure collection. Optional rewording keeps the original question identity. Icons, observation instructions, photo guides and device speech are connected to collection.
- Added typed point-of-care tests, device-display proposals, separate cough recordings, per-medicine strength/form/amount/frequency/duration/adviser and structured allergy records. Medicine flags hold the handoff until acknowledged; dose approval remains unavailable.
- Added constrained ranked doctor considerations with positive/negative rendered evidence, missing information, next measurements/tests and versioned corpus references. AI concern can only raise urgency with current confirmed evidence.
- Replaced combined/partial narrative with complete S/B/A/R, source-tagged appendix and full bilingual read-back. Patient rejection, stale facts/media and unresolved proposals block the version from being sent.
- Added the new LangGraph review pipeline, encrypted checkpoint storage and worker/doctor interrupts. Selective consideration/medicine dependencies coexist with conservative whole-packet invalidation; the UI does not claim every field is a separate agent/subgraph.
- Expanded doctor tasks to question-bank history, vitals, typed tests, audio/photos and clarifications. Validated returned tasks are appended exactly once and recomposed. Reply drafts survive navigation and hold their reviewed snapshot when worker data changes.
- Added doctor-authorised local-language wording, manual sun/moon medication schedules, feeding/ORS/counselling fields, stock alternatives for review and printable patient guidance. All schedule medicines receive safety checks; free-text doses require independent doctor verification.
- Added persistent scheduled escalation/check-in jobs, worker notifications, restart/idempotency handling, ongoing follow-up rescheduling, unknown answers and contradictory-outcome rejection. External delivery stays simulated.
- Added locally encrypted payloads/media/cache/checkpoints, storage migration, draft recovery, explicit retention preview/apply and FHIR R4 document-shaped export. Consent withdrawal cancels remote sharing; regrant requires a fresh handoff.

## Additional consultation closure

The cache helper shares the app's contract-bound encrypted cache and preserves valid responses. Roster availability/reassignment persist with audit; handoff IDs guard old replies and per-cycle timings. District/month context now has confirmed location/date, source matching, read-back/export, correction and reference-hash guards. Doctor-entered selected-product regimens support per-dose, absolute amount, daily-cap, frequency, duration and indication comparisons only where reviewed limits exist. The latest grouped run passed 99 checks, followed by eight context checks after the final location/current-reference UI changes. Actual regional/dose tables remain empty/pending; real delivery, clinical approval and measured doctor effort remain unestablished.

Doctor-selected tests now create local/lab worker orders with typed returned results, exact laboratory reports, optional PDF/photo attachment, unavailable reasons, explicit cancellations/repeats and doctor result acknowledgement. Provisional responses keep the graph waiting for the doctor; final responses start follow-up. Background packet recomposition no longer overwrites the active human gate. A recorded guidance file requires doctor approval and can be embedded in the patient card for offline listening. Call requests and ordered tests are displayed on that card. Recovery guards also check outstanding tests and changes after the last doctor response.

## Counselling and handoff closure

Reply-specific counselling confirmations now record the reported explanation method and patient/caregiver understanding. Clarification, transport barriers and unavailable contacts reopen the doctor queue and persist through restart, with explicit resolution in a new doctor response. Referral/contact states, cancellations, corrections, unavailable media and linked-task consistency are implemented. Recovery cannot close outstanding tasks, requests or handoffs. Reported referral remains open while transfer is pending. Current care reports accompany the encounter/FHIR narrative and portable patient card. These reports remain human-entered and simulated; they do not establish independent comprehension, clinician identity or facility receipt.

## Worker continuity

Worker continuity now includes a saved action list across cases, prioritised urgent/due/task work, and encrypted drafts for every task answer type and the five follow-up answers. A worker-confirmed urgent-change action remains available while the doctor awaits tests or before a final plan, independently of recovery/outcome validation. Urgent observations reopen the doctor review and remain local after sharing withdrawal or regrant without resend. The current UI warns when doctor guidance predates new case media as well as fact corrections.

## Packet and patient-wording integrity

The latest integrity pass makes initial pregnancy explicit in the ledger/read-back and includes pregnancy/language in the packet header and doctor view. It guards packet generation against concurrent worker requests and intake/consent changes, marks older packets stale, and retains local clarification requests through resend. Patient-wording assistance provenance persists across sessions; the doctor approves the exact current advice/text/case, and altered quantity-unit pairs are rejected. This does not establish general translation accuracy.

## Guided measurement and date continuity

Doctor tasks now provide the breath timer and temperature unit selector directly, with validated typed returns, original-unit audit and saved count drafts. DOB provenance/reference dates persist through the ledger, SBAR/read-back and FHIR export; conflicting typed age/DOB requires confirmation. Care calendar checks and readable follow-up times use the configured local timezone while audit/scheduling remains UTC. Existing encounters retain their recorded age rather than silently changing pathway on reopen.

## Intentional narrower implementations

| Requirement | Implemented scope / remaining boundary |
|---|---|
| Clinical approval | Pending physician/pharmacist register; no reviewer or dose ranges invented |
| Claim verifier | Exact deterministic English/Urdu or canonical ledger statements, not an LLM entailment judge for arbitrary prose |
| Consideration ranking | Evidence coverage/catalogue order, not calibrated diagnostic probabilities or a diagnosis |
| Audio/local language | English/Urdu browser speech, routed STT and doctor-confirmed human recordings embedded in patient cards; target-device/native-speaker validation, prerecorded MP3s and other languages remain pending |
| Clinical pathways | Deep respiratory/fever collection, universal/maternal safety and general collect-and-send; the app does not claim full clinical protocols for every disease |
| Graph architecture | Review pipeline/interrupts are real; transactional collection/scheduling remain service-driven rather than per-pathway graph subgraphs |
| Offline | Standalone local rules/capture and manual transfer, not automatic sync or the full server UI offline |
| Real services/accounts | Simulated clinician identity/routing/notifications/facility stock as expressly allowed by the one-week plan; no real external delivery |
| Retention | Explicit scoped preview/apply tool; institutional deletion policy is not automatically selected |
| FHIR/DHIS2 | Document-shaped FHIR R4 export with reference checks; no provincial implementation-guide validation or deployed integration |
| Independent evidence | Authored software tests and one synthetic live extraction; clinical accuracy, phone usability and measured doctor effort/outcomes not established |

Presentation assets are outside this implementation pass. Current feature and verification records are in `implementation_status.md` and `verification.md`.
