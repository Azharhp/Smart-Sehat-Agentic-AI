"""Seed 30 days of synthetic district activity into the SmartSehat ledger."""
from __future__ import annotations

import json
import random
import sqlite3
from collections import Counter
from datetime import datetime, timedelta, timezone

from smartsehat_workspace import audit


BHUS = [
    ("BHU-0442", "BHU Chak 45 — Rajanpur", "Rajanpur"),
    ("BHU-0511", "BHU Karor — Layyah", "Layyah"),
    ("BHU-0623", "BHU Kot Addu — Muzaffargarh", "Muzaffargarh"),
    ("BHU-0788", "BHU Rojhan — Rajanpur", "Rajanpur"),
]

DISEASES = [
    ("Suspected TB", 0.15),
    ("Acute Respiratory Infection", 0.25),
    ("Acute Gastroenteritis", 0.20),
    ("Malaria-suspect", 0.08),
    ("Typhoid-suspect", 0.06),
    ("Skin infection", 0.05),
    ("Other", 0.21),
]

TRIAGE = {
    "Suspected TB": ("YELLOW", "persistent_cough_tb_screen"),
    "Acute Respiratory Infection": ("GREEN", "stable_respiratory_symptoms"),
    "Acute Gastroenteritis": ("GREEN", "gastroenteritis_no_dehydration"),
    "Malaria-suspect": ("YELLOW", "fever_malaria_screen"),
    "Typhoid-suspect": ("YELLOW", "persistent_fever_referral"),
    "Skin infection": ("GREEN", "localized_skin_infection"),
    "Other": ("GREEN", "no_danger_signs"),
}

PARAMEDICS = {
    "BHU-0442": "PM-0442",
    "BHU-0511": "PM-0511",
    "BHU-0623": "PM-0623",
    "BHU-0788": "PM-0788",
}

_original_now = audit._now


def _set_clock(iso_str: str) -> None:
    audit._now = lambda: iso_str


def _restore_clock() -> None:
    audit._now = _original_now


def _weighted_disease(exclude_tb: bool = False) -> str:
    choices = DISEASES[1:] if exclude_tb else DISEASES
    return random.choices(
        [name for name, _ in choices],
        weights=[weight for _, weight in choices],
        k=1,
    )[0]


def _hitl_plan(schedule: list[tuple[int, str, str | None]]) -> dict[str, list[str]]:
    totals = Counter(bhu_id for _, bhu_id, _ in schedule)
    plan: dict[str, list[str]] = {}
    for bhu_id, total in totals.items():
        if bhu_id == "BHU-0511":
            actions = ["approved"] * total
        else:
            approved_rate, modified_rate = (0.35, 0.45) if bhu_id == "BHU-0788" else (0.70, 0.25)
            approved = round(total * approved_rate)
            modified = round(total * modified_rate)
            actions = ["approved"] * approved + ["modified"] * modified
            actions += ["rejected"] * (total - len(actions))
            random.shuffle(actions)
        plan[bhu_id] = actions
    return plan


def _seed_reference_data(conn: sqlite3.Connection) -> dict[str, list[str]]:
    for bhu in BHUS:
        conn.execute(
            "INSERT OR IGNORE INTO facilities (bhu_id, name, district) VALUES (?, ?, ?)", bhu
        )
    for bhu_id, _, _ in BHUS:
        suffix = bhu_id.split("-")[1]
        conn.execute(
            "INSERT OR IGNORE INTO users (user_id, name, role, bhu_id) VALUES (?, ?, ?, ?)",
            (PARAMEDICS[bhu_id], f"Synthetic Paramedic {suffix}", "paramedic", bhu_id),
        )

    patients_by_bhu: dict[str, list[str]] = {bhu_id: [] for bhu_id, _, _ in BHUS}
    for number in range(1, 41):
        bhu_id = BHUS[(number - 1) // 10][0]
        patient_id = f"PT-SYN-{number:04d}"
        patients_by_bhu[bhu_id].append(patient_id)
        conn.execute(
            "INSERT OR IGNORE INTO patients "
            "(patient_id, name, age, sex, chronic_conditions_json, allergies_json) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                patient_id,
                f"Synthetic Patient {number:02d}",
                random.randint(5, 78),
                random.choice(["M", "F"]),
                json.dumps([]),
                json.dumps([]),
            ),
        )
    conn.commit()
    return patients_by_bhu


def _case_schedule() -> list[tuple[int, str, str | None]]:
    schedule: list[tuple[int, str, str | None]] = []
    for day in (2, 5, 9, 13, 16, 19, 21):
        schedule.append((day, "BHU-0788", "Suspected TB"))
    for day in (24, 24, 25, 25, 26, 26, 27, 27, 28, 29, 30, 30):
        schedule.append((day, "BHU-0788", "Suspected TB"))
    for _ in range(83):
        schedule.append((random.randint(1, 23), random.choice(BHUS)[0], None))
    for _ in range(48):
        schedule.append((random.randint(24, 30), random.choice(BHUS)[0], None))
    random.shuffle(schedule)
    return schedule


