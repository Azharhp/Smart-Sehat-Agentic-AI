"""Offline Dawa regression entry point.

The old letter-category and flag-dismissal assertions were replaced by
source-backed shared-rule tests and workflow tests that preserve findings.
"""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parent

def _flatten(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from _flatten(item)
        else:
            yield item

def main() -> int:
    loader = unittest.TestLoader()
    rules = loader.discover(str(ROOT / "tests"), pattern="test_medication_rules.py")
    workflows = loader.discover(str(ROOT / "tests"), pattern="test_medication_workflows.py")
    selected = [test for test in _flatten(workflows)
                if ".test_dawa_" in test.id() or ".test_stale_dawa_" in test.id()]
    suite = unittest.TestSuite([rules, unittest.TestSuite(selected)])
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1

if __name__ == "__main__":
    raise SystemExit(main())
