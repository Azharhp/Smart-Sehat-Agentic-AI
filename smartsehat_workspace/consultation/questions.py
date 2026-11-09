"""Curated bilingual question bank; the model cannot invent questions."""
from __future__ import annotations
from .clinical import Age

# key, English, Urdu, trigger, answer type. Order is clinical collection priority.
_BANK = [
    ("cough", "Is there a cough?", "کیا کھانسی ہے؟", "always", "bool"),
    ("breathlessness", "Is breathing difficult?", "کیا سانس لینے میں مشکل ہے؟", "always", "bool"),
    ("fever", "Has there been fever?", "کیا بخار ہے؟", "always", "bool"),
    ("cough_days", "For how many days has there been a cough?", "کھانسی کتنے دن سے ہے؟", "cough", "number"),
    ("fever_days", "For how many days has there been fever?", "بخار کتنے دن سے ہے؟", "fever", "number"),
    ("chest_indrawing", "Does the chest draw in during breathing?", "کیا سانس لیتے وقت سینہ اندر جاتا ہے؟", "respiratory", "bool"),
    ("stridor", "Is there a harsh breathing sound when calm?", "کیا سکون میں سانس کی سخت آواز ہے؟", "respiratory", "bool"),
    ("wheeze", "Is there wheezing?", "کیا سانس میں سیٹی آتی ہے؟", "respiratory", "bool"),
    ("vomiting", "Has there been vomiting?", "کیا الٹی ہوئی ہے؟", "always", "bool"),
    ("diarrhoea", "Has there been diarrhoea?", "کیا دست ہیں؟", "always", "bool"),
    ("convulsions", "Have there been fits?", "کیا دورے پڑے ہیں؟", "always", "bool"),
    ("rash", "Is there a rash?", "کیا جسم پر دانے ہیں؟", "always", "bool"),
    ("drinking", "Is the patient drinking or feeding normally?", "کیا مریض معمول کے مطابق پی رہا ہے؟", "always", "bool"),
    ("urine_reduced", "Is urine reduced?", "کیا پیشاب کم ہے؟", "always", "bool"),
    ("neck_stiffness", "Is the neck stiff?", "کیا گردن اکڑی ہوئی ہے؟", "fever", "bool"),
    ("bleeding", "Is there unusual bleeding?", "کیا غیر معمولی خون بہہ رہا ہے؟", "fever", "bool"),
    ("persistent_vomiting", "Is vomiting persistent?", "کیا بار بار الٹی ہو رہی ہے؟", "fever", "bool"),
    ("abdominal_pain", "Is there abdominal pain?", "کیا پیٹ میں درد ہے؟", "fever", "bool"),
    ("haemoptysis", "Is there blood in the sputum?", "کیا بلغم میں خون ہے؟", "cough", "bool"),
    ("weight_loss", "Has there been weight loss?", "کیا وزن کم ہوا ہے؟", "tb", "bool"),
    ("night_sweats", "Are there night sweats?", "کیا رات کو پسینہ آتا ہے؟", "tb", "bool"),
    ("tb_contact", "Has there been contact with someone with TB?", "کیا ٹی بی کے مریض سے رابطہ ہوا؟", "tb", "bool"),
    ("diabetes", "Does the patient have diabetes?", "کیا مریض کو شوگر ہے؟", "tb", "bool"),
    ("previous_tb", "Has the patient previously had TB?", "کیا پہلے ٹی بی ہوئی ہے؟", "tb", "bool"),
    ("recurrent_wheeze", "Has wheezing happened before?", "کیا پہلے بھی سانس میں سیٹی آئی؟", "wheeze", "bool"),
    ("night_cough", "Is the cough worse at night?", "کیا کھانسی رات کو بڑھتی ہے؟", "wheeze", "bool"),
    ("asthma_history", "Is there a history of asthma?", "کیا دمہ کی تاریخ ہے؟", "wheeze", "bool"),
    ("smoke_exposure", "Is there tobacco or cooking-smoke exposure?", "کیا تمباکو یا چولہے کے دھوئیں سے واسطہ ہے؟", "respiratory", "bool"),
    ("sick_contact", "Has anyone nearby had similar symptoms?", "کیا آس پاس کسی کو ایسی علامات ہیں؟", "always", "bool"),
    ("immunisation_complete", "Are the child's vaccinations up to date?", "کیا بچے کے حفاظتی ٹیکے مکمل ہیں؟", "child", "bool"),
    ("premature_birth", "Was the child born prematurely?", "کیا بچہ وقت سے پہلے پیدا ہوا تھا؟", "child", "bool"),
    ("past_admission", "Has the patient been admitted before?", "کیا پہلے ہسپتال میں داخل ہوا؟", "always", "bool"),
    ("allergy_penicillin", "Is there a known penicillin allergy?", "کیا پینسلین سے الرجی ہے؟", "always", "bool"),
    ("allergies", "List other allergies, or explicitly write none or unknown.", "دوسری الرجی لکھیں، یا کوئی نہیں یا معلوم نہیں لکھیں۔", "always", "text"),
    ("chronic_conditions", "List existing illnesses, or none or unknown.", "پرانی بیماریاں لکھیں، یا کوئی نہیں یا معلوم نہیں۔", "always", "text"),
    ("breastfeeding", "Is the patient breastfeeding?", "کیا مریضہ دودھ پلا رہی ہے؟", "adult", "bool"),
    ("medicine_history", "What has been taken, how much, for how long, and who advised it?", "کون سی دوا، کتنی، کب سے اور کس کے مشورے پر لی؟", "always", "text"),
    ("worker_request", "What question should the doctor answer?", "ڈاکٹر سے کیا پوچھنا ہے؟", "always", "text"),
]
QUESTIONS = [{"id": f"Q{i:02}", "asks_key": key, "text_en": en, "text_ur": ur,
              "trigger": trigger, "kind": kind, "priority": len(_BANK) - i,
              "pertinent_negative": key in {"vomiting", "diarrhoea", "convulsions", "rash"},
              "why_ur": "یہ معلومات ڈاکٹر کو مکمل صورت حال سمجھنے میں مدد دیتی ہیں۔"}
             for i, (key, en, ur, trigger, kind) in enumerate(_BANK, 1)]

