"""Phase 2 authentication, authorization, ownership, and migration acceptance tests."""
from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import seed
from smartsehat import access, audit
from smartsehat.auth import DemoAuthenticationProvider


class IdentityEncounterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = str(Path(self.temp.name) / "audit.db")
        self.db_patch = patch("smartsehat.audit.DB_PATH", self.db)
        self.db_patch.start()
        self.addCleanup(self.db_patch.stop)
        seed.seed(self.db)
        conn = audit._connect()
        try:
            conn.execute("INSERT INTO facilities VALUES(?,?,?)",
                         ("BHU-B", "Facility B", "Test District"))
            users = [
                ("WORKER-B", "Worker B", "health_worker", "BHU-B"),
                ("DOCTOR-A", "Doctor A", "doctor", "BHU-0442"),
                ("DHO-1", "District Viewer", "dho", "BHU-0442"),
            ]
            conn.executemany("INSERT INTO users VALUES(?,?,?,?)", users)
            conn.executemany("INSERT INTO user_facilities VALUES(?,?)",
                             [(u[0], u[3]) for u in users])
            conn.execute(
                "INSERT INTO patients(patient_id,name,age,sex,chronic_conditions_json,allergies_json,bhu_id) "
                "VALUES(?,?,?,?,?,?,?)",
                ("PT-B", "Patient B", 31, "F", json.dumps([]), json.dumps([]), "BHU-B"),
            )
            conn.commit()
        finally:
            conn.close()
        provider = DemoAuthenticationProvider()
        self.worker_a = provider.authenticate("PM-2213")
        self.worker_b = provider.authenticate("WORKER-B")
        self.doctor_a = provider.authenticate("DOCTOR-A")
        self.dho = provider.authenticate("DHO-1")

    def test_facility_authorization_is_enforced_below_the_ui(self):
        patient = access.get_patient(self.worker_a, "PT-0001")
        self.assertEqual(patient["bhu_id"], "BHU-0442")
        encounter = access.create_encounter(self.worker_a, "consultation", patient_id="PT-0001")
        self.assertEqual(access.get_encounter(self.worker_a, encounter.encounter_id).patient_id,
                         "PT-0001")
        for operation in (
            lambda: access.get_patient(self.worker_b, "PT-0001"),
            lambda: access.get_encounter(self.worker_b, encounter.encounter_id),
            lambda: access.record_resume(self.worker_b, encounter.encounter_id),
            lambda: access.authorize_mutation(self.worker_b, encounter.encounter_id),
        ):
            with self.subTest(operation=operation):
                with self.assertRaises(access.AuthorizationError):
                    operation()

    def test_displayed_worker_text_cannot_be_used_as_identity(self):
        encounter = access.create_encounter(self.worker_a, "consultation", patient_id="PT-0001")
        with self.assertRaises(access.AuthorizationError):
            access.get_encounter("PM-2213", encounter.encounter_id)  # type: ignore[arg-type]
        with self.assertRaises(access.AuthorizationError):
            access.get_encounter(self.worker_b, encounter.encounter_id)
        self.assertEqual(access.get_encounter(self.worker_a, encounter.encounter_id).actor_id,
                         self.worker_a.actor_id)

    def test_explicit_roles_keep_only_their_authorized_capabilities(self):
        encounter = access.create_encounter(self.worker_a, "consultation", patient_id="PT-0001")
        self.assertEqual(access.get_encounter(self.doctor_a, encounter.encounter_id).encounter_id,
                         encounter.encounter_id)
        access.authorize_mutation(self.doctor_a, encounter.encounter_id)
        access.authorize_district_view(self.dho)
        with self.assertRaises(access.AuthorizationError):
            access.authorize_district_view(self.worker_a)
        with self.assertRaises(access.AuthorizationError):
            access.get_encounter(self.dho, encounter.encounter_id)
        with self.assertRaises(access.AuthorizationError):
            access.create_encounter(self.doctor_a, "consultation", patient_id="PT-0001")

    def test_encounter_ownership_and_snapshot_versions_are_immutable(self):
        encounter = access.create_encounter(self.worker_a, "consultation", patient_id="PT-0001")
        updated = access.update_patient_snapshot(
            self.worker_a, encounter.encounter_id,
            {"age": 42, "weight_kg": 70, "sex": "M", "allergies": [],
             "current_meds": [], "vitals": None, "pregnant": True,
             "gestational_weeks": 20, "aspirin_prescribed": True,
             "renal_impairment": None,
             "liver_disease": None, "context_confirmed": True},
        )
        restored = access.get_encounter(self.worker_a, encounter.encounter_id)
        self.assertEqual((restored.patient_id, restored.facility_id, restored.actor_id),
                         ("PT-0001", "BHU-0442", "PM-2213"))
        self.assertEqual(updated.version, 2)
        self.assertEqual(restored.snapshot.patient.weight_kg, 70)
        self.assertTrue(restored.snapshot.patient.aspirin_prescribed)
        self.assertTrue(access.medication_context(restored.snapshot)["aspirin_prescribed"])
        conn = audit._connect()
        try:
            versions = conn.execute(
                "SELECT version FROM patient_context_snapshots WHERE case_id=? ORDER BY version",
                (encounter.encounter_id,),
            ).fetchall()
        finally:
            conn.close()
        self.assertEqual([row[0] for row in versions], [1, 2])

        access.mark_encounter_active(self.worker_a, encounter.encounter_id)
        access.mark_encounter_completed(self.worker_a, encounter.encounter_id)
        with self.assertRaises(access.AuthorizationError):
            access.update_patient_snapshot(self.worker_a, encounter.encounter_id,
                                           {"age": 99})
        with self.assertRaises(access.AuthorizationError):
            access.record_resume(self.worker_a, encounter.encounter_id)

    def test_walk_in_dawa_is_explicit_and_retrievable(self):
        encounter = access.create_encounter(
            self.worker_a, "dawa", walk_in_reference="Walk-in token 27"
        )
        restored = access.get_encounter(self.worker_a, encounter.encounter_id)
        self.assertIsNone(restored.patient_id)
        self.assertEqual(restored.identity_kind, "walk_in")
        self.assertEqual(restored.snapshot.patient.display_reference, "Walk-in token 27")
        self.assertEqual(restored.snapshot.patient.verification["identity"],
                         "walk_in_unregistered")

    def test_multifacility_actor_must_activate_the_patient_facility(self):
        conn = audit._connect()
        try:
            conn.execute("INSERT INTO user_facilities VALUES('PM-2213','BHU-B')")
            conn.commit()
        finally:
            conn.close()
        actor = DemoAuthenticationProvider().authenticate("PM-2213")
        self.assertEqual({row["patient_id"] for row in access.list_patients(actor)}, {"PT-0001"})
        self.assertEqual(access.get_patient(actor, "PT-B")["patient_id"], "PT-B")
        with self.assertRaises(access.AuthorizationError):
            access.create_encounter(actor, "consultation", patient_id="PT-B")
        actor_b = actor.with_active_facility("BHU-B")
        encounter = access.create_encounter(actor_b, "consultation", patient_id="PT-B")
        self.assertEqual(encounter.facility_id, "BHU-B")
        self.assertEqual(encounter.snapshot.patient.facility_id, "BHU-B")


