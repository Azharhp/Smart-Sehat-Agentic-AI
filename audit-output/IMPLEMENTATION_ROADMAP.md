# SmartSehat completion and capability roadmap

This is a proposed implementation sequence, not a record of completed changes. It is based on the audited working tree and the 51 work items in PRIORITIZED_BACKLOG.md. Estimate effort after agreeing the supported clinical scope, target devices, deployment model and clinical reviewers; those decisions affect delivery substantially.

## Product direction

Build a reliable BHU care workflow: identify the patient, collect the needed history, recognize urgency promptly, review a supported care plan, reconcile medicines, arrange referral or follow-up, and verify the result. The strongest differentiator is continuity across those steps under rural connectivity and staffing constraints.

Use the current Python/Streamlit/LangGraph foundation for remediation. Introduce clear domain contracts and service boundaries before changing the frontend or splitting into services. More models or longer fallback chains will not resolve missing patient context, incorrect medication rules, unreliable state or incomplete handoff.

WHO's digital adaptation kits provide structured operational requirements for translating reviewed clinical guidance into software. Use applicable kits as reference material with local clinical adaptation rather than writing the whole clinical workflow from ad hoc prompts. [WHO SMART guidelines and digital adaptation kits](https://www.who.int/teams/digital-health-and-innovation/smart-guidelines).

## Milestone 1: Truthful and safe demonstration

**Deliverables:** remove universal ACS instructions; replace automatically passed consultation medication checks with a real check or explicit handoff to Dawa Check; remove unrelated insurance placeholder fields (insurance is outside the three-module product scope); preserve ASR emergency evidence; validate gate actions; introduce missing-data status; bind widgets and media to cases; fix medicine editing; fix complete handoff serialization; enforce genuinely offline demo behavior; repair repository exclusions and documentation; establish regression tests.

**Relevant work:** A01–A12, A19–A20, A25–A28, A31–A32, A48, A50–A51. Some require later clinical work before they can be fully closed; a truthful unsupported status is an interim demo safeguard, not implementation of a medical checker.

**Exit evidence:** all existing 58 checks still pass; new tests reproduce and close each changed defect; no network is used in offline mode; no cross-case input survives a new encounter; every demo output accurately labels its source and limitations. Demo data is isolated. No clinical-readiness claim is made from this milestone alone.

## Milestone 2: Complete one supported care pathway

Choose one narrow pathway with local clinician ownership, such as respiratory/TB assessment and referral. Selection is a product/clinical decision, not an endorsement of the current TB rules.

**Deliverables:** canonical encounter snapshot; structured observations with units and verification status; age-appropriate intake; prompt emergency branch; supported workup and management state; a shared medicine-reconciliation service; evidence tied to specific actions; authenticated worker/doctor roles; a complete review/disposition and referral/follow-up state machine.

**Relevant work:** A05–A24, A29–A35, A38–A40, A44–A46.

**Exit evidence:** clinician-reviewed positive, negative and uncertain scenarios; a held-out evaluation set; no dangerous action approved solely by a malformed model response; all decision actors and supporting evidence are reconstructible; unsupported patients or complaints have an explicit escalation path. The intended scope and unresolved exceptions are published for the pilot team.

## Milestone 3: Controlled multi-user pilot

**Deliverables:** constrained migrated schema; transactional/idempotent writes; case resume and pending-work queue; private media and agreed retention; authorized facility access; actual provider destination policy; total latency/cost budgets; measured SQLite concurrency or an evidence-based database change; backup/restore runbook; clean builds and deployment checks; honest and consistent analytics.

**Relevant work:** A30–A49, plus any unresolved clinical blockers from milestones 1–2.

**Exit evidence:** two-facility access tests; interrupted/replayed finalization tests; restore rehearsal including checkpoints/media; fresh installation; device/keyboard/RTL usability testing; known-count dashboard fixtures; no demo data in operational indicators; all remaining risks owned and accepted through the project's clinical/operational governance. Independent deployment or regulatory requirements still require the appropriate assessment.

