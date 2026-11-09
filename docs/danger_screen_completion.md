# Danger screening and Emergency Lane

Updated October 22, 2026. The software and prepared danger-prompt audio are implemented. Clinical approval, pronunciation review and an independently observed under-30-second field result are not established.

| Requirement | Current result |
|---|---|
| Child/adult/maternal danger questions | Implemented with current age/pregnancy and stale-answer guards |
| Immediate interruption | First Yes is sent immediately and backend rules activate RED; the worker does not finish the full screen before emergency referral |
| Phone interaction | One-question-at-a-time view, automatic advance after No/Unknown, previous-question control and optional all-question view |
| Pictures and large answers | Schematic pictures, large Yes/No/Unknown buttons and explicit unknown handling |
| Urdu danger-prompt audio | **17/17 unique prompts prepared**; all 19 configured occurrences are covered |
| English danger-prompt audio | **18/18 unique prompts prepared**; repeated wording is reused |
| Local playback | Exact-text recordings embedded in the quick component; no browser Urdu voice or live speech service is required for playback after loading |
| Emergency instructions | Existing English wording centralized with provisional Urdu wording; both included in the exact-content review package |
| Referral/pre-alert | Existing one-tap local referral and explicit gateway outbox remain available; actual delivery is inactive without credentials |
| Clinician/native-speaker review | **Pending**: zero matching actual stored review reports |
| Independent under-30-second usability | **Pending**: zero actual separately observed reports; automated/browser timings are not field evidence |

## Audio preparation

The configured Gemini service generated one missing danger prompt, then returned HTTP 429. The remaining missing danger prompts were generated locally using the matching Urdu voice in eSpeak NG. Existing recordings/caches were preserved. The audio manifest records exact wording, content hashes and voice origin; generation does not imply pronunciation quality or clinical approval.

All 35 unique English/Urdu danger recordings were readable mono WAVs. The longest prepared prompt is approximately 2.91 seconds. This measures file duration, not the time a worker needs to understand or complete the screen. The combined raw audio is 2,628,792 bytes.

The optional generation helper uses a pinned `@echogarden/espeak-ng-emscripten` 0.3.5 dependency under `scripts/offline-speech/`, installed with lifecycle scripts disabled. It runs offline; no machine-wide voice or execution policy was changed. Existing playback does not require Node or a provider connection. Dependency source/license: [Echogarden eSpeak port](https://github.com/echogarden-project/espeak-ng-emscripten), [eSpeak NG languages](https://github.com/espeak-ng/espeak-ng/blob/master/docs/languages.md). The package's GPL-3.0 COPYING is retained with its installed dependency.

Rebuild missing exact danger recordings locally:

```powershell
npm.cmd --prefix scripts/offline-speech ci --ignore-scripts --no-audit --no-fund --cache data/npm-cache
.venv/Scripts/python.exe -X utf8 -m scripts.build_consultation_audio --language Urdu --provider espeak --scope danger --limit 30
```

To improve pronunciation, a native speaker can listen to each exact prompt and replace it with a checked human recording through the existing prompt upload. Supplied recordings remain separate from independent clinical approval.

## Actual review and observation

[Focused review ZIP](danger-screen-review.zip) includes the exact child/adult/maternal wording, collection guidance, schematic diagrams, bilingual emergency instructions, all available danger audio and the review-report JSON schema. A physician must review the clinical material; a native speaker must listen to the recordings. The Evidence panel accepts actual version-bound reports for `danger_screen`, `emergency_response` and `danger_audio`. Reports remain labelled reported-only with unverified identity and never manufacture validation.

The new observer panel links a reported stopwatch result to a real recorded screen attempt and exact content hash. It records the observer identifier, participant code, device/browser, first/repeat attempt, measured seconds, expected result, wording comprehension and notes. Zero is not a measured default; the observer must enter a value and explicitly confirm an actual separate-person observation. Unknown answers and previously documented answers remain visible so a quick recheck cannot be confused with a complete fresh assessment. Changed content, wrong-case captures, invalid times and missing confirmation are rejected. These are auditable reports, not automatically verified evidence.

[Readiness JSON](danger-screen-status.json) currently records complete prepared audio, **zero review reports** and **zero observer reports**. Regenerate it and the focused package without clinical mutations:

```powershell
.venv/Scripts/python.exe -X utf8 -m scripts.consultation_danger_status --write-package
```

## Verification

Thirty focused consultation checks passed in 13.6 seconds, covering danger interruption, scope guards, observations/review reports, prompt binding, immutable records, transfer and inactive-delivery behavior. Browser component checks passed for sequential/all views, immediate Yes interruption, No/Unknown completion, prepared Urdu playback without an Urdu browser voice, stopping audio and clearing an older emergency message on a new case. These use invented data/mocked interaction and do not establish human usability or clinical correctness.

The entire fixed-prompt bundle now contains 66 English and 38/65 Urdu prompts. The 27 remaining Urdu phrases belong to the wider question catalogue; **no configured danger-screen Urdu prompt is missing**.
