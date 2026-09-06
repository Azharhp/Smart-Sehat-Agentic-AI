# SmartSehat implementation status — phases 1 and 2

Date: 2026-09-07
Branch: `master`
Protected baseline HEAD: `f2ca27c98bb392845ea6bb349e2bc5f316926764`

This record covers Phase 1 (A01, A02, the requested part of A32, A48 and A51) and the Phase 2 identity/ownership foundation (A11, A25, A30 and A31). It does not revise the broader audit or claim that later backlog items are complete.

## Repository protection and baseline

The working tree already contained developer edits to `config.yaml`, core SmartSehat modules, audit artifacts, runtime media and SQLite WAL/SHM files. Those files and all operational databases were preserved. A secondary worktree exists at `.claude/worktrees/hi-session-042b33` on `claude/hi-session-042b33`; it was not changed.

The audit's original 34 Consultation and 24 Dawa checks described the pre-implementation behavior. By the time this implementation request was attached, the unsafe assertions were already being replaced in this same task. The exact 34/24 snapshots therefore could not be rerun unchanged without undoing authorized work. Their current successors pass 35/35 and 45/45 and retain the useful triage, HITL, edit-loop and ledger coverage while asserting the new truthful behavior.

## Traceability

| Backlog ID | Priority | Status | Files Changed | Tests | Acceptance Evidence | Remaining Work |
|---|---|---|---|---|---|---|
| A01 | P0 | BLOCKED — CLINICAL REVIEW REQUIRED | `smartsehat/emergency.py`, `agents.py`, `graph.py`, `state.py`, `app.py` | `test_emergency_protocols.py`, workflow and AppTest regressions | All five configured RED categories preserve the actual trigger, use the single protocol builder in graph/UI handoff, produce `UNSUPPORTED_EMERGENCY_PROTOCOL` + `REQUIRES_URGENT_REFERRAL`, and contain no medicine or dose. The universal aspirin output is absent. | A licensed/local clinical authority must approve scenario-specific protocols before any treatment content can replace the explicit unsupported state. Engineering removal and safe referral fallback are complete. |
| A02 | P0 | IMPLEMENTED | `medication_review.py`, `medication_ui.py`, `dawa.py`, `dawa_graph.py`, `agents.py`, `graph.py`, `state.py`, `app.py`, `audit.py`, `dash.py` | 38 rule tests, 29 workflow tests, 11 component UI tests, 9 whole-app tests, 35 Consultation checks, 45 Dawa checks | Consultation and Dawa use the same versioned review function. Structured edits re-run it; input hashes reject stale receipts; HOLD cannot be acknowledged away; unknown/unsupported inputs remain visible; evidence is attached. No source/runtime event emits `PHARM_CHECK_PASSED` or `insurance_status`. | Independent clinical/pharmacy validation and local formulary sign-off remain required before clinical deployment; the UI states this limit. |
| A32 | P1 | PARTIALLY IMPLEMENTED | `.gitignore`; Git index entry for `assets/uploaded/dawa_rx.jpg` | UTF-8/runtime-pattern regression plus `git check-ignore` and `git ls-files` verification | `.gitignore` is plain UTF-8 and covers uploads, DB/WAL/SHM/journal, cache, build and secret artifacts. The formerly tracked upload is removed from the index but preserved locally and ignored. Existing sidecars and uploads were not deleted. | The audit found no proof of external disclosure. The historical copy of `assets/uploaded/dawa_rx.jpg` remains in existing commits; classify its content and, if it is not approved synthetic data, perform coordinated history remediation separately. Other A32 repository cleanup is outside this phase. |
| A48 | P1 | IMPLEMENTED | `tests/__init__.py`, `tests/test_*.py`, `test_flow.py`, `test_dawa.py`, `test_real.py`, `test_qwen_asr.py`, `.github/workflows/offline-tests.yml` | 111 discoverable tests; live module skipped; script suites 35 + 45 | Standard `unittest discover` finds the suite. Tests use temporary audit/checkpoint/media DBs and roots, block sockets/provider routes, and exercise the prior audit failures plus Phase 2 authorization and concurrency criteria. Live probes require `SMARTSEHAT_RUN_LIVE=1`. CI sets offline mode explicitly and runs the same commands. | The new GitHub workflow is syntactically present but has not yet run on GitHub infrastructure in this local-only phase. |
| A51 | P1 | IMPLEMENTED | `router.py`, `agents.py`, `dawa_graph.py`, `app.py`, offline/app tests | `test_offline_foundation.py`, `test_app_medication.py`, workflow suites | `SMARTSEHAT_OFFLINE=1` forces stub mode even when project config says real. Router calls and health probes fail closed/return disabled; Consultation and Dawa show deterministic/manual states. Whole-app AppTests block sockets and complete Consultation plus Dawa entry without a call. | Offline Dawa image extraction intentionally requires manual medicine confirmation; it does not simulate OCR success. |

