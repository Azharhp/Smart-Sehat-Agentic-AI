"""Fixed SBAR composition with a compact transport packet and complete appendix."""
from __future__ import annotations
import json
from .clinical import config
from .narrative import sentence, full_readback, display_value
from .questions import applicable

BACKGROUND = {"pregnant", "cough_days", "fever_days", "symptom_onset", "symptom_course", "symptom_severity", "chronic_conditions", "allergies", "allergy_penicillin", "medicines", "medicine_records", "medicine_history", "breastfeeding", "previous_tb", "tb_contact", "past_admission", "immunisation_complete", "premature_birth", "occupation", "exposure_history", "fever_context"}
EXCLUDE = {"age", "transcript", "worker_request", "chief_complaint", "ai_concern"}


def compose(case: dict, facts: list[dict], urgency: dict, analysis: dict, medcheck: dict,
            events: list[dict], version: int, verification: dict) -> dict:
    confirmed = [f for f in facts if f.get("confirmed")]
    by_key = {f["key"]: f for f in confirmed}
    age_data = by_key["age"]["value"]
    from .clinical import Age
    required_questions = applicable(confirmed, Age.from_input(age_data["value"], age_data["unit"]))
    remaining_questions = [q["asks_key"] for q in required_questions if q["asks_key"] not in by_key or by_key[q["asks_key"]]["value"] is None]
    vitals = set(config()["measurements"])
    sections = {name: [] for name in ("Situation", "Background", "Assessment", "Request")}
    source_lines = []

    def line(section: str, text: str, source: str, fact_ids: list[str], event_ids: list[int] | None = None) -> None:
        row = {"text": text, "source": source, "fact_ids": fact_ids, "section": section}
        if event_ids:
            row["event_ids"] = event_ids
        sections[section].append(row)
        source_lines.append(row)

    for fact in confirmed:
        key = fact["key"]
        if key == "transcript":
            continue
        section = "Request" if key == "worker_request" else "Situation" if key in ("chief_complaint", "age") else "Background" if key in BACKGROUND or fact["value"] is False else "Assessment"
        source = {"device": "Device", "doctor": "Doctor", "rule": "Rule"}.get(fact["source"], "Worker-confirmed")
        line(section, sentence(fact), source, [fact["id"]])
    if not sections["Request"]:
        line("Request", "Worker request not yet recorded; doctor review requested.", "Rule", [])
    from .care import project
    open_requests = [request for request in project(events)["requests"] if request["status"] == "open"]
    for request in open_requests:
        line("Request", request["text"] + f" [E{request['revision']}]", "Worker request", [], [request["revision"]])
    for rule in urgency["fired"]:
        line("Assessment", rule["rule_id"] + ": " + rule["text"], "Rule", rule["fact_ids"])
    med_ids = [f["id"] for f in confirmed if f["key"] in ("medicines", "medicine_records", "reported_doses", "allergies", "allergy_penicillin", "breastfeeding")]
    for flag in medcheck["flags"]:
        line("Assessment", flag["rule_id"] + ": " + flag["text"], "Rule", med_ids)
    for item in analysis["considerations"]:
        line("Assessment", f"Consideration {item['rank']}: {item['title']} (doctor decides)", "AI-proposed", item["fact_ids"])
    from .epidemiology import context_for
    local_context = analysis.get("fever_context") or context_for(facts)
    if local_context["status"] != "NOT_APPLICABLE":
        line("Background", "District/season context: " + local_context["status"] + "; doctor interprets, no diagnostic probability.", "Context availability", local_context["fact_ids"])
    for entry in local_context["entries"]:
        line("Assessment", entry["condition"] + ": " + entry["context"] + f" [context {entry['id']}; source {entry['source']}; reviewed {entry['reviewed_by']} on {entry['reviewed_on']}]", "Reviewed local context", local_context["fact_ids"])
    data = case["data"]
    header = f"SYNTHETIC | {urgency['level']} | {data['name']} | {display_value(by_key['age'])} | {data['sex']} | Pregnancy: {'Yes' if data['pregnant'] else 'No'} | {data['village']} | Language: {data['language']}"
    full = [header]
    for name, rows in sections.items():
        full += ["", name.upper()]
        full += [f"[{row['source']}] {row['text']}" for row in rows]
    media = [{"event_id": e["seq"], "type": e["type"], **e["data"]} for e in events if e["type"] in ("PHOTO_ATTACHED", "AUDIO_ATTACHED", "COUGH_AUDIO_ATTACHED", "MEDICINE_IMAGE_READ", "DOCTOR_AUDIO_ATTACHED")]
    full += ["", "APPENDIX", f"{len(confirmed)} confirmed facts; {len(events)} audit events; {len(media)} media items.",
             "Complete source facts, media references and timeline accompany the structured packet."]
    compact = [header, "SITUATION"]
    compact += [sentence(f) for f in confirmed if f["key"] in ("chief_complaint", "cough", "fever", "breathlessness", "cough_days", "fever_days")]
    compact += ["BACKGROUND"]
    compact += [sentence(f) for f in confirmed if f["key"] in ("medicines", "allergy_penicillin", "allergies", "chronic_conditions", "fever_context")]
    compact += ["ASSESSMENT"]
    compact += [sentence(f) for f in confirmed if f["key"] in vitals]
    compact += ["[Rule] " + ", ".join(r["rule_id"] for r in urgency["fired"]) or "No configured urgency rule fired."]
    compact += ["Missing critical: " + (", ".join(urgency["missing_critical"]) or "none")]
    compact += ["REQUEST", sentence(by_key["worker_request"]) if "worker_request" in by_key else "Please review the complete case."]
    compact += ["[Worker request] " + request["text"] + f" [E{request['revision']}]" for request in open_requests]
    compact += [f"APPENDIX: {case['id']} packet v{version}; full SBAR, facts, media and audit attached."]
    compact_text = "\n".join(compact)
    # Arbitrary patient text is never silently truncated. Large cases explicitly
    # use the structured/full packet instead of claiming to fit a network budget.
    return {"version": version, "fact_ids": [f["id"] for f in confirmed], "verification": verification,
            "verification_contract": "ledger-and-considerations-v1",
            "text": compact_text, "compact_text": compact_text, "compact_bytes": len(compact_text.encode()),
            "transport_budget_exceeded": len(compact_text.encode()) > 3072, "full_text": "\n".join(full),
            "sections": sections, "source_lines": source_lines, "considerations": analysis["considerations"],
            "references": [{k: r[k] for k in ("id", "text", "source") if k in r} for r in analysis["references"]], "fever_context": local_context,
            "gaps": list(dict.fromkeys(analysis["missing"] + remaining_questions + (["fever_context"] if local_context["status"] in ("INPUT_REQUIRED", "INPUT_INVALID") else []))), "suggested_measurements": analysis["suggested_measurements"],
            "collection_completeness": sum(q["asks_key"] in by_key for q in required_questions) / max(1, len(required_questions)),
            "ranking_basis": analysis.get("ranking_basis", ""), "mode": analysis["mode"], "urgency": urgency,
            "ai_concern": {"level": analysis.get("ai_concern", "GREEN"), "fact_ids": analysis.get("concern_fact_ids", [])},
            "medcheck": medcheck, "appendix": {"facts": confirmed, "timeline": events, "media": media},
            "readbacks": {lang: full_readback(confirmed, lang) for lang in ("English", "Urdu")},
            "dependencies": {"fact_ids": [f["id"] for f in confirmed], "keys": ["*"]}}
