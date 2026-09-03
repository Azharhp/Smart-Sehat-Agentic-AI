"""Headless tests for the Dawa Check workflow — Day 3 (offline).

All vision calls are monkeypatched so ZERO API calls are made.
"""
from __future__ import annotations
import json
import os
import sys
import tempfile

os.environ["SMARTSEHAT_DB"] = os.path.join(
    tempfile.mkdtemp(prefix="smartsehat_dawa_"), "smartsehat.db"
)

from smartsehat import audit                       # noqa: E402
from smartsehat import dawa                        # noqa: E402
from smartsehat import dawa_graph                  # noqa: E402
import seed                                        # noqa: E402


RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    RESULTS.append((name, cond, detail))
    print(f"[{'PASS' if cond else 'FAIL'}] {name}{(' — ' + detail) if detail else ''}")


def _row(grid: list[dict], name: str) -> dict | None:
    for r in grid:
        if r.get("name") == name:
            return r
    return None


def _flag_codes(row: dict | None) -> set[str]:
    if not row:
        return set()
    return {f.get("code") for f in row.get("flags") or []}


# ----------------------------------------------------------------------
# Scenario A — PREGNANT: 6-medicine bag
# ----------------------------------------------------------------------

def scenario_pregnant() -> None:
    meds = [
        {"name": "Panadol", "strength": "500mg"},
        {"name": "Brufen", "strength": "400mg"},
        {"name": "Nise", "strength": "100mg"},
        {"name": "Augmentin", "strength": "625mg"},
        {"name": "Risek", "strength": "20mg"},
        {"name": "multivitamin", "strength": None},
    ]
    grid = dawa.check_medicines(meds, {"pregnant": True})

    panadol = _row(grid, "Panadol")
    brufen = _row(grid, "Brufen")
    nise = _row(grid, "Nise")
    augmentin = _row(grid, "Augmentin")
    risek = _row(grid, "Risek")
    multi = _row(grid, "multivitamin")

    check("A: Panadol GREEN", panadol and panadol["light"] == "GREEN",
          f"got={panadol.get('light') if panadol else '?'}")
    check("A: Brufen RED (pregnancy D + duplicate NSAID class)",
          brufen and brufen["light"] == "RED"
          and "contraindicated_pregnancy" in _flag_codes(brufen)
          and "duplicate_class" in _flag_codes(brufen),
          f"flags={_flag_codes(brufen)}")
    check("A: Nise RED (duplicate NSAID class escalated by pregnancy peer)",
          nise and nise["light"] == "RED"
          and "duplicate_class" in _flag_codes(nise),
          f"flags={_flag_codes(nise)} light={nise.get('light') if nise else '?'}")
    check("A: Augmentin YELLOW (antibiotic_review)",
          augmentin and augmentin["light"] == "YELLOW"
          and "antibiotic_review" in _flag_codes(augmentin),
          f"flags={_flag_codes(augmentin)}")
    check("A: Risek GREEN (bare category C is not a flag)",
          risek and risek["light"] == "GREEN",
          f"flags={_flag_codes(risek)} light={risek.get('light') if risek else '?'}")
    check("A: multivitamin YELLOW (unrecognized_review)",
          multi and multi["light"] == "YELLOW"
          and "unrecognized_review" in _flag_codes(multi),
          f"flags={_flag_codes(multi)}")


# ----------------------------------------------------------------------
# Scenario B — DUPLICATE GENERIC: Panadol + Calpol
# ----------------------------------------------------------------------

def scenario_duplicate_generic() -> None:
    grid = dawa.check_medicines(
        [{"name": "Panadol", "strength": "500mg"},
         {"name": "Calpol", "strength": "500mg"}],
        {"pregnant": False},
    )
    p = _row(grid, "Panadol")
    c = _row(grid, "Calpol")
    check("B: Panadol RED duplicate_generic",
          p and p["light"] == "RED" and "duplicate_generic" in _flag_codes(p),
          f"flags={_flag_codes(p)}")
    check("B: Calpol RED duplicate_generic",
          c and c["light"] == "RED" and "duplicate_generic" in _flag_codes(c),
          f"flags={_flag_codes(c)}")


# ----------------------------------------------------------------------
# Scenario C — Non-pregnant, same 6-medicine bag
# ----------------------------------------------------------------------

def scenario_non_pregnant() -> None:
    meds = [
        {"name": "Panadol", "strength": "500mg"},
        {"name": "Brufen", "strength": "400mg"},
        {"name": "Nise", "strength": "100mg"},
        {"name": "Augmentin", "strength": "625mg"},
        {"name": "Risek", "strength": "20mg"},
        {"name": "multivitamin", "strength": None},
    ]
    grid = dawa.check_medicines(meds, {"pregnant": False})
    brufen = _row(grid, "Brufen")
    nise = _row(grid, "Nise")
    check("C: no medicine has contraindicated_pregnancy",
          all("contraindicated_pregnancy" not in _flag_codes(r) for r in grid),
          str([(r["name"], _flag_codes(r)) for r in grid]))
    check("C: Brufen YELLOW duplicate_class",
          brufen and brufen["light"] == "YELLOW"
          and "duplicate_class" in _flag_codes(brufen),
          f"flags={_flag_codes(brufen)} light={brufen.get('light') if brufen else '?'}")
    check("C: Nise YELLOW duplicate_class",
          nise and nise["light"] == "YELLOW"
          and "duplicate_class" in _flag_codes(nise),
          f"flags={_flag_codes(nise)} light={nise.get('light') if nise else '?'}")