# Editable configuration takes precedence over the seed catalogue above.
from .clinical import ROOT
import yaml
_question_file = ROOT / "config/question_bank.yaml"
if _question_file.exists():
    QUESTIONS = yaml.safe_load(_question_file.read_text(encoding="utf-8"))["questions"]
_extensions = ROOT / "config/question_extensions.yaml"
if _extensions.exists():
    QUESTIONS += yaml.safe_load(_extensions.read_text(encoding="utf-8"))["questions"]
QUESTIONS = sorted(QUESTIONS, key=lambda q: -q["priority"])


def applicable(facts: list[dict], age: Age) -> list[dict]:
    v = {f["key"]: f["value"] for f in facts if f.get("confirmed")}
    respiratory = any(v.get(k) is True for k in ("cough", "breathlessness", "wheeze"))
    from .clinical import config
    triggers = {"always": True, "respiratory": respiratory, "cough": v.get("cough") is True,
                "fever": v.get("fever") is True, "wheeze": v.get("wheeze") is True, "diarrhoea": v.get("diarrhoea") is True,
                "tb": (v.get("cough_days") or 0) >= config()["thresholds"]["tb_cough_days"],
                "child": age.pathway in ("young_infant", "child", "older_child"),
                "adult": age.pathway in ("adult", "older_adult")}
    return [q for q in QUESTIONS if triggers.get(q["trigger"], False)]


def next_question(facts: list[dict], age: Age) -> tuple[dict | None, float]:
    answered = {f["key"] for f in facts if f.get("confirmed")}
    bank = applicable(facts, age)
    remaining = [q for q in bank if q["asks_key"] not in answered]
    return (remaining[0] if remaining else None, (len(bank) - len(remaining)) / max(1, len(bank)))


def collection_status(facts: list[dict], age: Age) -> dict:
    current = {f["key"]: f for f in facts if f.get("confirmed")}
    rows = [{"id": q["id"], "key": q["asks_key"], "question": q["text_en"],
             "status": "not asked" if q["asks_key"] not in current else "unknown" if current[q["asks_key"]]["value"] is None else "known",
             "reason": current.get(q["asks_key"], {}).get("reason_missing") or ""} for q in applicable(facts, age)]
    return {"questions": rows, **{status: sum(row["status"] == status for row in rows) for status in ("known", "unknown", "not asked")}}


def explanation(question: dict, language: str) -> str:
    key = question["asks_key"]
    if question["kind"] == "number" or key in ("symptom_onset", "symptom_course", "symptom_severity"):
        return "This records when symptoms began and how they changed, for the doctor's review." if language == "English" else "یہ سوال علامت کا وقت، مدت یا تبدیلی ڈاکٹر کو بتاتا ہے۔"
    if key in ("medicines", "medicine_history", "allergies", "allergy_penicillin", "breastfeeding"):
        return "The doctor needs the reported medicine and allergy history before making a treatment decision." if language == "English" else "علاج کا فیصلہ کرنے سے پہلے ڈاکٹر کو دوا اور الرجی کی معلومات چاہییں۔"
    return "Record both yes and no answers. An unknown answer remains visible to the doctor." if language == "English" else "ہاں اور نہیں دونوں جواب درج کریں۔ نامعلوم جواب بھی ڈاکٹر کو دکھایا جائے گا۔"
