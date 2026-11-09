"""Bilingual fact rendering shared by read-back, clinical evidence and packet export.

Sentences are deterministic representations of confirmed evidence, rather than
free clinical prose. The verifier can compare them to the exact ledger snapshot.
"""
from __future__ import annotations
import json
from .clinical import config
from .questions import QUESTIONS

LABELS = {
    "cough": ("Cough", "کھانسی"), "fever": ("Fever", "بخار"),
    "breathlessness": ("Breathing difficulty", "سانس میں مشکل"),
    "resp_rate": ("Respiratory rate", "سانس فی منٹ"), "spo2": ("Oxygen saturation", "آکسیجن"),
    "temperature": ("Temperature", "درجہ حرارت"), "weight": ("Weight", "وزن"),
    "pulse": ("Pulse", "نبض"), "systolic": ("Systolic blood pressure", "اوپر کا بلڈ پریشر"),
    "diastolic": ("Diastolic blood pressure", "نیچے کا بلڈ پریشر"), "muac": ("MUAC", "بازو کا گھیر"),
    "cough_days": ("Cough duration", "کھانسی کے دن"), "fever_days": ("Fever duration", "بخار کے دن"),
    "vomiting": ("Vomiting", "الٹی"), "diarrhoea": ("Diarrhoea", "دست"),
    "chest_indrawing": ("Chest indrawing", "سینے کا اندر جانا"), "stridor": ("Stridor when calm", "سکون میں سانس کی سخت آواز"),
    "medicines": ("Medicines reported", "بتائی گئی دوائیں"), "medicine_records": ("Medicine details", "دوا کی تفصیل"),
    "reported_doses": ("Confirmed reported medicine amounts", "بتائی گئی دوا کی تصدیق شدہ مقدار"),
    "allergies": ("Other allergies", "دوسری الرجی"), "allergy_penicillin": ("Penicillin allergy", "پینسلین سے الرجی"),
    "chronic_conditions": ("Existing illnesses", "پرانی بیماریاں"), "worker_request": ("Question for doctor", "ڈاکٹر سے سوال"),
    "pregnant": ("Pregnant", "حاملہ"), "breastfeeding": ("Breastfeeding", "دودھ پلانا"),
    "chief_complaint": ("Main concern", "اصل تکلیف"), "symptom_onset": ("Symptoms started", "علامات کا آغاز"),
    "symptom_course": ("Course of symptoms", "علامات میں تبدیلی"), "symptom_severity": ("Reported severity", "تکلیف کی شدت"),
    "followup_worse": ("Condition reported worse", "حالت پہلے سے خراب بتائی گئی"),
    "followup_danger": ("New danger or inability to drink reported", "نئی خطرے کی علامت یا پینے سے قاصر ہونا بتایا گیا"),
    "fever_context": ("Illness location and season reference", "بیماری کا مقام اور موسم کی تاریخ"),
}


def label(key: str, language: str = "English") -> str:
    if key.startswith("lab_result_"):
        return "لیبارٹری رپورٹ" if language == "Urdu" else "Laboratory report"
    if key.startswith("requested_media_"):
        return "مانگی گئی تصویر یا آواز" if language == "Urdu" else "Requested photo or audio"
    pair = LABELS.get(key)
    if pair:
        return pair[1 if language == "Urdu" else 0]
    question = next((q for q in QUESTIONS if q["asks_key"] == key), None)
    if question:
        return question["text_ur" if language == "Urdu" else "text_en"].rstrip("?؟")
    return key.replace("_", " ").capitalize()


