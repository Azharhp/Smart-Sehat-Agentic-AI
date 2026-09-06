# SmartSehat — Consultation, Dawa Check, and District Dashboard

SmartSehat is a clinical AI assistant for BHU-level health workers in rural
Pakistan. It uses a LangGraph-based agent pipeline with deterministic-first
triage and human-in-the-loop (HITL) gates to support safe, auditable
clinical workflows.

## Three Views

### Consultation

Voice intake with Urdu ASR feeds deterministic triage in `rules_triage.py`,
followed by AI-assisted clinical reasoning with citations and X-ray pattern
detection. Every structured medicine proposal is evaluated by the same bounded,
source-linked rules used by Dawa Check; edits invalidate the prior receipt and
run the review again. A health worker must approve, modify, or reject the plan.
RED cases produce referral packets without invented drug orders, while an
asynchronous doctor review loop supports telemedicine follow-up through
WhatsApp.

### Dawa Check

A prescription or medicine-bag photo is processed through confirmed extraction
and a finite shared review of product identity, selected interactions,
ingredient duplication, adult oral paracetamol ceilings, selected pregnancy
NSAID timing, allergy reconciliation, formulary scope, and antibiotic
stewardship. Findings cannot be dismissed or turned green; review/referral notes
preserve the original result and its evidence link.

Both clinical workflows now create an owned encounter before any media is
stored. The encounter freezes the registered patient or explicit walk-in
reference, authenticated actor, facility, workflow, status, and versioned
patient-context snapshot. Uploaded WAV/JPEG/PNG content is validated from its
bytes, stored under an immutable media ID in a private case directory, and
authorized through the encounter rather than exposed as a caller-supplied path.

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

For a guaranteed local demonstration with external providers disabled:

```bash
set SMARTSEHAT_OFFLINE=1
set SMARTSEHAT_AUTH_MODE=demo
set DOTENV_DISABLED=1
python -m streamlit run smartsehat/app.py
```

The demo account selector is explicitly a local development identity provider.
It is not production authentication. Set `SMARTSEHAT_AUTH_MODE=production` to
fail closed until a production identity provider is implemented. Worker,
doctor/clinical-reviewer, and DHO permissions are checked in the service layer,
including facility ownership for patients, encounters, media, resume, mutation,
and export access.

Runtime media defaults to `runtime/private-media` (gitignored). Override it with
`SMARTSEHAT_MEDIA_ROOT`; the default size limit is 10 MiB and can be changed with
`SMARTSEHAT_MAX_UPLOAD_BYTES`.

## Tests

```bash
python -m unittest discover -v  # 111 discovered: 110 pass, 1 live probe skipped
python test_flow.py             # 35 consultation workflow checks
python test_dawa.py             # 45 medication/Dawa checks
python demo_medication_review.py
```

## Agent Modes

Set `agents` in `config.yaml`:

- `agents: stub` — deterministic/manual demo operation with provider routing blocked.
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
  medication_review.py — shared finite medication review rules
  medication_ui.py — shared structured medicine/context UI
  emergency.py    — single emergency referral output source
  dash.py         — district dashboard (read-only)
  agents.py       — stub/real agent implementations
  audit.py        — append-only SQLite ledger
  migrations.py   — additive, versioned SQLite migration runner
  domain.py       — typed actor, patient snapshot, and encounter contracts
  auth.py         — authentication provider abstraction and demo provider
  access.py       — service-layer authorization and encounter ownership
  media.py        — validated private immutable media repository
  ui_state.py     — encounter-keyed UI state isolation
  router.py       — model router + provider health
  rules_triage.py — deterministic triage rules
  corpus.py       — RAG corpus loader
  state.py        — PatientState TypedDict
data/brand_generic_map.json — Pakistani brand→generic map
corpus/chunks.json           — 27-chunk clinical corpus
seed.py           — baseline seed (1 facility, 1 user, 1 patient)
seed_district.py  — synthetic demo month (150 cases, 4 BHUs, labeled)
tests/            — discoverable offline regression suite
test_flow.py      — consultation workflow checks (35)
test_dawa.py      — medication and Dawa checks (45)
test_real.py      — paid live integration probe (requires SMARTSEHAT_RUN_LIVE=1)
```

## What is verified

- 35 scripted checks on the consultation pipeline: triage (English/Urdu/
  Devanagari scripts), reasoning citations, HITL gates, emergency dispatch,
  and the doctor review loop.
- 45 medication/Dawa tests plus the discoverable offline suite cover bounded
  rules, non-dismissable findings, stale receipt rejection, referral and ledger evidence.
- Live API probes are opt-in and were not run as part of offline verification.
- The district dashboard renders from the same immutable ledger; every
  number is a query over append-only events.

The medication rules are a limited, source-backed prototype. Independent
clinical/pharmacy validation and local formulary sign-off remain pending; see
`docs/MEDICATION_EVIDENCE.md` for supported checks and explicit exclusions.
