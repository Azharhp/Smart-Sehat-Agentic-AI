# SmartSehat — Clear information. Better care.

Clinical workflow support for frontline health workers and district health officers.

[Design brief](Design.md) · [Medication evidence](docs/MEDICATION_EVIDENCE.md)

SmartSehat is AI-assisted frontline rural clinical workflow support for
BHU-level health workers in rural Pakistan. It uses a LangGraph-based agent
pipeline with deterministic-first triage and human-in-the-loop (HITL) gates to
support safe, auditable clinical workflows. It is NOT an autonomous doctor,
NOT an autonomous prescribing system, and NOT clinically validated for
unsupervised deployment.

SmartSehat turns an unstructured frontline patient conversation into a
structured, evidence-backed assessment that can be reviewed, referred and
handed off to a remote clinician. We do not replace doctors. We extend the
reach of limited doctors.

The regional prototype includes one structured respiratory/TB history pathway
with deterministic safety escalation and explicit unsupported-scope behavior.
It does not claim to support all respiratory medicine.

## Three Views

### Consultation

Voice intake with Urdu ASR feeds deterministic triage in `rules_triage.py`,
followed by AI-assisted clinical reasoning with citations and X-ray pattern
detection. Every structured medicine proposal is evaluated by the same bounded,
source-linked rules used by Dawa Check; edits invalidate the prior receipt and
run the review again. A health worker must approve, modify, or reject the plan.
RED cases produce referral packets without invented drug orders, while an
report can be shared through a WhatsApp link. Doctor advice is entered manually
by the worker; doctor identity is not verified by this prototype.

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

The District Health Officer receives a read-only dashboard of synthetic/demo
district analytics backed by the append-oriented auditable event history. It
provides KPIs, cases per BHU, top diagnoses, HITL oversight with rubber-stamp
and high-override flags, syndromic trend charts with rule-based outbreak flags
(a regional, not-production demo signal — not clinically-validated automated
outbreak detection), and a facility map.

## Interface and reports

- A welcome screen introduces Consultation, Dawa Check, and District Pulse,
  with role-based demo sign-in from the sidebar.
- Transparent SmartSehat branding, a dark sidebar, teal actions, readable clinical
  cards, and responsive layouts replace raw JSON in the default presentation.
- Encounter summaries and stage indicators keep the active patient and workflow
  visible. Original and reviewed history are shown side by side.
- The toolbar is separated from the scrolling workspace; sidebar controls have
  explicit contrast and keyboard focus states.
- Medication reviews, doctor handoffs, and emergency referrals offer a readable
  **HTML report download**. Open the file and select **Print / Save as PDF** to
  create a PDF through the browser. This preserves Unicode/Urdu text; it is not
  a direct server-generated PDF download.
- Medication evidence can also be downloaded as JSON. Clinical warnings,
  unassessed checks, and human-review requirements remain part of the report.

The [design brief](Design.md) covers the screen inventory and visual direction.
Mockups inform the styling; they do not add unsupported clinical features.

## Quick Start

```bash
pip install -r requirements.txt
python seed.py
python seed_district.py
python -m streamlit run smartsehat/app.py
```

For a guaranteed local demonstration with external providers disabled:

PowerShell:

```powershell
$env:SMARTSEHAT_OFFLINE = "1"
$env:SMARTSEHAT_AUTH_MODE = "demo"
$env:DOTENV_DISABLED = "1"
python -m streamlit run smartsehat/app.py
```

Bash:

```bash
SMARTSEHAT_OFFLINE=1 SMARTSEHAT_AUTH_MODE=demo DOTENV_DISABLED=1 python -m streamlit run smartsehat/app.py
```

Select **Farid Khan · paramedic** for patient workflows or **District Officer
(demo) · dho** for the district dashboard, then click **Start demo session**.
`seed.py` creates both demo identities; `seed_district.py` populates the synthetic
analytics. The committed configuration defaults to `agents: stub`.

The demo account selector is explicitly a `demo-local` identity provider.
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
python -m unittest discover -v  # Offline regression suite; live probes are opt-in
python test_flow.py             # 35 consultation workflow checks
python test_dawa.py             # 45 medication/Dawa checks
python demo_medication_review.py
```

## Agent Modes

Set `agents` in `config.yaml`, or override it with `SMARTSEHAT_MODE`:

- `stub` — deterministic/manual demo operation with external provider routing blocked.
- `real` — live providers using keys from your local `.env` or environment:
  `GOOGLE_API_KEY`, `AIHUBMIX_API_KEY`, `TOKENHARBOR_API_KEY`,
  `DASHSCOPE_API_KEY`, and `GROQ_API_KEY`. Keys must remain outside Git.

`SMARTSEHAT_OFFLINE=1` always forces stub mode. To enable real mode, remove that
flag or set it to `0`, set `SMARTSEHAT_MODE=real`, and provide the needed keys.
If relying on `.env`, remove `DOTENV_DISABLED=1` as well. Restart the app after
changing mode or routes because configuration and routers are cached.
Demo authentication remains a development-only identity mechanism in either mode.

## Model priorities

The shared router uses the following order from `config.yaml` for voice and
agent workflows. Availability depends on each provider and configured credentials;
these are configured model IDs, not a claim that every tier is currently available.

| Role | Priority order (first → last) |
|---|---|
| Voice / STT | `gemini/gemini-3.6-flash` → `aihubmix/gemini-3.8-flash-free` → `tokenharbor/mimo-v2.5:free` → `qwen/qwen3-asr-flash` → `groq/whisper-large-v3` |
| Prescription vision | `qwen/qwen3-vl-plus` → `gemini/gemini-3.6-flash` → `aihubmix/gemini-3.8-flash-free` |
| X-ray vision | `qwen/qwen3-vl-plus` → `gemini/gemini-3.6-flash` → `aihubmix/gemini-3.8-flash-free` |
| Reasoning | `groq/gpt-oss-120b` → `qwen/qwen3-max` → `gemini/gemini-3.6-flash` → `aihubmix/gemini-3.8-flash-free` |

Routing supports fallback and a short retry for transient overload errors.
If every provider fails, the workflow reports degraded availability rather than
presenting an error as clinical model output.

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
  app.py          — Streamlit UI and role-based workflow navigation
  presentation.py — welcome, theme, encounter header, readable HTML reports
  intake_ui.py    — complaint, history review, and structured intake
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
seed.py           — baseline seed (facility, worker + DHO accounts, patient)
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
- The district dashboard renders from the same append-oriented auditable event
  history; every number is a query over append-only events.

The medication rules are a limited, source-backed prototype. Independent
clinical/pharmacy validation and local formulary sign-off remain pending; see
`docs/MEDICATION_EVIDENCE.md` for supported checks and explicit exclusions.
