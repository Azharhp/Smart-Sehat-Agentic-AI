# Roadmap

This is a maintenance plan, not a promise of deployment readiness or a dated delivery commitment.

0. **Workspace integration:** consolidate the two entry points only after identity, authorization, media and medication contracts have parity tests.
1. **Reproducible builds and CI:** validate dependency availability on clean Python environments; keep offline workflows independent of provider credentials; add dependency auditing with reviewed findings.
2. **Accessibility:** keyboard/screen-reader review, focus management and clear bilingual labels; test with health workers and native speakers before usability claims.
3. **Security boundaries:** private reporting channel, threat model, authentication/authorization design and export/backup handling before any real-data deployment.
4. **Clinical and language review:** independent source/version/redistribution audit, physician/pharmacist review and dialect-specific evaluation. Dosing and regional context tables remain pending.
5. **Adapter contracts:** synthetic delivery failure/retry/idempotency tests; activate external services only with partner credentials and authorization.
6. **Reusable infrastructure:** assess extraction of audit/task orchestration into a separately testable nonclinical Python package after interfaces stabilize.

Track concrete work with issues containing acceptance criteria and genuine results. Do not represent authored synthetic tests as external usage or clinical outcomes.

## Tracked work

- [Workspace security integration](https://github.com/Azharhp/Smart-Sehat-Agentic-AI/issues/2)
- [Clean installation and dependencies](https://github.com/Azharhp/Smart-Sehat-Agentic-AI/issues/3)
- [Keyboard and bilingual accessibility](https://github.com/Azharhp/Smart-Sehat-Agentic-AI/issues/4)
- [Clinical corpus and fixture rights audit](https://github.com/Azharhp/Smart-Sehat-Agentic-AI/issues/5)