# ----------------------------------------------------------------------
# Scenario D — DDI: rifampicin + oral contraceptive in current_meds
# ----------------------------------------------------------------------

def scenario_ddi_rifampicin_ocp() -> None:
    grid = dawa.check_medicines(
        [{"name": "rifampicin", "strength": "600mg"}],
        {"pregnant": False, "current_meds": ["oral contraceptive"]},
    )
    r = _row(grid, "rifampicin")
    codes = _flag_codes(r)
    cits = [f.get("citation") for f in (r.get("flags") if r else []) or []]
    check("D: rifampicin RED with ddi_rifampicin_ocp flag",
          r and r["light"] == "RED" and "ddi_rifampicin_ocp" in codes,
          f"flags={codes}")
    check("D: DDI flag carries NEML rifampicin citation",
          any(c and "NEML" in c for c in cits),
          f"citations={cits}")


# ----------------------------------------------------------------------
# Scenario E — Full graph flow with monkeypatched vision
# ----------------------------------------------------------------------

CANNED_VISION_JSON = json.dumps({
    "medicines": [
        {"name": "Panadol", "strength": "500mg"},
        {"name": "Brufen", "strength": "400mg"},
        {"name": "Nise", "strength": "100mg"},
        {"name": "Augmentin", "strength": "625mg"},
        {"name": "Risek", "strength": "20mg"},
        {"name": "multivitamin", "strength": None},
    ]
})


def scenario_graph_flow() -> None:
    # Monkeypatch _router() so no real API call is ever made.
    class _FakeRouter:
        def route_vision(self, role, image, prompt):
            return CANNED_VISION_JSON, "test-fake"
    dawa_graph._router = lambda: _FakeRouter()  # type: ignore[assignment]

    ckpt = os.path.join(os.path.dirname(os.environ["SMARTSEHAT_DB"]), "dawa_ckpt.db")
    case_id, thread_id, first = dawa_graph.run_dawa_case(
        rx_image_path="/tmp/nonexistent.jpg",
        paramedic_id="PM-2213",
        checkpoint_db=ckpt,
    )
    check("E: extract reached confirm gate",
          "__interrupt__" in first,
          f"result keys: {list(first.keys())}")

    # Confirm — pregnant patient
    r2 = dawa_graph.resume_dawa_case(
        thread_id,
        checkpoint_db=ckpt,
        medicines=[m for m in json.loads(CANNED_VISION_JSON)["medicines"]],
        patient={"pregnant": True, "age": 30, "current_meds": []},
    )
    check("E: confirm advanced to dawa_hitl gate",
          "__interrupt__" in r2,
          f"result keys: {list(r2.keys())}")

    # Dismiss one flag — dismiss Augmentin's antibiotic_review
    r3 = dawa_graph.resume_dawa_case(
        thread_id, checkpoint_db=ckpt,
        action="dismiss", medicine="Augmentin",
        flag_code="antibiotic_review",
        reason="Confirmed indication: dental abscess, 5-day course",
    )
    check("E: after dismiss, graph re-interrupts at dawa_hitl",
          "__interrupt__" in r3)

    # Finalize
    r4 = dawa_graph.resume_dawa_case(thread_id, checkpoint_db=ckpt,
                                     action="finalize")
    check("E: finalize completes graph",
          "__interrupt__" not in r4)

    # Ledger order
    types = [e["event_type"] for e in audit.get_case_timeline(case_id)]
    for name in ("RX_EXTRACT_OK", "RX_EXTRACTED", "MEDICINE_FLAGGED",
                 "FLAG_DISMISSED", "DAWA_FINALIZED"):
        check(f"E: ledger has {name}", name in types, str(types))
    order_ok = (types.index("RX_EXTRACTED") < types.index("MEDICINE_FLAGGED")
                < types.index("FLAG_DISMISSED") < types.index("DAWA_FINALIZED"))
    check("E: ledger order RX_EXTRACTED→MEDICINE_FLAGGED→FLAG_DISMISSED→DAWA_FINALIZED",
          order_ok, str(types))

    row = audit.get_case(case_id)
    check("E: case row outcome is dawa_check_completed",
          bool(row) and row.get("outcome") == "dawa_check_completed",
          str(row.get("outcome") if row else None))


def main() -> int:
    seed.seed()
    print(f"DB at: {os.environ['SMARTSEHAT_DB']}")
    scenario_pregnant()
    scenario_duplicate_generic()
    scenario_non_pregnant()
    scenario_ddi_rifampicin_ocp()
    scenario_graph_flow()
    fails = [n for n, ok, _ in RESULTS if not ok]
    print("\n===== SUMMARY =====")
    print(f"Total: {len(RESULTS)}  Passed: {len(RESULTS) - len(fails)}  Failed: {len(fails)}")
    if fails:
        for n in fails:
            print(f"  FAILED: {n}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
