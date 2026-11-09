# Consultation integrations and transfer update

Updated October 22, 2026. The user confirmed that clinical reviewers and delivery credentials are not yet available, and requested preparation of the integrations. No real message was sent and no clinical approval or clinical dose/context entry was invented.

Later danger-screen work is recorded in `danger_screen_completion.md`: all danger recordings are now prepared (17 Urdu/18 English unique), with sequential phone playback and version-bound observer reports. The wider Urdu bundle is now 38/65; audio counts below describe this earlier integration batch. Actual clinical review and independent usability remain pending.

Later Step 2 work is recorded in `voice_story_completion.md`: explicit experimental Saraiki/Balochi attempts, source-bound fact confirmation/correction guards, original record/upload playback, signal inspection and encrypted reported speech evaluations are now implemented. References to manual-only Saraiki/Balochi below describe the earlier batch. Actual language accuracy remains unvalidated.

## Implemented in this batch

| Blueprint area | Added behavior | Remaining boundary |
|---|---|---|
| 1. Danger screen | A compact browser component collects answers without a Streamlit round trip for each negative. The first Yes sends immediately; the backend validates the current age/pregnancy/question scope and recomputes urgency. Existing detailed buttons remain available. Browser elapsed time, existing-answer counts and changed-answer counts are audited. | Browser timing is explicitly device-reported, not an independently observed under-30-second field result. Clinical/device/native-speaker review remains pending. |
| 2. Voice story | Opt-in experimental Sindhi/Punjabi/Pashto transcription through an explicit-language Whisper route. A named bilingual reviewer must check the exact editable transcript before extraction. Drafts/proposals are separate per language; changed text requires reconfirmation. Urdu/English retain their provider chain. | Regional script/dialect accuracy remains unvalidated. Saraiki/Balochi remain recording/manual-interpreter only. No live regional speech trial was performed. |
| 3/7. Prompt/read-back audio | Eleven more fixed Urdu prompts were generated and encrypted; total is now 21/65 Urdu and 66/66 English. The helper now prioritizes danger prompts and supports `--scope danger`, `questions` or `all`. | The next request returned HTTP 429; 44 Urdu prompts remain unprepared. Generated audio still needs native-speaker/clinical review. |
| 8. Portable packet | Lossless ZIP containing full/compact SBAR, complete bilingual read-backs, structured packet and optional original case-scoped media. A manifest records byte counts and SHA-256 hashes, including unavailable media. Absolute file references are removed. | Exports are readable. Checksums detect byte changes; they do not authenticate a sender or establish clinical correctness. Original media is not clinically recompressed. |
| 8. Interrupted transfer | Split the ZIP into 64 KiB JSON parts, receive them in any order, report missing parts, retain received parts in the current session, and reconstruct only after part/full-package checks succeed. Receiving never confirms/imports clinical facts. | This is manual resumable transfer, not automatic offline synchronization. A browser-session reset can require reselecting the saved parts. Real poor-network/device throughput remains unmeasured. |
| 9/10. Delivery integration | An encrypted audited outbox prepares current case packets, RED pre-alerts and current doctor guidance for a partner-operated care-team/SMS/WhatsApp/call gateway. Dispatch requires explicit recipient/payload confirmation and server credentials. Consent, handoff, evidence and source-version changes block stale requests. | Gateway credentials/provider routing remain absent, so dispatch is inactive. HTTP acceptance is not recipient reading, facility receipt or care. Clinician identity and local routing simulations remain unverified. |
| 10/11. Patient guidance | A complete script preserves all doctor-authored advice, local wording, written prescription/schedule, feeding/ORS, counselling, warnings, tests, referral and provisional/final status. Optional generation creates an encrypted unreviewed recording; the doctor must listen and confirm. The server rejects known generated audio after its written source changes, even if draft metadata is removed. | English/Urdu labels do not translate the doctor's wording. No instructions are inferred from checker-only mg/regimen fields. Long scripts require human recording rather than truncation. System-assigned follow-up dates remain separately visible on the saved card. |
| 5/6. Clinical inputs | Downloadable exact dose/context JSON schemas and empty partner templates are available in the Evidence panel, alongside the hash-bound content-review archive. | Actual reviewed dose/context entries and reviewer sign-off remain absent. These downloads do not activate clinical content. |