## Milestone 4: Distinctive capabilities with measurable value

Choose two or three capabilities below after the supported care pathway works. The first five are the strongest candidates for demonstrating value without broad autonomous prescribing.

| Proposed capability | Concrete problem solved | Minimum implementation | Proof of value | Effort / dependency |
|---|---|---|---|---|
| **1. Evidence-backed safety card** | Workers see a color without knowing the evidence | Exact patient observation, matched rule, source passage, rule version and next action in one readable card | Every urgent flag can be traced in one interaction; worker can identify why it fired | Medium; typed observations and governed rules |
| **2. Offline encounter capture and recovery** | Lost connectivity interrupts documentation and follow-through | Save structured intake locally, clearly indicate sync state, preserve drafts and reconcile conflicts; reviewed local protocols within scope | Deliberately disconnect, complete supported intake, restart, reconnect and sync once without loss | Large; privacy, local storage, idempotency and conflict rules |
| **3. Referral tracking through arrival/review** | A shared message does not prove the patient reached care | Referral created/accepted/departed/arrived/reviewed states, responsible roles and acknowledgment | Demonstrate a referral's actual receipt and outcome; count unresolved referrals | Medium–large; authenticated actors and authorized communications |
| **4. Medicine reconciliation with explainable findings** | Current and proposed medicines are evaluated separately | Confirm OCR identities, normalize all ingredients, show duplicate/DDI/allergy findings and explicit keep/hold/refer disposition | Find the same risk regardless of brand, combination product or list ordering | Large; reviewed drug knowledge and safety coverage |
| **5. “What is missing?” intake guide** | Workers cannot see the most important unanswered question | Clinician-authored yes/no/unknown questions selected from missing fields, retaining units and original answers | Required relevant observations are captured with fewer unnecessary questions; emergency escalation is never delayed | Medium; clinical content and adaptive state |
| 6. Patient visit card with protected QR reference | Patients lose paper histories or attend different facilities | Printable local-language summary plus a non-sensitive encounter reference; authorized retrieval | Another authorized facility retrieves the right record; QR does not expose clinical details publicly | Medium; identity, access controls and export |
| 7. Authentic doctor work queue | External advice has no verified author or ownership | Assigned/pending/needs-info/reviewed cases, authenticated replies and final-plan changes | No case marked doctor-reviewed without a real reviewer; time-to-review visible | Medium; milestone 2 review model |
| 8. Lab/result follow-up | Suspected disease and testing advice stop at a recommendation | Requested/collected/received/reviewed result states with responsible worker and follow-up | Pending results cannot disappear; positive result review is attributable | Medium; data contracts and local lab process |
| 9. Local-language audio explanation and teach-back | Patients may not understand written instructions | Clinician-approved templates, accessible audio, confirmation of comprehension | Patient/worker study verifies understanding and correct interpretation | Medium–large; language validation, consent, content review |
| 10. Audio/image quality and uncertain-span review | Bad input creates false confidence | Playback/preview, input-quality warnings, uncertain words/drugs highlighted for confirmation | More errors corrected before assessment; no low-quality input mislabeled as validated | Medium; media provenance and provider capabilities |
| 11. Maternal and child care pathways | Generic adult inputs cannot support age/pregnancy-specific care | Separate reviewed pathway contracts, gestation/age units, missing-data states and referrals | Pathway-specific clinical evaluation meets agreed criteria | Large; separate clinical validation scope |
| 12. Verified dose calculation assistant | Strength on a label is confused with administered dose | Structured weight/concentration/amount/frequency and reviewed formulas with visible math | Golden boundary/unit fixtures pass; missing inputs yield unassessed status | Large; governed formulas and product knowledge |
| 13. Facility capability and stock availability | Advice may require a test or medicine unavailable locally | Verified service hours, capacity and stock timestamps; availability-aware referral options | Recommendations identify unavailable resources and actionable alternatives | Medium–large; maintained operational data; no unreviewed substitutions |
| 14. Adherence and follow-up tracking | Chronic care and prescribed follow-up are easily missed | Patient-approved schedules, visit outcomes, missed-follow-up queue and opt-out | Follow-up completion improves relative to a measured baseline | Medium; permissions, consent and communication integration |
| 15. Guideline update and conflict review | Old and new guidance coexist silently | Source version/expiry/change comparison and reviewer approval before rule release | One rule's update can be traced to reviewed evidence and tested scenarios | Medium; provenance archive and clinical ownership |
| 16. Recommendation evidence export | Reviewers cannot reconstruct a recommendation later | Minimal authorized bundle containing accepted input versions, actual model metadata, rule/corpus versions and human decisions | A reviewer reconstructs the approved result without access to unrelated patient data | Medium; versioned events and privacy controls |
| 17. Honest district outbreak investigation | A heuristic spike is shown as an outbreak without context | Current calendar windows, completeness, baseline, suspected/confirmed labels, investigation/acknowledgment workflow | Old/demo/incomplete data cannot create a current operational alert | Medium; accurate reporting model and epidemiological evaluation |
| 18. Language/provider quality dashboard | A handful of demo clips hides model weaknesses | Held-out, consented/de-identified evaluation by language/noise, correction rate, latency and supported tasks | Route changes require improved measured quality within policy and budget | Medium–large; evaluation datasets and actual provider metadata |
| 19. Patient-controlled processing preferences | Automatic failover can surprise patients or facilities | Approved-provider sets per use, recorded choices and transfer receipt | Provider failure never expands allowed data destinations | Medium; policy and consent infrastructure |
| 20. Duplicate-patient review | Multiple records fragment history | Candidate matching with human merge review and reversible identity links | No automatic merge; history and audit ownership remain consistent | Medium; patient enrollment and identity governance |
| 21. Portable interoperability export | Future labs/district systems require structured exchange | Validate an agreed FHIR profile for encounters/observations/medication requests and map local terminology | A partner validator accepts the agreed bundle; data does not lose units/provenance | Large; actual integration partner and version agreement |
| 22. Operational recovery console | Unfinished cases cannot be distinguished from failed writes | Pending cases, checkpoint/event reconciliation, retry status and protected diagnostics | Simulate interrupted writes and recover without duplicate clinical actions | Medium; storage idempotency and roles |
| 23. Worker training sandbox | New users need practice without altering operations | Isolated synthetic scenarios with expected decisions and feedback | Training records never reach live statistics or patient history | Medium; reliable demo separation and clinical training content |
| 24. Resource-aware referral comparison | The nearest facility may lack the required service | Show distance estimates, verified capability/contact and last update; worker chooses destination | Selected destination can provide the service and acknowledges the case | Medium–large; trustworthy facility data and current transport context |

