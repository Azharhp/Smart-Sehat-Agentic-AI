# Contributing

Start with the README installation steps and use fictional cases only. Python 3.12+ and Node 20+ are used for offline checks. Do not send patient records, credentials, private recordings or encryption keys in issues, pull requests or test fixtures.

## Development and checks

Create a branch from the current default branch. Set `SMARTSEHAT_OFFLINE=1`, `SMARTSEHAT_MODE=stub`, `DOTENV_DISABLED=1` and `CONSULTATION_DISABLE_SCHEDULER=1` in your test environment. Run both `tests` and `tests_workspace` suites plus the README Python and Node commands. The original application remains under `smartsehat`; the completed consultation build is isolated under `smartsehat_workspace`. See docs/workspace-integration.md before merging their interfaces. Run legacy script tests in separate processes because they configure isolated databases at import time. Live provider tests are opt-in and may spend credits; never make them mandatory CI checks.

Use small pull requests describing the concrete before/after behavior, relevant test results, and remaining limitations. Add regression tests for meaningful workflow or integrity changes. Preserve append-only audit records, stale-version guards, explicit confirmation, unknown/missing reasons and emergency escalation. Never silently turn AI output into a confirmed fact.

## Clinical and language changes

A passing test is not clinical approval. Cite the exact source/version, identify redistribution rights, update the content register, and retain pending-review status until qualified independent review exists. Do not add automatic prescribing or claim dose validation from an unreviewed table. Translation and speech changes require native-speaker and clinical review before accuracy claims.

Useful contributions include reproducible offline fixtures, accessibility and keyboard operation, setup reproducibility, dependency review, privacy boundaries and adapter contract tests. Consult ROADMAP.md before starting large architectural work. Maintainers review changes; avoid fabricated usage, reviews, endorsements or validation results.
