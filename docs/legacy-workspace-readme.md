# SmartSehat — Consultation workspace

The consultation now has a guided worker flow, confirmed Fact Ledger, typed ages,
configurable danger screening, measurement validation, browser breath timer,
structured medicine/allergy reconciliation, exact fact verification, full SBAR
and bilingual read-back, checkpointed review, doctor tasks/replies, patient
guidance and persistent scheduled follow-up.

**Run on Windows:** `./run.ps1` (or `.venv/Scripts/python.exe -m streamlit run smartsehat/app.py`).
Use the sidebar for **Consultation**, **Doctor console**, **Split-screen demo**,
**Dawa Check** and **District View**. The new consultation needs no seeding and
uses its own `consultation.db`, preserving existing district/medicine data.

**Consultation workflow:** Identify and consent → danger screen → patient story
and confirmed AI proposals → adaptive questions → measurements/tests → medicine
and allergy reconciliation → current packet and full read-back → acknowledge
medicine flags and send → doctor review/tasks/plan → patient guidance → follow-up.
Live AI is optional per operation with cached/manual fallback and a bounded
wait. All cases must be fictional; clinician identity and external delivery are
simulated by default. An optional partner gateway remains inactive until server
credentials and explicit dispatch confirmation are supplied. Clinical rules and medication data
await physician/pharmacist review; no patient dose is generated.

```powershell
.venv/Scripts/python.exe -X utf8 -m unittest discover -s tests -p 'test_consultation*.py'
.venv/Scripts/python.exe -m scripts.consultation_evidence
.venv/Scripts/python.exe -m scripts.warm_consultation_cache
```

See [danger-screen completion and external review status](docs/danger_screen_completion.md), [integration/transfer update](docs/consultation_integrations.md), [steps 1–7 update](docs/steps_1_to_7_status.md), [implementation status](docs/implementation_status.md), [architecture and current limits](docs/consultation_architecture.md),
[clinical review register](docs/clinical_content_register.md), and the generated
[synthetic test report](docs/vignette_results.md). Save `assets/smartsehat-offline.html`
for browser-side safety capture independent of the server; its export is imported
as proposals requiring worker confirmation. It is not automatic offline sync.

Encounter payloads, uploaded media, model caches and workflow checkpoints are
encrypted locally. Preserve the matching ignored `.consultation-keys` folders
when backing up the case/workflow databases and media. Exports contain readable
data. Production accounts/access control and real-patient deployment are outside
this prototype's scope.

Doctor-selected tests create worker orders automatically. Local results use typed
units/choices; lab results preserve the issued report with an optional PDF/photo.
Pending and returned orders remain visible for review, cancellation or explicit
repeat. Provisional replies keep the doctor loop open; recovery follow-up starts
with the final plan. Doctor-confirmed recorded guidance can be included in the
downloaded patient card for playback without a connection.

Workers can record counselling and reported understanding against the exact
doctor response. Clarification questions, contact problems and referral barriers
return to the doctor queue with an audit trail. Reported transport/arrival,
contact completion, explicit cancellations and unavailable observations are
tracked locally. Unresolved requests and handoffs cannot be hidden by recording
recovery. These reports do not establish real external delivery or comprehension.

The doctor console includes persisted simulated reviewer availability and
compatible reassignment. Returned tasks and shared questions start a new audited
handoff, invalidating older acknowledgements and reply drafts. Review timings are
reported per handoff, separately from simulated clock advances. Cache preparation
uses the app's encrypted exact-input contract; it preserves existing valid results
and accepts additional explicitly synthetic fixtures with `--manifest <path>`.
Doctor-selected products and regimens retain formulation, strength, route,
daily count, duration and indication. Per-dose/daily/frequency/course checks
require explicit reviewed entries; the current empty/pending table leaves them
unavailable. Edits clear prescription acknowledgement. See
[prescription checking limits](docs/dose_entry_contract.md).

Workers can confirm illness country, region, district and date for the next
packet. Reviewed place/month/date-window matching is implemented, with saved
drafts, unknown reasons, read-back/export and stale-reference guards. The regional
catalogue remains empty/pending; no actual local disease prior is supplied.
See [fever context contract](docs/fever_context_contract.md).

The original Day-4 architecture below describes the retained legacy modules.

## Original Day-4 implementation

