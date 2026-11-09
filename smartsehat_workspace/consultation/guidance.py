"""Worker collection guidance. Instructions describe observation, never treatment."""
GUIDES = {
    "cannot_drink": ("🥛", "Ask the caregiver whether the child can drink or breastfeed.", "دیکھیں اور پوچھیں کہ بچہ دودھ یا پانی پی سکتا ہے یا نہیں۔"),
    "vomits_everything": ("🤢", "Ask whether every drink or feed is vomited back.", "پوچھیں کہ کیا ہر بار دودھ یا پانی پینے کے بعد الٹی ہو جاتی ہے؟"),
    "convulsions": ("⚡", "Ask about fits during this illness. Record what the patient or caregiver reports.", "اس بیماری کے دوران دوروں کے بارے میں پوچھیں۔"),
    "lethargic": ("😴", "Observe alertness and response to the caregiver. Do not delay the emergency lane.", "ہوش اور جواب دینے کی حالت دیکھیں۔ خطرہ ہو تو فوراً ہنگامی راستہ اختیار کریں۔"),
    "chest_indrawing": ("🫁", "Observe the lower chest during breathing while the patient is calm. Record yes, no or unable to assess.", "مریض کے پرسکون ہونے پر سانس کے ساتھ نچلے سینے کی حرکت دیکھیں۔"),
    "stridor": ("👂", "Listen for harsh breathing noise while calm; record uncertainty rather than guessing.", "سکون میں سانس کی سخت آواز سنیں۔ اندازہ نہ لگائیں، شک ہو تو نامعلوم لکھیں۔"),
    "dehydration": ("💧", "Record drinking, urine and observed appearance separately. A doctor interprets them together.", "پانی پینے، پیشاب اور ظاہری حالت کی معلومات الگ الگ درج کریں۔"),
    "pallor": ("🖐️", "Use good natural light and record the observed finding; avoid colour filters.", "اچھی قدرتی روشنی میں دیکھیں، تصویر پر رنگ بدلنے والا فلٹر نہ لگائیں۔"),
    "jaundice": ("👁️", "Record observed yellow colouring in good light. Send a photo only with consent.", "اچھی روشنی میں پیلاہٹ دیکھیں۔ رضامندی کے بعد تصویر لیں۔"),
    "oedema": ("🦶", "Describe and photograph observed swelling for the doctor, within your training.", "اپنی تربیت کے مطابق سوجن کی جگہ اور حالت ڈاکٹر کو بتائیں۔"),
    "rash": ("🔍", "Describe site, onset and change; photograph a wider view and a close view.", "دانے کہاں ہیں، کب سے ہیں اور کیسے بدلے، یہ بتائیں۔ دور اور قریب کی تصویر لیں۔"),
}
PHOTO_GUIDES = {
    "device": "Keep the whole display in focus, including labels and units. Avoid glare; do not crop away pulse/SpO2 labels.",
    "skin / wound": "Take a wider view for location and a close view for detail. Use good light and avoid identifying features.",
    "throat / tongue": "Use good light. Do not force the mouth open or put an object into the throat. Stop if the patient is distressed.",
    "eyes": "Photograph in indirect light without flash into the eyes. Capture the finding clearly and avoid unrelated identifiers.",
    "medicine": "Include the brand, generic name, formulation, strength and expiry where visible. Take a second image if needed.",
}


def guide(key: str) -> tuple[str, str, str]:
    return GUIDES.get(key, ("⚠️", "Ask directly and observe within your training. If unsure, record unknown; a positive danger sign needs urgent action.", "براہ راست پوچھیں۔ شک ہو تو نامعلوم لکھیں۔ خطرے کی علامت ہو تو فوری اقدام کریں۔"))