## Phase 2 traceability

| Backlog ID | Priority | Status | Files Changed | Tests | Acceptance Evidence | Remaining Limitations |
|---|---|---|---|---|---|---|
| A11 | P0 | IMPLEMENTED | `smartsehat/domain.py`, `access.py`, `migrations.py`, `graph.py`, `dawa_graph.py`, `state.py`, `audit.py`, `seed.py`, `seed_district.py` | `test_identity_encounters.py`, `test_medication_workflows.py`, `test_flow.py` | `ActorContext`, `PatientContext`, `ClinicalContextSnapshot`, and `EncounterContext` establish one canonical encounter. Consultation and Dawa freeze patient/walk-in, actor, facility, workflow, timestamps, status and versions before execution. Snapshot updates append versioned rows and preserve prior snapshots, including weight and explicitly reported aspirin-prescribing context. Consultation context changes require the update/re-review action; Dawa context can change only at its confirmation gate. Out-of-sequence, approval and finalization payloads cannot alter canonical context, and every final decision is bound to the current canonical snapshot. Explicit Dawa walk-ins have `patient_id=NULL`, `identity_kind=walk_in`, a local reference, and an unregistered verification state. Unknown clinical values remain `None`; no values are invented. | The existing patient table stores age rather than date of birth. Legacy encounters receive explicit migration metadata but no fabricated snapshot, so the new service refuses to resume them until a separately designed reconciliation process establishes canonical ownership. |
| A25 | P0 | IMPLEMENTED | `smartsehat/ui_state.py`, `app.py` | `test_ui_state.py`, `test_app_medication.py` | Consultation and Dawa state is keyed by encounter ID. Transcript, follow-up, medicine/context, hash-scoped acknowledgment/review notes and pending upload values are retained in encounter drafts; stored artifact IDs remain case-owned. Backend-normalized medicines and assigned IDs replace the medicine draft after medicine submission, while action-scoped reconciliation preserves an independently edited context draft. Both the pure state test and a real AppTest Case A → Case B → Case A sequence prove clean B drafts and restored A transcript/context. Whole-app tests prove acknowledgment clears when the review input hash changes and an unsaved allergy survives a medicine-only recheck, keeping approval disabled. Ownership fields cannot be changed in UI state. The active banner is loaded from the stored encounter; a changed sidebar patient produces an explicit continue/start-new choice and never rebinds the case. | UI drafts persist for the current Streamlit session. Graph checkpoints and canonical context persist separately, but reconstructing every presentation-only draft after a browser/server session loss is not included in this phase. |
| A30 | P0 | IMPLEMENTED | `smartsehat/auth.py`, `domain.py`, `access.py`, `app.py`, `dash.py`, `graph.py`, `dawa_graph.py`, `seed.py`, `seed_district.py` | `test_identity_encounters.py`, `test_app_medication.py`, workflow regressions | Editable worker identity was removed. The application receives a typed authenticated actor containing ID, name, role, memberships, active facility, permissions and auth source. Service checks cover patient read; encounter create/read/resume/mutate; media create/read; existing export/handoff; and district view. Cross-facility calls fail, a multi-facility worker must activate the patient's facility, and current reviewers are attributed separately from the immutable encounter creator. DHO has only district view; health workers cannot call district view; same-facility authorized doctor/reviewer access works. Completed context cannot be resumed or mutated. | The included account selector is explicitly `demo-local`, suitable only for the hackathon/local environment, and remains visibly marked after login. `SMARTSEHAT_AUTH_MODE=production` fails closed because no production identity provider, token/session validation, account lifecycle or audit-grade login system is configured. |
| A31 | P0 | IMPLEMENTED | `smartsehat/media.py`, `migrations.py`, `app.py`, `graph.py`, `dawa_graph.py`, `.gitignore` | `test_private_media.py`, `test_app_medication.py`, `test_flow.py` | The encounter is allocated before upload. Each artifact receives a UUID-derived ID and case/facility-owned path plus patient identity reference, uploader, original basename, detected MIME, size, SHA-256, timestamp, purpose, status and modality confirmation metadata. Writes use a same-directory temporary file, flush/fsync and atomic replacement. Pillow/WAV parsing rejects spoofed, corrupt, empty and oversized content. Purpose and encounter-ID checks prevent prescription/X-ray reinterpretation and same-facility cross-case attachment; validation occurs before activation. Replacing or clearing an image resets its modality attestation. Concurrency tests prove later Case B/C uploads and duplicate names cannot overwrite Case A and unauthorized Facility B cannot read or write A. | Supported upload types are WAV, JPEG and PNG, with a configurable default 10 MiB limit. Image modality requires worker attestation because this phase does not add a medical-image classifier. Deployment-level filesystem encryption, key management, backup controls and retention execution remain environment responsibilities. |