## Partner delivery gateway contract

Server configuration lives in the existing environment; `.env.consultation.example` lists the optional fields:

```dotenv
CONSULTATION_DELIVERY_URL=https://your-partner-gateway.example/consultation
CONSULTATION_DELIVERY_TOKEN=your-server-secret
```

Leave both blank until a real partner endpoint is supplied. Credentials must not be placed in the URL. The adapter uses HTTPS, disables redirects and environment proxies, and has a 20-second HTTP timeout. It never stores raw provider errors or credentials in its audit.

The adapter posts JSON with these envelope fields:

```json
{
  "format": "smartsehat-gateway-1",
  "case_id": "fictional-case-id",
  "delivery_id": "stable-sha256-idempotency-key",
  "kind": "case_packet",
  "channel": "care_team",
  "recipient": "partner-agreed-recipient-identifier",
  "research_prototype": true,
  "clinical_identity_verified": false,
  "source": {},
  "content": {}
}
```

`kind` is `case_packet`, `emergency_prealert` or `patient_guidance`; `channel` is `care_team`, `sms`, `whatsapp` or `call`. The partner must map the recipient/channel to its authorized service and implement persistent deduplication using the `Idempotency-Key` header. Authentication uses `Authorization: Bearer <server token>`.

For a successfully accepted request, return a 2xx response containing:

```json
{"accepted": true, "receipt_id": "partner-request-receipt"}
```

This becomes **gateway accepted**, never “doctor read” or “patient received.” Timeouts, ambiguous HTTP responses and invalid/missing receipts remain **outcome unknown**. Before retrying, check the gateway; retries preserve the same idempotency key. An abandoned in-flight attempt becomes retryable after two minutes with explicit confirmation. Cancelling an unsent request does not recall a request already accepted or in flight. Consent withdrawal blocks subsequent dispatch; an already-started external request cannot be recalled.

Packets contain text/source lines and media references, not embedded files. Use the portable case ZIP for original attachments. Real-service authentication/recipient verification and production governance remain required beyond this local research prototype.

## Verification

The grouped consultation run passed **134 tests in 46.8 seconds**. After removing a transcript widget state warning and adding three transfer/UI checks, a focused **nine-test run passed in 3.2 seconds**. The current suite contains 137 consultation tests; this is not a claim that all 137 were rerun as one group.

The new checks cover interrupted/out-of-order transfers, corruption/unsafe archives, exact original media bytes, unavailable media, explicit delivery confirmation, inactive gateway, consent withdrawal, stale handoffs/content, cancellation, idempotency, timeout/receipt semantics, immediate danger interruption, per-language unconfirmed drafts and generated patient-audio source changes. All outbound provider/delivery tests use mocks and invented data.

Browser logic checks passed for immediate danger interruption, complete negative/unknown capture, case reset, voice matching, timing payload, speech stop and full-minute breathing capture. No physical-device usability, regional speech accuracy, clinical correctness or patient outcome claim follows from these tests.

The updated `clinical-review-package.zip` is 6,657,459 bytes and contains the exact content snapshots, schematic danger diagrams and 87 available fixed audio prompts. It contains no patient encounters and records no actual reviewer approval.

Official implementation references: [Groq speech transcription](https://console.groq.com/docs/speech-to-text), [Whisper language tokens](https://github.com/openai/whisper/blob/main/whisper/tokenizer.py), [Gemini audio processing](https://ai.google.dev/gemini-api/docs/audio). Listed provider language tokens do not establish field accuracy.
