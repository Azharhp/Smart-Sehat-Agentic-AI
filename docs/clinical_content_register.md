# Clinical Content Register

Research prototype. Synthetic data only. No clinical sign-off has been obtained by this implementation.

| Item | Version | Reviewer | Date | Scope | Status |
|---|---|---|---|---|---|
| Universal/maternal danger signs | consultation-1 | Pending physician | Pending | Question wording and escalation | pending |
| Age bands, RR, SpO2, infant temperature | consultation-1 | Pending physician | Pending | National IMNCI version, units and cutoffs | pending |
| Chest indrawing classification | consultation-1 | Pending physician | Pending | RED is a conservative prototype choice; requires version-specific review | pending |
| CRB-65, TB, fever and MUAC rules | consultation-1 | Pending physician | Pending | Applicability, thresholds, missing data | pending |
| Question bank and Urdu wording | consultation-1 | Pending clinician/native speaker | Pending | Clinical pertinence and comprehension | pending |
| Brand map and interaction flags | consultation-1 | Pending pharmacist | Pending | Ingredients, formulations, regional brands | pending |
| Dose plausibility table | consultation-1 | Pending pharmacist/physician | Pending | Product/age/indication and explicit per-dose/absolute/daily/frequency/duration limit matching software exists; no reviewed entries or automatic dose approval | pending |
| Regional fever context | fever-context-1 | Pending clinical/public-health reviewer | Pending | Country/province/district, month/date windows, malaria/dengue/typhoid source statements; no reviewed entries or disease priors supplied | pending |
| Emergency instructions and patient guidance | consultation-1 | Pending physician | Pending | Local protocols and return precautions | pending |
| Typed POC/diarrhoea/course rules | consultation-1 | Pending physician | Pending | Units, plausibility, positive-test handling and escalation | pending |
| Consideration catalogue | consultation-1 | Pending physician | Pending | Eligible hypotheses, evidence/gaps, separating tests and reference scope | pending |
| Complete English/Urdu read-back and guidance | consultation-1 | Pending clinician/native speaker | Pending | All reported medicine fields, clarity, speech and comprehension | pending |
| Danger/measurement diagrams | consultation-1 | Pending physician/native speaker | Pending | Schematic orientation aids; not calibrated clinical images | pending |
| Fixed prompt recordings | wording/content hashes in review archive | Pending physician/native speaker | Pending | 66 English and 38/65 Urdu prompts prepared; all danger wording covered (18 English/17 Urdu unique); exact pronunciation and comprehension remain unreviewed | pending |
| Focused danger/Emergency Lane review | danger_screen / emergency_response / danger_audio hashes | Pending physician/native speaker | Pending | Dedicated ZIP includes exact wording, diagrams and all danger audio; zero actual matching reports at latest audit | pending |
| Interpreter regional read-back | exact packet/language/text hash | Named interpreter per encounter; no real review supplied | Pending | Full meaning/negations/units checked by the human; number/name guards are not equivalence proof | pending |
| Synthetic vignettes | consultation-1 | Pending physician | Pending | Authored software expectations, not clinical labels | pending |

Reference material checked during implementation:

- [WHO IMCI chart booklet (2014)](https://www.who.int/docs/default-source/mca-documents/imci-chart-booklet.pdf)
- [WHO management of the sick young infant up to two months (2019)](https://www.who.int/publications/i/item/9789241516365)
- [WHO oxygen therapy for children](https://www.who.int/publications/i/item/9789241549554)

These references do not establish local clinical approval. The young-infant guidance and chest-indrawing handling differ by guideline version and clinical context. Rules in this prototype follow the supplied plan pending review; do not represent them as a validated clinical protocol.

The app exports exact-content snapshots and hashes plus available public fixed audio/diagrams for review. `clinical-review-package.zip` is the current prepared archive. Imported real reports are recorded as reported reviews with unverified reviewer identity; they do not create independent validation or activate clinical tables. No actual physician/pharmacist/native-speaker approval has been obtained by this implementation.