## Architectural result

Consultation and Dawa now share one deterministic medication review engine and one UI vocabulary. The engine evaluates only its documented finite scope, emits `HOLD`, `REVIEW_REQUIRED`, `NO_FLAGS_IN_SUPPORTED_CHECKS`, or `NO_MEDICINES_PROPOSED`, and records the rule version plus an input fingerprint. Package strength is never interpreted as an administered dose. Human edits or patient-context changes invalidate the previous review. Review notes retain findings rather than removing them.

Emergency output now comes from one builder. Because this repository has no clinically approved scenario-specific emergency treatment set, every current RED category produces a truthful unsupported-protocol state and urgent referral instructions without a medication order. The same stored payload supplies the graph gate and UI referral packet.

## Commands and local results

```text
.venv\Scripts\python.exe test_flow.py
35 passed, 0 failed

.venv\Scripts\python.exe test_dawa.py
45 passed, 0 failed

.venv\Scripts\python.exe -m unittest discover -v
111 discovered: 110 passed, 0 failed, 1 live-provider module skipped by design

.venv\Scripts\python.exe -m unittest -v tests.test_identity_encounters tests.test_ui_state tests.test_private_media
15 passed, 0 failed

.venv\Scripts\python.exe -m compileall -q smartsehat tests seed.py seed_district.py test_flow.py test_dawa.py test_real.py test_qwen_asr.py demo_medication_review.py
passed

.venv\Scripts\python.exe demo_medication_review.py
8/8 synthetic examples matched; ruleset 2026-09-06.1

.venv\Scripts\python.exe test_real.py
skipped before provider access: SMARTSEHAT_RUN_LIVE=1 not set

.venv\Scripts\python.exe test_qwen_asr.py
skipped before provider access: SMARTSEHAT_RUN_LIVE=1 not set

git diff --check
passed

git check-ignore -q runtime/private-media/example.bin
passed (runtime private-media path is ignored)
```

The test sockets were instrumented to fail on any connection attempt; the mocks recorded zero calls. No paid API was called. Temporary test databases, migration copies, media roots and checkpointers were used. Existing application databases, WAL/SHM sidecars and uploaded files were not modified or deleted for testing.

## Phase 2 domain, schema and storage result

Schema migration version 1 is additive and idempotent. It adds `patients.bhu_id`; canonical ownership/status/version/snapshot columns to `cases`; and `schema_migrations`, `user_facilities`, `patient_context_snapshots`, and `media_artifacts`. Legacy rows retain their existing IDs and clinical/audit fields. Migration assigns patient facility only when all linked legacy encounters agree; ambiguous ownership remains `NULL` for reconciliation. It records unresolved snapshots as version 0 and does not create fake patients or clinical context. A test builds and migrates a temporary legacy database copy and verifies its original row values remain present.

Consultation and Dawa now call the same authorization/encounter service before graph start and every resume. Graph thread identity equals the canonical encounter ID. Runtime uploads live outside fixture/source directories under a private configured root; callers pass artifact IDs and the repository resolves authorized paths only after validating encounter ownership, permission, stored purpose, size and hash.

## Clinical status

Software evidence proves deterministic behavior for the recorded synthetic examples. It does not prove clinical accuracy, sensitivity, patient benefit, prescribing authority, comprehensive interaction coverage or safety in real patients. The evidence register and exact supported scope are in `docs/MEDICATION_EVIDENCE.md`; independent clinical review remains pending.

## Recommended next dependency-aware package

Stop after Phase 2. The next package should build the structured clinical-intake and triage inputs in dependency order (A03–A10 and A26) on top of the new canonical patient/encounter snapshot. Keep A01 blocked pending licensed/local clinical approval, and do not expand the medication rule vocabulary until a licensed reviewer and local formulary owner can sign each rule and product mapping.