def display_value(fact: dict, language: str = "English") -> str:
    value = fact["value"]
    urdu = language == "Urdu"
    if value is None:
        return ("معلوم نہیں، وجہ: " if urdu else "Unknown / not done; reason: ") + str(fact.get("reason_missing") or ("درج نہیں" if urdu else "not recorded"))
    if type(value) is bool:
        return ("ہاں" if value else "نہیں") if urdu else ("present / yes" if value else "absent / no")
    if fact["key"] == "age" and isinstance(value, dict):
        units = {"days": "دن", "weeks": "ہفتے", "months": "ماہ", "years": "سال"}
        text = f"{value['value']:g} {units.get(value['unit'], value['unit'])}" if urdu else f"{value['value']:g} {value['unit']}"
        if value.get("date_of_birth") and value.get("as_of"):
            text += (f"؛ تاریخ پیدائش {value['date_of_birth']}، عمر کی تاریخ {value['as_of']}" if urdu else f"; DOB {value['date_of_birth']}, age as of {value['as_of']}")
        return text
    if fact["key"] == "fever_context" and isinstance(value, dict):
        if urdu:
            return f"ملک: {value.get('country') or 'نامعلوم'}؛ صوبہ: {value.get('province') or 'نامعلوم'}؛ ضلع: {value.get('district') or 'نامعلوم'}؛ تاریخ: {value['observed_on']}؛ وجہ: {value.get('unknown_reason') or '—'}؛ اطلاع: {value.get('notes') or '—'}"
        return f"Country: {value.get('country') or 'unknown'}; province: {value.get('province') or 'unknown'}; district: {value.get('district') or 'unknown'}; date: {value['observed_on']}; unknown reason: {value.get('unknown_reason') or '—'}; reported context: {value.get('notes') or '—'}"
    if isinstance(value, list):
        if fact["key"] == "reported_doses":
            def rendered_amount(row):
                unknown = "نامعلوم" if urdu else "unknown"
                amount = f"{row['dose_mg']:g} mg" if row.get("dose_mg") is not None else unknown
                regimen, product = row["regimen"], row["product"]
                daily, duration = regimen.get("doses_per_day"), regimen.get("duration_days")
                daily = f"{daily:g}" if daily is not None else unknown
                duration = f"{duration:g}" if duration is not None else unknown
                return (f"{row['medicine']}: {amount}؛ 24 گھنٹے میں زیادہ سے زیادہ خوراکیں: {daily}؛ دن: {duration}؛ راستہ: {product['route']}؛ وجہ استعمال: {regimen.get('indication') or unknown}؛ صرف ضرورت پر: {'ہاں' if regimen.get('as_needed') else 'نہیں'}؛ نامعلوم کی وجہ: {row.get('unknown_reason') or '—'}" if urdu else
                    f"{row['medicine']}: {amount}; maximum doses in 24h: {daily}; days: {duration}; route: {product['route']}; reported indication: {regimen.get('indication') or unknown}; only as needed: {regimen.get('as_needed', False)}; unknown reason: {row.get('unknown_reason') or '—'}")
            return "; ".join(rendered_amount(row) for row in value) or ("کوئی درج نہیں" if urdu else "none recorded")
        if fact["key"] == "medicine_records":
            fields = [("strength", "طاقت" if urdu else "strength"), ("formulation", "شکل" if urdu else "form"),
                      ("amount", "لی گئی مقدار" if urdu else "amount taken"), ("frequency", "کتنی بار" if urdu else "frequency"),
                      ("duration", "مدت" if urdu else "duration"), ("adviser", "مشورہ" if urdu else "adviser")]
            return "; ".join(m.get("name", "?") + ": " + ", ".join(label + " " + str(m.get(key, "unknown")) for key, label in fields) for m in value) or ("کوئی درج نہیں" if urdu else "none recorded")
        return ", ".join(str(v) for v in value) or ("کوئی درج نہیں" if urdu else "none reported")
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    unit = fact.get("unit") or ("days" if fact["key"].endswith("_days") else "")
    return str(value) + (" " + unit if unit else "")


def sentence(fact: dict, language: str = "English", citation: bool = True) -> str:
    text = ("عمر" if language == "Urdu" else "Age") if fact["key"] == "age" else label(fact["key"], language)
    return f"{text}: {display_value(fact, language)}" + (f" [{fact['id']}]" if citation else "")


def full_readback(facts: list[dict], language: str = "English") -> dict:
    lines = [sentence(f, language, citation=False) for f in facts if f.get("confirmed") and f["key"] not in ("transcript",)]
    return {"language": language, "text": ("۔\n" if language == "Urdu" else ".\n").join(lines),
            "fact_ids": [f["id"] for f in facts if f.get("confirmed") and f["key"] != "transcript"], "lines": lines}
