"""Voice capture options distinguish provider routing from field validation."""
VOICE_LANGUAGES = {
    "Urdu": {"browser": "ur-PK", "stt": "ur", "status": "Prototype transcription; native-speaker/device validation pending"},
    "English": {"browser": "en-PK", "stt": "en", "status": "Prototype transcription; device validation pending"},
    "Sindhi": {"browser": "sd-PK", "stt": None, "status": "Manual/interpreter transcription by default; experimental speech draft available with bilingual review; accuracy unvalidated"},
    "Punjabi": {"browser": "pa-Arab-PK", "stt": None, "status": "Manual/interpreter transcription by default; experimental speech draft available with bilingual review; accuracy unvalidated"},
    "Pashto": {"browser": "ps-PK", "stt": None, "status": "Manual/interpreter transcription by default; experimental speech draft available with bilingual review; accuracy unvalidated"},
    "Saraiki": {"browser": "skr-PK", "stt": None, "status": "Manual/interpreter by default; experimental multimodal draft available; provider language support and accuracy unvalidated"},
    "Balochi": {"browser": "bal-PK", "stt": None, "status": "Manual/interpreter by default; experimental multimodal draft available; provider language support and accuracy unvalidated"},
}

# Provider language tokens are not evidence of clinical/dialect accuracy.
# These routes are opt-in experiments and always require a bilingual reviewer.
EXPERIMENTAL_STT = {"Sindhi": "sd", "Punjabi": "pa", "Pashto": "ps", "Saraiki": "skr", "Balochi": "bal"}
