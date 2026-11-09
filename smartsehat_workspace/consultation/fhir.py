"""FHIR R4 document-shaped export of confirmed source facts.

Uses text codes where no verified terminology mapping is configured. This is
an interoperability export, not a claim of validation against a provincial IG.
"""
from __future__ import annotations
from datetime import datetime, timezone
import html
import json
import uuid


def bundle(case: dict, facts: list[dict], packet: dict | None, tasks: list[dict], reply: dict | None, care: dict | None = None) -> dict:
    if packet and packet.get("stale"):
        packet = None
    cid = case["id"]
    patient_id = cid + "-patient"
    patient = {"resourceType": "Patient", "id": patient_id,
               "identifier": [{"system": "urn:smartsehat:fictional-patient", "value": cid}],
               "name": [{"text": case["data"]["name"]}],
               "meta": {"tag": [{"system": "urn:smartsehat", "code": "synthetic"}]}}
    sex = case["data"]["sex"].lower()
    patient["gender"] = sex if sex in ("male", "female") else "unknown"
    age_fact = next((f for f in facts if f.get("confirmed") and f["key"] == "age"), None)
    if age_fact and age_fact["value"].get("date_of_birth"):
        patient["birthDate"] = age_fact["value"]["date_of_birth"]
    subject = {"reference": f"Patient/{patient_id}"}
    encounter = {"resourceType": "Encounter", "id": cid, "status": "finished" if case["stage"] == "CLOSED" else "in-progress",
                 "class": {"system": "http://terminology.hl7.org/CodeSystem/v3-ActCode", "code": "AMB"},
                 "subject": subject, "period": {"start": case["created"]}}
    observations, provenance, fact_references = [], [], {}
    for fact in facts:
        if not fact.get("confirmed"):
            continue
        rid = cid + "-" + fact["id"]
        observation = {"resourceType": "Observation", "id": rid, "status": "final",
            "code": {"text": fact["key"]}, "subject": subject,
            "encounter": {"reference": f"Encounter/{cid}"}, "effectiveDateTime": fact["ts"],
            "identifier": [{"system": "urn:smartsehat:fact", "value": fact["id"]}]}
        value = fact["value"]
        if value is None:
            observation["dataAbsentReason"] = {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/data-absent-reason", "code": "unknown"}], "text": fact.get("reason_missing") or "Not recorded"}
        elif fact["key"] == "age":
            observation["valueQuantity"] = {"value": value["days"], "unit": "days", "system": "http://unitsofmeasure.org", "code": "d"}
        elif type(value) is bool:
            observation["valueBoolean"] = value
        elif type(value) in (int, float):
            observation["valueQuantity"] = {"value": value, "unit": fact.get("unit") or "unspecified"}
        elif isinstance(value, dict) and "value" in value and "unit" in value and type(value["value"]) in (int, float):
            observation["valueQuantity"] = {"value": value["value"], "unit": value["unit"]}
        else:
            observation["valueString"] = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        from .clinical import config
        from .poc import tests
        if fact["key"] not in set(config()["measurements"]) | set(tests()) | {"age"}:
            answers = {k: v for k, v in observation.items() if k.startswith("value")}
            if not answers:
                answers = {"valueString": "Unknown: " + (fact.get("reason_missing") or "Not recorded")}
            observation = {"resourceType": "QuestionnaireResponse", "id": rid, "status": "completed", "subject": subject,
                "encounter": {"reference": f"Encounter/{cid}"}, "authored": fact["ts"],
                "item": [{"linkId": fact["key"], "text": fact["key"].replace("_", " "), "answer": [answers]}]}
        if fact["key"] == "doctor_diagnosis":
            observation = {"resourceType": "Condition", "id": rid, "subject": subject, "encounter": {"reference": f"Encounter/{cid}"},
                "recordedDate": fact["ts"], "code": {"text": str(value)}, "note": [{"text": "Doctor-authored assessment; retained exactly as recorded."}]}
        fact_references[fact["id"]] = f"{observation['resourceType']}/{rid}"
        observations.append(observation)
        proven = {"resourceType": "Provenance", "id": rid + "-source", "recorded": fact["ts"],
            "target": [{"reference": fact_references[fact["id"]]}],
            "agent": [{"who": {"display": fact["source"]}, "role": [{"text": "Confirmed fact source"}]}]}
        if fact.get("supersedes"):
            proven["entity"] = [{"role": "revision", "what": {"identifier": {"system": "urn:smartsehat:fact", "value": fact["supersedes"]}}}]
        provenance.append(proven)
    composition = {"resourceType": "Composition", "id": cid + "-sbar", "status": "final" if packet and packet["verification"]["status"] == "VERIFIED" and not packet.get("stale") else "preliminary",
        "type": {"text": "SBAR remote consultation"}, "subject": subject, "encounter": {"reference": f"Encounter/{cid}"},
        "date": (packet or {}).get("created", case["created"]), "author": [{"display": "Worker-confirmed SmartSehat consultation"}],
        "title": "Synthetic consultation SBAR", "section": []}
    for section, rows in (packet or {}).get("sections", {}).items():
        text = "".join("<p>" + html.escape(row["text"]) + "</p>" for row in rows)
        ids = list(dict.fromkeys(fid for row in rows for fid in row["fact_ids"]))
        composition["section"].append({"title": section,
            "text": {"status": "generated", "div": '<div xmlns="http://www.w3.org/1999/xhtml">' + text + "</div>"},
            "entry": [{"reference": fact_references[fid]} for fid in ids if fid in fact_references]})
    task_resources = [{"resourceType": "Task", "id": cid + "-task-" + t["task_id"], "status": "cancelled" if t.get("cancelled") else "completed" if t["done"] else "requested",
                       "intent": "order", "description": t["text"], "code": {"text": t["key"]}, "for": subject,
                       "encounter": {"reference": f"Encounter/{cid}"}} for t in tasks]
    plans = []
    if reply:
        plans.append({"resourceType": "CarePlan", "id": cid + "-plan", "status": "completed" if case["stage"] == "CLOSED" else "active",
            "intent": "plan", "subject": subject, "encounter": {"reference": f"Encounter/{cid}"},
            "description": reply["advice"], "note": [{"text": "Doctor-authored response (" + reply.get("status", "final") + "). Clinician identity/delivery simulated in this prototype."}]})
    composition["section"].append({"title": "Confirmed source record and doctor workflow", "entry": [{"reference": f"{r['resourceType']}/{r['id']}"} for r in observations + task_resources + plans]})
    if care:
        lines = ["Reported referral to " + r["facility"]["name"] + ": " + r["status"] for r in care.get("referrals", [])]
        lines += ["Reported requested contact status: " + c["status"] for c in care.get("calls", [])]
        lines += ["Worker request (" + r["status"] + "): " + r["text"] for r in care.get("requests", [])]
        lines += ["Counselling via " + g["channel"] + "; patient reported understanding: " + str(g["understood"]) for g in care.get("guidance", [])]
        if lines:
            composition["section"].append({"title": "Reported counselling and handoff status", "text": {"status": "generated", "div": '<div xmlns="http://www.w3.org/1999/xhtml">' + "".join("<p>" + html.escape(line) + "</p>" for line in lines) + "</div>"}})
    resources = [composition, patient, encounter, *observations, *provenance, *task_resources, *plans]
    stamp = datetime.now(timezone.utc).isoformat()
    return {"resourceType": "Bundle", "id": str(uuid.uuid4()), "type": "document", "timestamp": stamp,
        "identifier": {"system": "urn:ietf:rfc:3986", "value": "urn:uuid:" + str(uuid.uuid4())},
        "meta": {"lastUpdated": stamp, "tag": [{"system": "urn:smartsehat", "code": "synthetic-research-prototype"}]},
        "entry": [{"fullUrl": f"https://smartsehat_workspace.invalid/fhir/{r['resourceType']}/{r['id']}", "resource": r} for r in resources]}