These are design proposals, not capabilities already installed. Validate need with BHU workers, doctors and district staff before committing to each one. FHIR provides a structured representation of medication requests and their encounter context, but interoperability requires a specific profile/version agreed with partners; it is not achieved by renaming local JSON fields. [HL7 FHIR MedicationRequest definitions](https://hl7.org/fhir/R4/medicationrequest-definitions.html).

## Proposed code structure

Refactor incrementally. The following is a target responsibility map, not a request to create empty directories or move all files immediately.

```text
smartsehat/
  settings.py                validated environment configuration
  domain/
    patient.py               identity and verified clinical context
    encounter.py             typed states and lifecycle
    observations.py          units, time, assertion, source and verification
    medication.py            products, ingredients, dose and dispositions
    evidence.py              clinical rule/source/model provenance
  clinical/
    triage.py                reviewed deterministic rules
    medication_safety.py     shared consultation + Dawa checker
    protocols/               versioned clinical content
  workflows/
    consultation.py          thin orchestration and gate contracts
    dawa.py                  extraction/review orchestration
  services/
    encounters.py            permission-checked use cases
    reviews.py               authenticated human decisions
    referrals.py             handoff and outcome workflow
    reporting.py             canonical metric definitions
  providers/
    contracts.py             typed request/response and error types
    router.py                approved routing, deadlines and telemetry
    adapters/                provider-specific request translations
  repositories/
    encounters.py            constrained data access
    events.py                transactional/idempotent event writes
    media.py                 private artifact storage
  ui/
    consultation.py          case-scoped frontend state
    dawa.py                  medicine editor/review
    district.py              authorized operational reporting
    components.py            patient banner, evidence and status cards
tests/
  unit/                      pure rule/contract tests
  workflows/                 interrupted/replayed graph tests
  ui/                        synthetic AppTest regressions
  integration/               explicit live synthetic tests
  fixtures/                  licensed de-identified/synthetic gold cases
migrations/                  schema and data transitions
tools/                       seed and provider exploration commands
docs/                        scope, architecture, operations and evidence
```

Keep raw media, production databases, checkpoints and backups in configured private runtime locations outside the source tree. Packaging should describe runtime/test dependencies and supported Python versions. CI should verify the same path used to release.

## Database implementation plan

1. Create a migration inventory and a backup/restore rehearsal before changing the current schema. Explicitly classify the existing RX-WALKIN references and system events; do not force a foreign key onto unexplained records.
2. Introduce patient, encounter, facility membership and workflow-type relationships, constrained statuses, source/demo markers, structured observations, reviewed medications and private media IDs.
3. Use a versioned encounter snapshot for clinical inputs. Store raw, corrected and accepted observations distinctly. Missing, negative and unassessed must remain different states.
4. Define immutable decision/event records with actor, event identity, input version, event type/version, event time and relevant evidence. Store independently verifiable integrity metadata if tamper evidence is a requirement; a hash chain alone cannot stop a privileged attacker from rewriting both records and hashes.
5. Make event plus projection writes transactional. Use idempotency/expected-version checks so graph replay cannot duplicate actions. Add a recoverable outbox for external actions rather than sending from an uncoordinated graph retry.
6. Normalize requested actions separately from final statuses. Define operational metrics over explicit cohorts and diagnose states; keep analytics projections rebuildable and reconcile them to source events.
7. Add indexes justified by measured query plans. Use configured short transactions, busy handling and tested backups for a local SQLite pilot. Move to a managed relational database only when concurrent writers, multiple application instances, operations or availability justify it.
8. Set data minimization, retention and authorization requirements for every copy, including checkpoints and exports. Test restore and permitted deletion without silently destroying required decision history.

## What to measure before expansion

| Dimension | Useful measure |
|---|---|
| Patient safety | Missed/red-flag over-alert rates on clinician-reviewed cases; unsupported-input behavior; adverse medication fixture recall |
| Data integrity | Cross-case attribution failures; duplicate decisions; orphan references; restore/replay consistency |
| Frontline usability | Time and steps to complete a supported encounter; correction effort; successful recovery after interruption |
| Clinical support | Whether approved workup/management is visible and useful; relevant evidence support; unnecessary questions |
| Continuity | Referrals acknowledged/arrived; lab results reviewed; follow-up completed |
| Reliability | Time to visible emergency action; completed encounters during provider outage; total operation latency and cost |
| Equity and language | Errors/corrections by language, script, accent/noise and supported age group with adequate sample sizes |
| Reporting | Cohort reconciliation, freshness, completeness, demo exclusion and alert investigation outcomes |

Agree numeric targets with clinical and operational owners using representative data. A small demo set with 100% success is not evidence of field accuracy. Do not optimize model confidence scores as a substitute for outcomes or calibrated evaluation.

## Demonstration worth building

Use one synthetic patient journey: a worker records and confirms history, answers a labeled follow-up, sees the exact observation behind an urgent flag, reconciles a medicine bag, and sends a complete approved handoff. Simulate one provider failing; the permitted fallback or local supported workflow remains clear. The receiving doctor acknowledges and updates the plan. The patient's follow-up and the district aggregate change once, with the demo environment visibly isolated.

This produces a stronger “wow” factor through explainability, continuity and recovery. Any external communication in such a demonstration still needs explicit task authorization; the current audit sent no messages and created no live integrations.
