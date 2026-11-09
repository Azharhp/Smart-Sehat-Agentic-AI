# Consultation steps 1–7: implementation update

Updated October 22, 2026. The changes below extend the existing Streamlit consultation. They are application behavior, not evidence of clinical approval or measured patient outcomes.

Later additions, including quick danger capture, opt-in regional speech, transfer recovery, inactive delivery integration and complete guidance audio, are recorded in [consultation_integrations.md](consultation_integrations.md).

The latest step-1 status is in [danger_screen_completion.md](danger_screen_completion.md): all danger prompt recordings are prepared, sequential phone playback is connected, and actual clinician/observer evidence remains pending.

| Step | Implemented in this batch | Remaining input or validation |
|---|---|---|
| 1. Danger screen | Large immediate Yes/No/Unknown actions, code-authored observation diagrams, answer progress, matching-language browser speech and local fixed-prompt playback. Confirming a danger sign immediately activates the existing emergency lane. | Physician/native-speaker review of wording, diagrams and audio; actual under-30-second target-device usability. External alerts/transport remain simulated. |
| 2. Voice story | Seven choices with retained record/upload audio, exact worker review, named regional review, transcript/answer-bound fact proposals, stale-result guards and correction rechecks. Urdu/English bounded STT, opt-in Sindhi/Punjabi/Pashto Whisper and Saraiki/Balochi multimodal attempts; signal inspection and encrypted reported speech evaluations. See `voice_story_completion.md`. | Experimental regional routes are unvalidated; reliable Saraiki/Balochi ASR and independently reviewed language/dialect/device results remain pending. No real speech dataset was supplied. |
| 3. Adaptive questions | Separate known, unknown and not-asked counts, coverage table, missing reasons, collection explanations and local audio for the existing 48-question bilingual bank. Negative answers remain distinct from unknown answers. | Clinician/native-speaker review of the bank and wording. Collection progress does not establish that the doctor has enough clinical information. |
| 4. Measurements | Schematic measurement/observation diagrams and unit guidance alongside the existing full-minute breath counter, validated vitals, typed tests, photo proposals and explicit unavailable reasons. | Diagrams are orientation aids, not calibrated diagnostic examples. Physical-device/worker measurement and low-end-phone usability require observation. |
| 5. Medicines/allergies | Each confirmed medicine can retain a separately confirmed mg-per-dose amount, maximum whole doses per 24 hours, duration, route, indication and unknown reason. Amounts bind the exact product record, survive drafts and invalidate when that record changes. Existing reconciliation and dose-limit software check these reported amounts separately. | No reviewed clinical dose rows are supplied, so real dose checks remain unavailable. Product/interaction content and clinical ranges need physician/pharmacist review. No dose or volume/tablet conversion is invented. |
| 6. Considerations | A second verifier checks eligible catalogue IDs, exact configured wording, current fact citations, confirmed negatives, expected gaps, references and forbidden diagnosis/dose/probability fields. Older packets require regeneration under the new verification contract. A version-bound review archive/report workflow covers the relevant content. | This checks constrained structure, not arbitrary clinical truth or unrestricted prose entailment. Clinical interpretation, reviewed regional context and independent physician review remain pending. |
| 7. Read-back | Full English/Urdu read-back plus named interpreter-confirmed regional-language text; packet/language/text-bound audio recording or upload, optional generated audio preview and an offline HTML download with explicitly confirmed audio. Changed wording clears prior patient confirmation and blocks stale sending. | A human must check translated meaning, negation and units. Number/name guards cannot prove equivalence. Regional-language automatic TTS and comprehension validation remain pending. |

## Prepared audio and content review

The local bundle contains all **66 unique fixed English prompts** and **38 of 65 unique Urdu prompts**. All 17 unique Urdu danger prompts are prepared, using existing Gemini clips plus matching-language offline eSpeak fallback. Twenty-seven wider question-catalogue Urdu phrases remain unprepared. Prepared prompts play without a provider call while the Streamlit server is available. Human recordings can replace exact wording/language prompts. Generated pronunciation remains unreviewed.

[Clinical review package](clinical-review-package.zip) contains the exact content snapshots and hashes, available fixed audio and danger diagrams. It contains no patient encounters. The app can export the current package and record an actual physician, pharmacist or native-speaker review against the exact content hash. Reports are append-only, encrypted and labelled reported reviews with unverified reviewer identity; they do not activate clinical tables automatically. No real review has been recorded by this implementation.

The complete Streamlit application still requires its server. Downloaded read-back HTML with confirmed embedded audio can be opened offline; this is separate from automatic offline synchronization.

To prepare missing fixed Urdu prompts when provider capacity is available:

```powershell
.venv/Scripts/python.exe -X utf8 -m scripts.build_consultation_audio --language Urdu --provider gemini --live --limit 65
```

Existing valid exact-text recordings/caches are reused. Generation stops when the provider is unavailable. The helper never calls a provider without `--live`. The Windows provider requires an installed voice matching the requested language; it does not substitute English for Urdu. Its optional `--allow-local-script` switch permits only the helper's child PowerShell process to run the local speech script; it does not change machine execution policy.

## Verification

The existing 100-check regression batch passed during implementation. Eleven new workflow checks cover the new speech cache, exact recording binding, interpreter read-back/send guards, per-medicine amount corrections, encrypted immutable review reports, coverage counts, consideration verification and worker UI behavior. The latest grouped workflow/UI result is recorded in [verification.md](verification.md). Browser component checks cover matching-language voices, no wrong-language fallback, read-only mode, draft preservation, case reset and microphone/speech stop. All 76 authored urgency vignettes retained exact browser/Python parity.

These checks use authored software fixtures. They do not establish speech accuracy, clinical correctness or patient safety. Broader deployment and later consultation steps remain listed in [blueprint_comparison.md](blueprint_comparison.md).
