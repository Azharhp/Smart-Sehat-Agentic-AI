"""Generate a reproducible synthetic report and browser/Python parity check.

Run: python -m scripts.consultation_evidence
No model API calls. Node is needed only for the browser equivalence check.
"""
from __future__ import annotations
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

from smartsehat_workspace.consultation.clinical import Age, ROOT, config, evaluate
from smartsehat_workspace.consultation.store import Store
from smartsehat_workspace.consultation.verify import verify, fact_sentence
from smartsehat_workspace.consultation.exports import offline_capture
from tests.test_consultation import vignettes


def main() -> int:
    cases = vignettes()
    fixture_dir = ROOT / "tests_workspace/vignettes"
    fixture_dir.mkdir(parents=True, exist_ok=True)
    results = []
    for i, case in enumerate(cases, 1):
        (fixture_dir / f"{i:03}.json").write_text(json.dumps(case, ensure_ascii=False, indent=2), encoding="utf-8")
        age = Age.from_input(case["age"]["value"], case["age"]["unit"])
        actual = evaluate(case["facts"], age, case["pregnant"])
        passed = actual["level"] == case["expected_urgency"] and set(case["expected_rule_ids"]) <= {f["rule_id"] for f in actual["fired"]}
        results.append({"name": case["name"], "expected": case["expected_urgency"], "actual": actual["level"], "passed": passed, "result": actual})
    parity = "Not run: Node unavailable"
    parity_ok = True
    if shutil.which("node"):
        code = "const fs=require('fs');const {evaluateConsultation}=require(process.argv[1]);const x=JSON.parse(fs.readFileSync(0,'utf8'));process.stdout.write(JSON.stringify(x.cases.map(c=>evaluateConsultation(c.facts,c.age,c.pregnant,x.config))));"
        completed = subprocess.run(["node", "-e", code, str(ROOT / "smartsehat_workspace/consultation/browser_rules.js")], input=json.dumps({"cases": cases, "config": config()}), capture_output=True, text=True, check=True, encoding="utf-8")
        browser = json.loads(completed.stdout)
        mismatches = [r["name"] for r, b in zip(results, browser) if r["result"] != b]
        parity_ok = not mismatches
        parity = f"{len(cases)-len(mismatches)}/{len(cases)} exact results match" + (": " + ", ".join(mismatches) if mismatches else "")
    red = [r for r in results if r["expected"] == "RED"]
    recall = sum(r["actual"] == "RED" for r in red) / max(1, len(red))
    order = {"GREEN": 0, "AMBER": 1, "RED": 2}
    overtriage = sum(order[r["actual"]] > order[r["expected"]] for r in results) / len(results)
    fact = {"id": "F1", "key": "fever", "value": False, "unit": None, "confirmed": True}
    traps = ["fever = true [F1]", "fever = false C [F1]", "fever is present", "fever = false [F999]"]
    caught = sum(verify([claim], [fact])["status"] == "BLOCKED" for claim in traps)
    with tempfile.TemporaryDirectory() as tmp:
        store = Store(Path(tmp) / "report.db")
        cid = store.load_ayaan()
        packet = store.review(cid)
        packet_bytes = len(packet["text"].encode("utf-8"))
        cited = sum(bool(line["fact_ids"]) for line in packet["source_lines"])
        completeness = cited / max(1, len(packet["source_lines"]))
    docs = ROOT / "docs"
    docs.mkdir(exist_ok=True)
    lines = ["# Synthetic vignette test, not clinical validation", "", f"Generated: {datetime.now(timezone.utc).isoformat()}",
             "", f"- Boundary vignettes passed: {sum(r['passed'] for r in results)}/{len(results)}",
             f"- Red-flag recall against authored synthetic expectations: {recall:.1%} ({len(red)} expected RED cases)",
             f"- Over-triage against authored expectations: {overtriage:.1%}",
             f"- Canonical contradiction/unit/citation traps blocked: {caught}/{len(traps)}",
             f"- Browser/Python equivalence: {parity}",
             f"- Ayaan SBAR text size: {packet_bytes} bytes",
             f"- Ayaan source-tagged fact/rule lines with fact IDs: {completeness:.1%}",
             "- Completion time and doctor ratings: not measured.", "",
             "These figures describe only this authored test matrix. No independent clinical review, patient outcomes, clinical accuracy, or field language performance has been established.",
             "", "| Synthetic case | Expected | Actual | Pass |", "|---|---|---|---|"]
    lines += [f"| {r['name']} | {r['expected']} | {r['actual']} | {'yes' if r['passed'] else 'NO'} |" for r in results]
    (docs / "vignette_results.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (docs / "vignette_results.json").write_text(json.dumps({"synthetic": True, "clinical_validation": False, "cases": results, "parity": parity}, ensure_ascii=False, indent=2), encoding="utf-8")
    (ROOT / "assets/smartsehat-offline.html").write_text(offline_capture(), encoding="utf-8")
    print("\n".join(lines[2:14]))
    return 0 if all(r["passed"] for r in results) and parity_ok and caught == len(traps) else 1


if __name__ == "__main__":
    raise SystemExit(main())
