# SmartSehat — Day 2: Real Intelligence

A LangGraph-orchestrated clinical AI assistant for rural health units (BHUs)
in Pakistan. **Day 2 makes the agents real** on top of the Day-1 skeleton:
the ModelRouter now actually calls Groq / Gemini / Qwen, the corpus is
seeded with verbatim WHO / EML / IMCI guideline text, and every agent honours
a `agents:` config switch so the offline test suite stays deterministic and
green.

## What's new in Day 2

- **Real STT, structured extraction, vision, and RAG-grounded reasoning** —
  gated behind `agents: real` in `config.yaml`; `agents: stub` (default)
  preserves Day-1 offline behaviour.
- **Voice pipeline split** — `stt_node` → **transcript_gate** (HITL
  paramedic review, editable) → `structure_node` (JSON extraction).
- **Deterministic triage rules** (`smartsehat/rules_triage.py`) — RED / YELLOW
  / GREEN by keyword matching on English + roman-Urdu variants. Never an
  LLM (safety law). Preserves the `force_triage` override hook.
- **Vision route** — `ModelRouter.route_vision(role, image_path, prompt)`
  with Qwen (VL) primary and Gemini (multimodal) fallback; Groq raises.
- **Clinical corpus** (`corpus/chunks.json`, `corpus/PROVENANCE.md`) —
  verbatim WHO TB screening + treatment, WHO EML entries for amoxicillin,
  TB medicines, artemether-lumefantrine, WHO malaria ACT recommendation,
  IMCI amoxicillin dosing. Pakistan-specific NEML / NTP chunks are
  clearly marked `NOT_RETRIEVED` because those government sites were not
  reachable from this sandbox.
- **Citation gate (warn mode)** — the reasoning agent's DDX citations are
  validated against `corpus.get_chunk_map()`; off-allowlist medicines are
  flagged; the model is re-prompted once; on failure invalid citations are
  stripped and confidence is reduced by 0.1, all recorded in the event
  ledger.
- **Explicit degraded mode** — when a router call raises (all providers
  fail), the reasoning agent logs `REASONING_DEGRADED` and returns
  `{"ddx":[], "confidence":0, "degraded":"reasoning_unavailable"}`;
  the UI surfaces the warning.
- **Streamlit UI** — audio + image uploaders, a dedicated transcript
  confirmation screen, HITL card with triage badge / DDX / citations /
  degraded-mode warning.
- **Tests** — `test_flow.py` (offline, stub) grows to **29 assertions**
  covering the new voice pipeline and every Day-1 safety invariant;
  `test_real.py` runs live against real APIs when keys + `agents: real`
  are set, and **SKIPS cleanly** otherwise.

## Run

```bash
python -m venv .venv
source .venv/bin/activate         # Windows: .venv\Scripts\activate
pip install -r requirements.txt

python seed.py                    # seed BHU-0442 + PM-2213 + PT-0001
python test_flow.py               # 29/29 PASS, offline

streamlit run smartsehat/app.py   # UI with audio/image uploads + HITL
```

## Switching to real mode

1. Put keys in `.env` at repo root:
   ```
   GROQ_API_KEY=...
   GOOGLE_API_KEY=...
   DASHSCOPE_API_KEY=...
   ```
   `router.py` calls `load_dotenv()` at import time — no manual export needed.
2. Flip `config.yaml`:
   ```yaml
   agents: real
   ```
3. `python test_real.py` — TB-audio + X-ray scenario + prescription-image
   vision call. Skips with a clear message when either keys or config are
   not real-mode ready.

## Corpus honesty

`corpus/PROVENANCE.md` is the audit trail. Every chunk records document
title, section / page, retrieval URL, and retrieval method. Chunks that
could not be retrieved verbatim carry `"text": "NOT_RETRIEVED"` and a
sentence naming the fallback in use — nothing was invented or paraphrased.

## Layout

```
smartsehat_project/
├── README.md
├── requirements.txt
├── config.yaml                    # agents: stub|real switch + role_routes
├── seed.py
├── test_flow.py                   # offline stub test — 29/29 PASS
├── test_real.py                   # real-mode integration test (skips w/o keys)
├── corpus/
│   ├── chunks.json                # verbatim clinical passages, stable ids
│   └── PROVENANCE.md              # source + retrieval audit trail
├── assets/                        # demo audio / xray / rx images
└── smartsehat/
    ├── __init__.py
    ├── router.py                  # ModelRouter + generate/transcribe/vision
    ├── audit.py                   # append-only SQLite ledger
    ├── state.py                   # PatientState TypedDict
    ├── corpus.py                  # load_chunks / retrieve (keyword-overlap)
    ├── rules_triage.py            # deterministic triage rules
    ├── agents.py                  # stt / transcript_gate / structure /
    │                              #  triage / vision / reasoning / pharm /
    │                              #  emergency / finalize
    ├── graph.py                   # LangGraph topology + HITL interrupts
    └── app.py                     # Streamlit UI
```