def _seed_dawa(patients_by_bhu: dict[str, list[str]], base_time: datetime) -> None:
    dawa_specs = [
        ("BHU-0442", "Augmentin", "Possible penicillin allergy", True),
        ("BHU-0511", "Brufen", "Use cautiously with gastritis", False),
        ("BHU-0623", "Ponstan", "Review dose and duration", False),
    ]
    for number, (bhu_id, medicine, warning, dismissed) in enumerate(dawa_specs, 1):
        dawa_id = f"DAWA-SYN-{number:04d}"
        timestamp = (base_time - timedelta(days=number, hours=number)).isoformat()
        _set_clock(timestamp)
        try:
            audit.create_case(
                dawa_id, patients_by_bhu[bhu_id][number - 1], PARAMEDICS[bhu_id], bhu_id
            )
            audit.log_event(
                dawa_id,
                "RX_EXTRACTED",
                {"medicines": [{"name": medicine, "dose": "1 tablet"}], "synthetic": True},
            )
            audit.log_event(
                dawa_id,
                "MEDICINE_FLAGGED",
                {
                    "grid": [{"medicine": medicine, "warning": warning}],
                    "light": "YELLOW",
                    "synthetic": True,
                },
            )
            if dismissed:
                audit.log_event(
                    dawa_id,
                    "FLAG_DISMISSED",
                    {
                        "medicine": medicine,
                        "reason": "Clinician confirmed monitored prior tolerance",
                        "synthetic": True,
                    },
                )
            audit.log_event(
                dawa_id, "DAWA_FINALIZED", {"status": "completed", "synthetic": True}
            )
            audit.update_case(dawa_id, outcome="dawa_check_completed", completed_at=timestamp)
        finally:
            _restore_clock()


def seed_district() -> None:
    random.seed(42)
    audit.init_db()
    conn = sqlite3.connect(audit.DB_PATH, check_same_thread=False)
    try:
        marker = conn.execute(
            "SELECT 1 FROM events WHERE event_type = 'SEED_MONTH_COMPLETE' LIMIT 1"
        ).fetchone()
        if marker:
            print("District data already seeded — skipping.")
            return
        patients_by_bhu = _seed_reference_data(conn)
    finally:
        conn.close()

    schedule = _case_schedule()
    red_cases = set(random.sample(range(len(schedule)), round(len(schedule) * 0.06)))
    hitl_plan = _hitl_plan(schedule)
    bhu_counts: Counter[str] = Counter()
    diagnosis_counts: Counter[str] = Counter()
    hitl_counts: dict[str, Counter[str]] = {bhu_id: Counter() for bhu_id, _, _ in BHUS}
    base_time = datetime.now(timezone.utc)

    for index, (day, bhu_id, forced_disease) in enumerate(schedule):
        case_id = f"CASE-SYN-{index + 1:04d}"
        patient_id = random.choice(patients_by_bhu[bhu_id])
        disease = forced_disease or _weighted_disease(exclude_tb=bhu_id == "BHU-0788")
        level, rule = TRIAGE[disease]
        if index in red_cases:
            level, rule, outcome = "RED", "emergency_danger_sign", "emergency_referral"
        else:
            outcome = "routine_referral" if level == "YELLOW" else "managed_locally"
        hitl_action = hitl_plan[bhu_id].pop()
        reason_text = {
            "approved": "Assessment matches observed presentation",
            "modified": "Clinician refined assessment after examination",
            "rejected": "Clinical findings did not support the suggested assessment",
        }[hitl_action]
        confidence = round(random.uniform(0.4, 0.9), 2)
        completed_ts = (
            base_time - timedelta(days=30 - day, hours=random.randint(0, 18), minutes=random.randint(0, 59))
        ).isoformat()
        _set_clock(completed_ts)
        try:
            audit.create_case(case_id, patient_id, PARAMEDICS[bhu_id], bhu_id)
            audit.log_event(
                case_id,
                "TRIAGE_DECISION",
                {"triage_level": level, "rules": [rule], "synthetic": True},
                provider="deterministic",
            )
            audit.log_event(
                case_id,
                "REASONING_RESULT",
                {
                    "ddx": [{"diagnosis": disease, "probability": "high"}],
                    "confidence": confidence,
                    "synthetic": True,
                },
                provider="stub",
                confidence=confidence,
            )
            audit.log_hitl_decision(
                case_id,
                gate_type="assessment",
                action=hitl_action,
                original={"assessment": disease, "synthetic": True},
                final={"assessment": disease, "synthetic": True},
                reason=reason_text,
                ai_confidence=confidence,
            )
            audit.update_case(
                case_id,
                completed_at=completed_ts,
                outcome=outcome,
                final_diagnosis=disease,
                hitl_outcome=hitl_action,
                triage_level=level,
            )
        finally:
            _restore_clock()
        bhu_counts[bhu_id] += 1
        diagnosis_counts[disease] += 1
        hitl_counts[bhu_id][hitl_action] += 1

    _seed_dawa(patients_by_bhu, base_time)
    audit.log_event(
        "SYSTEM",
        "SEED_MONTH_COMPLETE",
        {"cases": len(schedule), "dawa": 3, "synthetic": True},
    )

    print("=== District seed complete ===")
    print("Cases per BHU: " + ", ".join(f"{bhu_id}: {bhu_counts[bhu_id]}" for bhu_id, _, _ in BHUS))
    print("Cases per diagnosis: " + ", ".join(f"{name}: {diagnosis_counts[name]}" for name, _ in DISEASES))
    print(
        "HITL override rates: "
        + ", ".join(
            f"{bhu_id}: {(hitl_counts[bhu_id]['modified'] + hitl_counts[bhu_id]['rejected']) / bhu_counts[bhu_id]:.0%}"
            for bhu_id, _, _ in BHUS
        )
    )
    print(f"Total: {len(schedule)} consultation cases + 3 dawa cases")


if __name__ == "__main__":
    seed_district()
