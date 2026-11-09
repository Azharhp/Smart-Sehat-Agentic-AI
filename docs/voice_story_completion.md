# Voice story to confirmed facts

Updated October 22, 2026. This completes the remaining software work for Step 2 that can be done without native-speaker reference recordings. It does **not** establish accurate regional-language or clinical transcription.

## Implemented

- Seven language choices preserve separate editable drafts, reviewer names and retained encrypted recordings. Workers can record in the app or upload WAV, MP3, M4A, OGG, WebM or FLAC. Original recordings remain available for listening after restart, including when transcription fails.
- Urdu/English retain the bounded provider chain. Sindhi/Punjabi/Pashto use opt-in explicit-language Whisper drafts. Saraiki/Balochi now have opt-in Gemini multimodal attempts using explicit Arabic-script language prompts. They never pass unsupported language codes to Whisper or fall through to an Urdu prompt. These are experimental attempts, not claims of officially supported or validated ASR. Missing credentials, empty/unintelligible output, failures and timeouts preserve manual entry.
- Every transcript requires an exact-text worker review before extraction. Regional review requires a named bilingual worker/interpreter. The UI calls out negatives, duration, quantities/units, medicine names and unclear spans. Names and review assertions remain unverified reports.
- Extraction proposals retain the exact transcript fact ID, SHA-256, spoken language, extraction mode, verbatim quote and previous answer version. Confirming a proposal preserves that evidence. Edits in another session, a changed draft, changed answers and late extraction results block old confirmations. Re-extraction replaces old pending proposals. Correcting a reviewed transcript reopens previously confirmed facts derived from it as unknown with an explicit recheck reason; original facts remain in the audit.
- Existing confidence highlighting and individual Confirm/Dismiss remain. Extra conservative guards reject changed digit durations, recognized contradictory negation, uncertain/unclear quotes, out-of-transcript evidence, unsupported types/keys and conflicting values. These literal checks do not understand arbitrary regional-language semantics.
- Recording inspection reports the original SHA-256, byte count and, for supported PCM WAV, duration/sample rate/channels, signal level and clipping warnings. Silent WAVs and WAVs longer than ten minutes are not sent for automatic transcription; files over 20 MB are rejected before upload/transcription. Signal checks do not measure speech quality or intelligibility. Other encodings remain playable/manual and report unavailable measurements.
- A speech evaluation panel downloads the exact schema and an empty dataset, previews computed results and explicitly records reported evaluations. References/results are encrypted, append-only, idempotent by dataset hash and recover after restart. No evaluation changes language activation or claims clinical validation.

## Measuring the remaining language/device work

Provide independently reviewed, consented, pseudonymised recordings for each relevant language/dialect, actual target phone, noise and network condition. Include symptoms/negatives, symptom duration, numbers/units, medicine names and code-switching. Keep the reference transcript independent of the model hypothesis. Record actual end-to-end latency and the provider/model version. Do not reuse the same recording as multiple independent observations.

The evaluator computes exact word/character edit rates, omission/spurious/wrong fact counts, negative flips, number/unit errors, medicine-label errors and p50/p95 reported latency. It groups results by source, language, dialect, provider/model, device, noise and network; synthetic and speaker recordings remain separate. Word/character normalization uses NFC/casefold, ASCII decimal digits and punctuation/space normalization, preserving letter distinctions. CER excludes spaces; there is no transliteration or automatic spelling correction. Fully annotated empty references can measure spurious facts; unannotated samples are not counted as fact evaluations.

Use the app's **Speech accuracy and device evaluation** panel or:

```powershell
.venv/Scripts/python.exe -X utf8 -m scripts.consultation_speech_evaluate --template docs/speech-evaluation-template.json
.venv/Scripts/python.exe -X utf8 -m scripts.consultation_speech_evaluate reviewed-dataset.json --output results.json
```

The command makes no provider calls. Add `--record` only to save the reported dataset to the installation's encrypted database. The empty template is deliberately rejected as an evaluation. Dataset files and exported results must be handled as review data; do not commit actual speaker references to the repository.

## Remaining external evidence

No actual speaker dataset, bilingual/native-speaker review, target-phone/noise trial or regional provider quality result was supplied. Independent validation remains pending for every language. Experimental routes may produce inaccurate or wrong-script drafts and must be reviewed against the recording. A reliable Saraiki/Balochi provider/model can replace the experimental route after partner selection and measured evaluation. Software completion and measured language accuracy are separate claims.

Provider references: [Groq speech transcription](https://console.groq.com/docs/speech-to-text), [Whisper language tokens](https://github.com/openai/whisper/blob/main/whisper/tokenizer.py), [Gemini audio processing](https://ai.google.dev/gemini-api/docs/audio). A language token or a general audio prompt does not establish clinical/dialect accuracy.

Verification: 154 grouped consultation tests passed in 61.2 seconds; after the final exact-recording/review-event guards, 19 focused speech/workflow/UI checks passed in 3.5 seconds. Provider outputs and speech references in tests are synthetic/mocked. Actual stored speaker evaluations remain zero for every language. The app was restarted and its health endpoint returned `ok`.
