# v0.1.0-alpha

Research prototype for synthetic-case exploration of auditable remote consultation. Includes deterministic screening, worker-confirmed facts, doctor review/tasks, SBAR, Dawa image review and an aggregate district dashboard.

See CHANGELOG.md for scope, README.md for setup and offline checks, and THIRD_PARTY_NOTICES.md for outstanding redistribution review. This is not clinically validated, has no approved dosing catalogue, and is not suitable for real patient deployment. External delivery and clinician identity remain simulated by default. Regional speech is experimental. No adoption or patient outcomes are claimed.

Owner-authorized Apache-2.0 applies to original code only. Uploads, databases, encryption keys and runtime caches are excluded. GitHub Actions passed clean Python 3.12 installation, 326 Python tests across both application suites, legacy flow/medication checks and three Node browser-component checks. These are software checks, not clinical validation.

The completed consultation entry point is `smartsehat_workspace/app.py`. The existing `smartsehat/app.py` and its safety implementation remain preserved; cross-workspace authentication and access-control consolidation is tracked in issue #2.
