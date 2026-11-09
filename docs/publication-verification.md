# Publication verification — 9 November 2026

Executed in the isolated publication copy using the existing Windows Python 3.14 environment:

- Existing upstream suite: 163 tests passed in 65.4 seconds.
- Workspace suite: 161 tests passed in 130.1 seconds after giving the Dawa fixture its own temporary database.
- Two additional provider-boundary tests passed: offline calls block before resolution; Qwen original ASR text remains unchanged.
- Legacy flow: 35/35 checks passed. Legacy medication workflow: 45 tests passed.
- Medication examples: 8/8 authored expectations matched.
- Three Node browser-component scripts passed.
- Credential-pattern scan found no matching files in the prepared source allowlist. Uploads, databases, keys, encrypted caches and local build outputs are excluded.

These are software regression results, not clinical validation. Clean dependency resolution and a new remote CI run require separate verification. Upstream safety modules/tests remain intact. The new workspace is a separate entry point; identity/access-control integration remains a roadmap item. No real-world outcomes or adoption are claimed.
