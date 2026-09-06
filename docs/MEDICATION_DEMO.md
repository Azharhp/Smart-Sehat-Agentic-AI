# Medication review hackathon demonstration

Use only synthetic patient data. Start the app with `SMARTSEHAT_OFFLINE=1` and `DOTENV_DISABLED=1`, or run `python demo_medication_review.py` to generate `audit-output/medication-demo-results.json` without a model, database or network call.

Demonstrate these steps:

1. In Consultation, submit a structured paracetamol plan with administered dose, daily frequency, duration, route and indication. Show the ruleset version, input hash, evidence link, covered checks and unassessed checks.
2. Change the dose to 1500 mg per administration. Show that the shared engine produces HOLD and the backend refuses approval.
3. Correct the dose and submit it. Show that the review hash changes and the new receipt is required.
4. Add a recorded penicillin allergy and propose amoxicillin. Show that the context change triggers a fresh blocking review.
5. Enter the same confirmed medicine/context in Dawa Check. Show that the finding code, evidence source and ruleset version match Consultation.
6. Record a finding-review note. Show that the warning remains present; it is never dismissed or changed to green.
7. Trigger each RED synthetic triage example. Show the actual rule in the referral packet, `UNSUPPORTED_EMERGENCY_PROTOCOL`, and an empty medicine-order list.
8. Download the JSON evidence receipt and show the ledger's actor, action, review hash and source links.

The defensible presentation is: “SmartSehat has a reproducible, bounded, source-linked medication review prototype with mandatory human reconciliation.” Do not present software test results as clinical validation. Say that licensed clinical/pharmacy sign-off, local formulary review and real-world evaluation are pending.
