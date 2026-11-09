# Doctor-selected product matching

The application does not supply a dose. A doctor can record an exact brand/generic, formulation, printed strength, route, their mg amount per dose, maximum whole dose count in any 24 hours, course length, indication and whether it is only as needed. The daily count is never an average across intermittent days; fractional daily counts are rejected. These fields persist in the reply draft, confirmed doctor-plan fact and reply audit. Editing the prescription, product, regimen or reviewed case clears acknowledgement. Actual patient instructions must be doctor-authored in the written prescription/schedule; they are not inferred from these checking fields.

`config/consultation_resources.json` keeps `dose_review.status` as `pending` and `entries` empty. No clinical ranges or reviewer approvals have been added. `dosing.py` provides the software contract for future reviewed content; editing metadata is not independent clinical sign-off or an authenticated approval process.

To enable a matched per-dose comparison after actual physician/pharmacist review, the table must be explicitly marked `reviewed`, and every entry must satisfy this contract:

| Field | Meaning |
|---|---|
| `id` | Unique versioned rule ID |
| `generic` | Exact single ingredient; combinations cannot borrow a single-ingredient rule |
| `formulation` | Specific selected form: tablet, capsule, syrup, injection, inhaler, cream or other |
| `strength` | Exact printed product strength, including units and denominator |
| `route` | Specific oral, inhaled, topical, intramuscular, intravenous or other route |
| `min_age_days`, `max_age_days` | Inclusive, ordered nonnegative integer age bounds |
| `min_mg_per_kg_per_dose`, `max_mg_per_kg_per_dose` | Finite ordered band, supplied and reviewed by clinicians; maximum must be positive |
| `max_mg_per_dose` | Optional positive absolute maximum for one dose |
| `max_mg_per_day`, `max_mg_per_kg_per_day` | Optional positive absolute/weight-based daily caps; the stricter supplied cap is used |
| `min_doses_per_day`, `max_doses_per_day` | Optional reviewed frequency band; both ordered bounds are required together |
| `min_duration_days`, `max_duration_days` | Optional reviewed course-length band; both ordered bounds are required together |
| `indication` | Optional exact doctor-selected indication required by this entry; clinical appropriateness is not inferred from symptoms |
| `reviewed_by`, `reviewed_on` | Actual reviewer and ISO calendar date, never a future date |
| `source` | Guideline/formulary version and specific reference supporting this product/age band |

Matching normalises letter case and spacing only. It does not infer strength from a brand, convert a volume/tablet count into mg, or treat different units, concentrations, formulations or routes as equivalent. Exactly one product/ingredient/route/age entry must match. Missing, malformed, overlapping or unreviewed entries leave the check unavailable or unmatched. A valid confirmed weight and a positive finite doctor-entered mg amount are required to compare the per-dose band. NaN, infinity, booleans and nonpositive amounts are rejected.

A result within a reviewed per-dose band is not approval of a prescription. The helper reports separate per-dose, absolute amount, daily maximum, frequency, duration and indication checks. It multiplies only the doctor's entered amount and daily count; no count or course is inferred. Missing inputs, absent reviewed limits and out-of-band entries remain explicit. A partial per-dose entry cannot produce a complete-regimen approval. As-needed selection does not bypass the entered maximum daily count or caps.

Organ function, clinical interpretation, combination products, multiple medicines and free-text schedule interpretation still require doctor verification. Allergy, interaction, duplicate ingredient, pregnancy and synthetic stock flags remain separate. No automatic comparison overrides those flags, supplies a patient dose or declares the prescription safe. The configured medicine/dose resource is loaded for the app process; reviewed content changes require a controlled reload/review.

`tests/test_consultation_dosing.py` uses invented isolated bounds and explicitly fictional reviewer/source markers only. Those fixtures are never written to the clinical table and must not be used as medical guidance.