class MigrationTests(unittest.TestCase):
    def test_legacy_copy_is_migrated_additively_and_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / "legacy-copy.db")
            conn = sqlite3.connect(path)
            conn.executescript(audit.SCHEMA)
            conn.execute("INSERT INTO facilities VALUES('BHU-L','Legacy BHU','Legacy District')")
            conn.execute("INSERT INTO users VALUES('U-L','Legacy User','paramedic','BHU-L')")
            conn.execute(
                "INSERT INTO patients VALUES('P-L','Legacy Patient',55,'F','[]','[]')"
            )
            conn.execute(
                "INSERT INTO patients VALUES('P-AMB','Ambiguous Patient',44,'M','[]','[]')"
            )
            conn.execute("INSERT INTO facilities VALUES('BHU-X','Other BHU','Other District')")
            conn.execute(
                "INSERT INTO cases VALUES('CASE-LEGACY','P-L','U-L','BHU-L','2025-01-01',"
                "NULL,'GREEN',NULL,NULL,NULL)"
            )
            conn.execute(
                "INSERT INTO cases VALUES('CASE-AMB-1','P-AMB','U-L','BHU-L','2025-01-02',"
                "NULL,'GREEN',NULL,NULL,NULL)"
            )
            conn.execute(
                "INSERT INTO cases VALUES('CASE-AMB-2','P-AMB','U-L','BHU-X','2025-01-03',"
                "NULL,'GREEN',NULL,NULL,NULL)"
            )
            conn.commit()
            conn.close()

            audit.init_db(path)
            conn = sqlite3.connect(path)
            try:
                row = conn.execute(
                    "SELECT patient_id,paramedic_id,bhu_id,workflow_type,actor_id,status,"
                    "patient_context_version FROM cases WHERE case_id='CASE-LEGACY'"
                ).fetchone()
                membership = conn.execute(
                    "SELECT 1 FROM user_facilities WHERE user_id='U-L' AND bhu_id='BHU-L'"
                ).fetchone()
                patient_facility = conn.execute(
                    "SELECT bhu_id FROM patients WHERE patient_id='P-L'"
                ).fetchone()[0]
                ambiguous_facility = conn.execute(
                    "SELECT bhu_id FROM patients WHERE patient_id='P-AMB'"
                ).fetchone()[0]
                media_table = conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='media_artifacts'"
                ).fetchone()
                version = conn.execute("PRAGMA user_version").fetchone()[0]
            finally:
                conn.close()
            self.assertEqual(row, ("P-L", "U-L", "BHU-L", "consultation", "U-L",
                                   "legacy_in_progress", 0))
            self.assertTrue(membership)
            self.assertEqual(patient_facility, "BHU-L")
            self.assertIsNone(ambiguous_facility)
            self.assertTrue(media_table)
            self.assertEqual(version, 1)


if __name__ == "__main__":
    unittest.main()