SmartSehat is a clinical AI assistant for BHU-level health workers in rural
Pakistan. It uses a LangGraph-based agent pipeline with deterministic-first
triage and human-in-the-loop (HITL) gates to support safe, auditable
clinical workflows.

## Three Views

### Consultation

Voice intake with Urdu ASR feeds deterministic triage in `rules_triage.py`,
followed by AI-assisted clinical reasoning with citations and X-ray pattern
detection. A health worker must approve, modify, or reject recommendations
through a HITL gate. RED cases produce emergency dispatch packets, while an
asynchronous doctor review loop supports telemedicine follow-up through
WhatsApp.

### Dawa Check

A prescription or medicine-bag photo is processed through OCR extraction
and checked for pregnancy risks, duplicate drug classes, drug-drug
interactions (DDIs), and antibiotic stewardship concerns. Results appear
in a traffic-light safety grid for health worker review, including the
ability to dismiss individual flags with an auditable rationale.

### District View

The District Health Officer receives a read-only dashboard backed by the
immutable event ledger. It provides KPIs, cases per BHU, top diagnoses,
HITL oversight with rubber-stamp and high-override flags, syndromic trend
charts with automated outbreak alerts, and a facility map.

## Quick Start

```bash
pip install -r requirements.txt
python seed.py
python seed_district.py
python -m streamlit run smartsehat/app.py
```

## Tests

```bash
python test_flow.py     # 34/34 — consultation pipeline
python test_dawa.py     # 24/24 — dawa check pipeline
```

## Agent Modes

Set `agents` in `config.yaml`:

- `agents: stub` — deterministic operation with no API keys required.
- `agents: real` — live model providers; add `GROQ_API_KEY`,
  `GOOGLE_API_KEY`, and `DASHSCOPE_API_KEY` to `.env` (gitignored —
  keys never enter the repository).

## Model Routes (bake-off winners)

Selected by a 60-call live bake-off across Google, Alibaba, and Groq:

- STT: `gemini-3.6-flash` primary → `qwen3-asr-flash` fallback
- Vision (X-ray + prescriptions): `qwen3-vl-plus` primary → `gemini-3.6-flash` fallback
- Reasoning: `gpt-oss-120b` (Groq) primary → `qwen3-max` fallback

All inference flows through a provider-agnostic router with automatic
failover and 503-transient retry — demonstrated live when a provider
outage occurred mid-build and the consultation completed uninterrupted.

## Clinical Corpus

`corpus/chunks.json` holds 27 verbatim, provenance-tracked passages:
10 WHO + 17 Pakistani government sources (National TB Control Programme
guidelines 2019, the NEML Gazette of Pakistan S.R.O. 1423(I)/2023, and
the NIH XDR-typhoid advisory 2024). Every AI citation is mechanically
validated against this corpus; citations that don't resolve are stripped.
See `corpus/PROVENANCE.md` for the document-level audit trail.

## Layout

```text
smartsehat/
  app.py          — Streamlit UI (3 tabs)
  graph.py        — consultation LangGraph
  dawa_graph.py   — dawa check LangGraph
  dash.py         — district dashboard (read-only)
  agents.py       — stub/real agent implementations
  audit.py        — append-only SQLite ledger
  router.py       — model router + provider health
  rules_triage.py — deterministic triage rules
  corpus.py       — RAG corpus loader
  state.py        — PatientState TypedDict
data/brand_generic_map.json — Pakistani brand→generic map
corpus/chunks.json           — 27-chunk clinical corpus
seed.py           — baseline seed (1 facility, 1 user, 1 patient)
seed_district.py  — synthetic demo month (150 cases, 4 BHUs, labeled)
test_flow.py      — consultation tests (34)
test_dawa.py      — dawa check tests (24)
test_real.py      — live API integration test (skips without keys)
```

## What's Verified

- 34 automated checks on the consultation pipeline: triage (English/Urdu/
  Devanagari scripts), reasoning citations, HITL gates, emergency dispatch,
  and the doctor review loop.
- 24 automated checks on the dawa pipeline: drug flags, dismiss flow,
  finalization, and ledger audit.
- 7 live API integration checks (STT, vision, cited reasoning,
  prescription OCR at 6/6).
- The district dashboard renders from the same immutable ledger; every
  number is a query over append-only events.
