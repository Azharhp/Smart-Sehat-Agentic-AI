"""Version-bound quick danger capture and explicitly device-reported timing."""
import hashlib
import json
import math
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field

from .clinical import Age, danger_questions


class DangerObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, allow_inf_nan=False)
    capture_event: int = Field(gt=0, strict=True)
    observer: str = Field(min_length=2, max_length=200)
    participant_code: str = Field(min_length=2, max_length=100)
    device: str = Field(min_length=2, max_length=300)
    browser: str = Field(min_length=2, max_length=200)
    procedure: Literal["first use", "trained repeat"]
    observed_seconds: float = Field(gt=0, le=86400, strict=True)
    expected_outcome: Literal["screen documented", "positive danger reported"]
    wording_understood: bool = Field(strict=True)
    notes: str = Field(min_length=1, max_length=4000)


def screen_scope(case: dict, facts: list[dict]) -> str:
    from .danger_content import content_hash
    age = next(f["value"] for f in facts if f["key"] == "age")
    questions = danger_questions(Age.from_record(age), case["data"]["pregnant"])
    keys = {q["key"] for q in questions}
    data = {"case": case["id"], "age": age, "pregnant": case["data"]["pregnant"], "questions": questions, "content_hash": content_hash(),
            "answers": [(f["id"], f["key"], f["value"]) for f in facts if f["key"] in keys]}
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


class SafetyCaptureWorkflow:
    def quick_screen_snapshot(self, cid: str) -> dict:
        with self.connect() as db:
            db.execute("BEGIN")
            case, facts = self._case(db, cid), self._facts(db, cid)
            age = Age.from_record(next(f["value"] for f in facts if f["key"] == "age"))
            questions = danger_questions(age, case["data"]["pregnant"])
            keys = {q["key"] for q in questions}
            return {"scope": screen_scope(case, facts), "questions": questions, "language": case["data"]["language"],
                    "answers": {f["key"]: f["value"] for f in facts if f["key"] in keys}}

    def record_quick_screen(self, cid: str, response: dict) -> bool:
        """Human button answers only. Backend recomputes all actual urgency."""
        from .danger_content import content_hash
        if not isinstance(response, dict) or response.get("case_id") != cid:
            raise ValueError("Danger-screen response belongs to a different case.")
        seconds, answers = response.get("elapsed_seconds"), response.get("answers")
        if type(seconds) not in (int, float) or not math.isfinite(seconds) or not 0 <= seconds <= 86400 or not isinstance(answers, dict):
            raise ValueError("Invalid device-reported danger-screen timing/answers.")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute("SELECT data FROM consultation_events WHERE case_id=? AND type='QUICK_DANGER_CAPTURE'", (cid,)).fetchall()
            identity = hashlib.sha256(json.dumps(response, ensure_ascii=False, sort_keys=True, allow_nan=False).encode()).hexdigest()
            if any(self._decode(row["data"])["response_hash"] == identity for row in previous):
                return False
            case, facts = self._case(db, cid), self._facts(db, cid)
            if response.get("scope") != screen_scope(case, facts):
                raise ValueError("The danger-screen evidence changed. Reload and confirm the current screen.")
            age = Age.from_record(next(f["value"] for f in facts if f["key"] == "age"))
            required = {q["key"] for q in danger_questions(age, case["data"]["pregnant"])}
            if not answers or not set(answers) <= required or any(type(value) is not bool and value is not None for value in answers.values()):
                raise ValueError("Only current danger questions with Yes/No/Unknown answers are accepted.")
            if not any(value is True for value in answers.values()) and set(answers) != required:
                raise ValueError("Finish every danger item or report a positive sign immediately.")
            values = {f["key"]: f["value"] for f in facts}
            changes = [{"key": key, "value": value, "reason_missing": "Worker unable to assess" if value is None else None,
                        "source": "worker", "quote": "Worker pressed the labelled quick-screen answer"}
                       for key, value in answers.items() if key not in values or values[key] != value]
            if changes:
                for item in changes:
                    self._append(db, cid, **item)
                self._recompute(db, cid)
            self._event(db, cid, "QUICK_DANGER_CAPTURE", {"response_hash": identity, "elapsed_seconds": seconds,
                "outcome": "positive danger reported" if any(value is True for value in answers.values()) else "screen documented",
                "question_count": len(required), "answered_count": len(answers), "unknown_count": sum(value is None for value in answers.values()),
                "previously_documented_count": sum(key in values for key in required), "new_or_changed_count": len(changes),
                "scope": response["scope"], "content_hash": content_hash(),
                "backend_urgency": self._output(db, cid, "urgency")["level"], "device_reported": True, "independently_observed": False,
                "timing_scope": "Browser elapsed interaction time since this screen loaded; not a validated field usability result."})
            return True

    def report_danger_observation(self, cid: str, report: dict, confirmed: bool = False) -> None:
        from .danger_content import content_hash
        if confirmed is not True:
            raise ValueError("Confirm that an actual separate observer measured this attempt; automated or invented timings are not field observations.")
        parsed = DangerObservation.model_validate(report)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT data FROM consultation_events WHERE case_id=? AND seq=? AND type='QUICK_DANGER_CAPTURE'", (cid, parsed.capture_event)).fetchone()
            capture = self._decode(row["data"]) if row else None
            if not capture or capture.get("content_hash") != content_hash():
                raise ValueError("Choose a capture made with the current danger-screen content on this case.")
            data = {**parsed.model_dump(), "content_hash": capture["content_hash"], "browser_seconds": capture["elapsed_seconds"],
                "capture_outcome": capture["outcome"], "previously_documented_count": capture["previously_documented_count"],
                "unknown_count": capture["unknown_count"], "reported_under_30_seconds": parsed.observed_seconds < 30,
                "outcome_matched": parsed.expected_outcome == capture["outcome"], "observer_reported": True,
                "observer_identity_verified": False, "observation_independently_verified": False,
                "scope": "Reported human observation only. No clinical approval or population usability claim."}
            prior = db.execute("SELECT data FROM consultation_events WHERE case_id=? AND type='DANGER_OBSERVATION_REPORTED'", (cid,)).fetchall()
            if not any(self._decode(item["data"]) == data for item in prior):
                self._event(db, cid, "DANGER_OBSERVATION_REPORTED", data, "reported observer")
