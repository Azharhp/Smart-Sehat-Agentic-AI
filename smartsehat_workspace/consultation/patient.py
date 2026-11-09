"""Doctor-authored patient plan and guarded AI wording assistance."""
from __future__ import annotations
import hashlib
import json
import re
import unicodedata
from pydantic import BaseModel, ConfigDict, Field
from .ai import call_json
from .medicines import catalogue


class Wording(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text_ur: str = Field(min_length=1, max_length=5000)


class Schedule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    medicine: str = Field(min_length=1, max_length=120)
    dose: str = Field(min_length=1, max_length=150)
    morning: bool = False
    noon: bool = False
    evening: bool = False
    night: bool = False
    notes: str = Field(default="", max_length=500)


def complete_guidance(reply: dict, language: str = "Urdu") -> str:
    """Read all written doctor instructions verbatim, without inferred doses.

    Labels are bilingual presentation only. This does not translate or shorten
    the doctor's text. System-generated follow-up dates stay on the card rather
    than entering a recording prepared before the response is saved.
    """
    if language not in ("English", "Urdu"):
        raise ValueError("Use English or Urdu labels, or provide a human recording for this language.")
    urdu = language == "Urdu"
    label = lambda en, ur: ur if urdu else en
    lines = [label("Doctor's complete written patient instructions", "ڈاکٹر کی مکمل تحریری ہدایات")]
    lines.append(label("Interim guidance: awaiting requested information or results.", "عبوری ہدایات: مطلوبہ معلومات یا نتائج کا انتظار ہے۔")
                 if reply.get("status", "final") == "awaiting_information"
                 else label("Recorded final doctor plan.", "ڈاکٹر کا درج کردہ حتمی منصوبہ۔"))
    fields = (("advice", "Doctor advice", "ڈاکٹر کا مشورہ"),
              ("local_language", "Doctor-confirmed local wording", "ڈاکٹر کی تصدیق شدہ مقامی زبان میں ہدایات"),
              ("prescription", "Written prescription", "تحریری نسخہ"))
    for key, en, ur in fields:
        if str(reply.get(key) or "").strip():
            lines.extend([label(en, ur) + ":", str(reply[key])])
    times = (("morning", "Morning", "صبح"), ("noon", "Noon", "دوپہر"),
             ("evening", "Evening", "شام"), ("night", "Night", "رات"))
    for row in reply.get("medication_schedule") or []:
        if not str(row.get("medicine") or "").strip():
            continue
        lines.extend([label("Medicine schedule", "دوا کا شیڈول") + ":", str(row["medicine"]),
                      label("Doctor's written amount", "ڈاکٹر کی تحریر کردہ مقدار") + ": " + str(row.get("dose") or "")])
        selected = [label(en, ur) for key, en, ur in times if row.get(key) is True]
        if selected:
            lines.append(label("Selected times", "درج کردہ اوقات") + ": " + ", ".join(selected))
        if str(row.get("notes") or "").strip():
            lines.extend([label("Doctor's schedule notes", "شیڈول کے متعلق ڈاکٹر کی ہدایات") + ":", str(row["notes"])])
    for key, en, ur in (("feeding", "Feeding and fluids", "خوراک اور مائعات"),
                        ("ors", "Doctor's ORS instructions", "او آر ایس کے متعلق ڈاکٹر کی ہدایات"),
                        ("counselling_script", "Doctor's counselling script", "ڈاکٹر کی مشاورت کی ہدایات"),
                        ("return_if", "Return precautions", "واپس آنے یا مدد لینے کی علامات"),
                        ("referral", "Referral", "ریفرل")):
        if str(reply.get(key) or "").strip():
            lines.extend([label(en, ur) + ":", str(reply[key])])
    for key, en, ur in (("local_tests", "Doctor-requested local tests", "ڈاکٹر کے مطلوبہ مقامی ٹیسٹ"),
                        ("lab_tests", "Doctor-requested referral laboratory tests", "ڈاکٹر کے مطلوبہ ریفرل لیبارٹری ٹیسٹ")):
        if reply.get(key):
            lines.extend([label(en, ur) + ":", ", ".join(str(item) for item in reply[key])])
    if reply.get("request_call") is True:
        lines.append(label("The doctor requested contact through the agreed care-team channel.", "ڈاکٹر نے طے شدہ طبی رابطے کے ذریعے رابطہ کرنے کو کہا ہے۔"))
    return "\n".join(lines)


def _numbers(text: str) -> list[str]:
    normalized = "".join(str(unicodedata.decimal(c)) if c.isdecimal() else c for c in text)
    return sorted(re.findall(r"\d+(?:\.\d+)?", normalized))


def guidance_confirmation_id(cid: str, advice: str, wording: str, snapshot: dict) -> str:
    """Approval binds the exact wording, source advice and reviewed case inputs."""
    content = {"case_id": cid, "advice": advice, "wording": wording,
               "fact_ids": snapshot["fact_ids"], "media_ids": snapshot["media_ids"], "care_revision": snapshot["care_revision"], "handoff_id": snapshot.get("handoff_id"), "fever_catalogue_hash": snapshot.get("fever_catalogue_hash")}
    return hashlib.sha256(json.dumps(content, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _quantities(text: str) -> list[tuple[str, str]]:
    normalized = "".join(str(unicodedata.decimal(c)) if c.isdecimal() else c for c in text).casefold()
    # Preserve written quantities exactly. This is a text guard, not a dose or
    # translation validator; the doctor must still approve the full meaning.
    units = r"mmol/l|mg/dl|g/dl|mmhg|mcg|µg|μg|mg|kg|ml|bpm|cm|°c|°f|g|l|tablets?|capsules?|drops?|puffs?|days?|weeks?|hours?|minutes?"
    return sorted((number, unit) for number, unit in re.findall(r"(\d+(?:\.\d+)?)\s*(" + units + r")(?!\w)", normalized))


def simplify_doctor_advice(text: str, live: bool) -> dict:
    result, mode = call_json("doctor-reword", {"doctor_authored_advice": text,
            "rules": "Rewrite in simple Urdu. Preserve every medicine name in Latin script, every number/unit, timing, warning and negation. Do not add or remove any clinical instruction. Doctor approval is mandatory."},
            Wording, {"text_ur": text}, live)
    rewritten = result["text_ur"]
    medicines = [name for name in catalogue() if re.search(r"(?<!\w)" + re.escape(name) + r"(?!\w)", text.casefold())]
    negation = r"\bno\b|\bnot\b|\bnever\b|\bavoid\b|نہیں|\bمت\b|\bنہ\b"
    valid = _numbers(text) == _numbers(rewritten) and _quantities(text) == _quantities(rewritten) and all(name in rewritten.casefold() for name in medicines) and len(re.findall(negation, text, re.I)) == len(re.findall(negation, rewritten, re.I))
    return {"text": rewritten if valid else text, "mode": mode if valid else "AI wording rejected: quantities/units, medicine names or negation changed; original retained",
            "doctor_confirmation_required": True, "source_hash": hashlib.sha256(text.encode()).hexdigest()}


TEMPLATES = {
    "Advice": "Please follow the advice recorded below. Contact us again if the recorded warning signs occur.",
    "Need more information": "Please complete the requested observations and return the answers before I finalise the care plan.",
    "Referral": "Please arrange assessment at the selected referral facility and follow the recorded local emergency protocol where applicable.",
    "Tests": "Please arrange the selected tests. Return the results for my review before changing the care plan.",
    "Call request": "Please contact me for a call to clarify the case before I finalise advice.",
}
