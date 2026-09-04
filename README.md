# SmartSehat — Day 4: District Dashboard

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