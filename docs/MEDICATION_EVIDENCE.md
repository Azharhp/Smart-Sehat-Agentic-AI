# SmartSehat medication review: evidence and demonstration scope

Evidence checked: **2026-09-06**. Intended use: a hackathon demonstration of shared, deterministic medication review in Consultation and Dawa Check, with a human responsible for every final decision.

**Clinical sign-off is pending.** This document records the sources and reasoning behind a deliberately limited rule pack. A passing software test establishes that code follows a specified example; it does not establish clinical accuracy, prescribing authority, clinical effectiveness, comprehensive interaction coverage or safety in real patients. Source-backed rules and a human acknowledgment do not make the application clinically validated.

## What the demonstration can establish

The defensible claim is: **the same confirmed medicine list and patient context produce the same review findings in both workflows; edits trigger a new review; findings identify their source and limitations; unresolved data remains visible; and the human decision is recorded.** Use synthetic cases and the actual test output to demonstrate these properties.

This document defines evidence-backed scope and acceptance criteria. The implementation and its test report determine which criteria currently pass. It is not a claim that every criterion below has already been implemented.

## Evidence register

Each entry is a concise paraphrase of the identified source. UK/US references support the stated clinical facts; they do not establish Pakistani regulatory approval, local prescribing authority or the correctness of every local brand formulation. Re-check a product's current local label and clinical context before clinical adoption.

### E01 — Adult oral paracetamol dose limits and exceptions

**Source:** [NHS: Paracetamol for adults](https://www.nhs.uk/medicines/paracetamol-for-adults/), page reviewed 24 March 2026. Exact sections: **How to take paracetamol and how much to take (dose)**, **500mg or 1g tablets and capsules**, and **Who can and cannot take paracetamol**.

For adults aged 18 or older, the ordinary tablet regimen permits 500–1000 mg per dose, at least four hours apart, up to 4000 mg in 24 hours. Weight below 50 kg may require a lower dose; liver/kidney conditions and alcohol dependence/binge drinking require suitability review. These limits are not a personalized prescription or an automatic dose for children, low-weight adults, organ impairment or uncertain formulations. Unknown dose, frequency, weight or relevant risk factors must remain unassessed; do not infer daily exposure from package strength alone.

### E02 — Duplicate active ingredients, including combination products

**Source:** [FDA: Acetaminophen](https://www.fda.gov/drugs/safe-use-over-counter-pain-relievers-and-fever-reducers/acetaminophen), exact sections **Safe Use of Acetaminophen** and **Acetaminophen use in infants and children**.

Paracetamol is the name commonly used outside the US for acetaminophen. It occurs alone and in combination products. FDA advises against using multiple acetaminophen-containing products together and emphasizes correct dose and interval. The review therefore needs ingredient-level reconciliation across existing and proposed medicines, including different brands. A duplicate flag is a prompt to reconcile the regimen, not evidence that two separate database records necessarily represent two doses actually taken. Confirm product ingredients and whether an item is being continued, replaced or stopped.

### E03 — Systemic NSAIDs in pregnancy and the aspirin exception

**Source:** [FDA Drug Safety Communication: NSAIDs at 20 weeks or later](https://www.fda.gov/drugs/drug-safety-and-availability/fda-recommends-avoiding-use-nsaids-pregnancy-20-weeks-or-later-because-they-can-result-low-amniotic), issued 15 October 2020, updates noted through 1 September 2022. Exact section: **Additional Information for Health Care Professionals**.

From approximately 20 weeks, systemic NSAIDs can cause fetal renal dysfunction and low amniotic fluid. Between 20 and 30 weeks, use only when clinically necessary at the lowest effective dose and shortest duration; monitoring may be needed beyond 48 hours. At 30 weeks or later, avoidance also addresses premature ductus arteriosus closure. The communication expressly exempts clinician-directed **81 mg aspirin** for certain pregnancy conditions and excludes ophthalmic NSAIDs. Unknown gestational age/dose/route needs review; the exception must not be generalized to every aspirin dose. Earlier gestation does not mean the medicine has been assessed as safe.

### E04 — Amoxicillin and serious beta-lactam hypersensitivity

**Source:** [DailyMed: Amoxicillin capsule, Rising Health](https://dailymed.nlm.nih.gov/dailymed/lookup.cfm?setid=d6a33b28-f520-4a4c-b553-0ebd00558345), displayed prescribing information revised February 2024. Exact sections: **4 Contraindications** and **5.1 Anaphylactic Reactions**.

A history of serious hypersensitivity, such as anaphylaxis or Stevens–Johnson syndrome, to amoxicillin or another beta-lactam is a labeled contraindication. Previous reactions should be investigated before therapy. An unspecified allergy entry should trigger clarification and review; the application must not diagnose anaphylaxis from a generic allergy string or treat intolerance as a confirmed serious allergy. Amoxicillin-containing combinations also require ingredient reconciliation. This entry supports the allergy check, not dosing for all infections or a complete cross-reactivity algorithm.

### E05 — Rifampicin and systemic hormonal contraception

**Source:** [DailyMed: Rifampin capsule, Lupin, version 26](https://dailymed.nlm.nih.gov/dailymed/lookup.cfm?setid=b56004f0-f23d-47f6-80c7-39884fccbccc&version=26), label updated 11 February 2026. Exact section: **Precautions → Drug Interactions → Effect of rifampin on other drugs → Table 1**, row **Systemic Hormonal Contraceptives**; also **Information for Patients**.

Rifampin/rifampicin reduces exposure to systemic estrogens/progestins and can reduce contraceptive reliability. The label advises nonhormonal contraception during therapy. Flag the combination for clinician/pharmacist counseling and a documented contraceptive plan; do not automatically stop essential rifampicin. The source supports an interaction-management warning, not a blanket contraindication to the pair. A generic contraception entry needs method clarification; not every contraceptive method is systemic hormonal treatment.

### E06 — Warfarin with aspirin or NSAIDs

**Source:** [DailyMed: Warfarin sodium, Amneal, version 7](https://dailymed.nlm.nih.gov/dailymed/lookup.cfm?setid=51e98fb6-ba76-497e-95d8-fe895ef0b7ed&version=7), prescribing information revised June 2026. Exact section: **7.3 Drugs that Increase Bleeding Risk**, **Table 3**.

The label identifies aspirin and NSAIDs, including ibuprofen, as medicines that increase bleeding risk with warfarin and calls for close monitoring. This is a high-priority medication review, not a statement that every clinician-directed combination is absolutely contraindicated. Evaluate both existing and proposed lists independent of ordering. Document the indication, bleeding-risk assessment and management plan; the rule pack does not calculate an individualized INR target, adjust anticoagulant doses or certify adequate monitoring.

### E07 — Trimethoprim–sulfamethoxazole with ACE inhibitors

**Source:** [DailyMed: Bactrim/Bactrim DS, Sun Pharmaceutical](https://dailymed.nlm.nih.gov/dailymed/drugInfo.cfm?setid=f59d0c04-9c66-4d53-a0e1-cb55570deb62), label updated 30 December 2024. Exact sections: **Precautions → Drug Interactions**, table row **Angiotensin Converting Enzyme Inhibitors**; **Precautions → Electrolyte Abnormalities → Hyperkalemia**; and **Geriatric Use**.

Trimethoprim can raise potassium, particularly with renal insufficiency, potassium-related disorders or other potassium-raising drugs. The interaction table advises avoiding concurrent ACE-inhibitor use; the precautions call for potassium monitoring in at-risk patients. This supports an avoid-concurrent-use/urgent-review finding with renal function, potassium and medicines reconciliation. It is not the same label category as an absolute contraindication. The software must not invent potassium values, normal renal function or an alternative antibiotic.

### E08 — Antibiotic selection must follow an indication and current guidance

**Source:** [WHO: The AWaRe antibiotic book](https://www.who.int/publications/i/item/9789240062382), published 9 December 2022. Exact sections on the official page: **Overview** and its table of subsequently published WHO guidelines; book chapter **Improving the use of antibiotics with the AWaRe book**.

WHO organizes antibiotic choice, dose, route and duration by infection and clinical setting. The official page notes newer guidance for several infections and an ongoing update of the book. A generic antibiotic-review prompt should request the indication and applicable current guidance; class membership alone does not establish appropriateness, dose or a universal course length. Do not label every Watch antibiotic as forbidden or assume all antibiotics require laboratory confirmation before empiric use.

### E09 — Pakistan-specific azithromycin stewardship

**Source:** [NIH Pakistan: Public Health Bulletin, Week 22 (2024)](https://www.nih.org.pk/wp-content/uploads/2024/06/Weekly%20Report-22-2024.pdf#page=19), advisory **F.1-22/Advisory/CDC/2024**, dated **22 May 2024**, **Advisory for Prevention and Treatment of Typhoid Fever including XDR Typhoid**. Exact location: physical PDF page **19 of 20**, **Treatment** section. The official PDF text was inspected directly.

The advisory urges culture/sensitivity-guided treatment and discourages unnecessary antibiotics for fever. It restricts azithromycin/meropenem for XDR typhoid under a registered medical practitioner and also recognizes **other serious medical conditions** when discussing reserved oral azithromycin use. A keyword such as `typhoid`, `XDR`, or a negated indication is insufficient to establish the exception. Show a stewardship-review finding and request indication, evidence and prescriber review; do not turn the advisory into a universal contraindication outside confirmed XDR typhoid. This is a dated advisory, not proof that later resistance patterns or local policy are unchanged.

## Rule scope and decision semantics

The colors and blocking behavior are **application policy**. They should not imply that every warning is a pharmacological contraindication. A blocker means the workflow needs human resolution; it must never silently discontinue an existing medicine.

| Review area | Intended finite scope | Required distinction | Evidence |
|---|---|---|---|
| Ordinary adult oral paracetamol limits | Explicit ingredient, unit, dose and administration frequency; appropriate adult context | Above-limit finding versus insufficient information or patient-specific dose review | E01 |
| Duplicate ingredients | Confirmed ingredients across proposed and existing products | Duplicate exposure versus replacement/reconciliation ambiguity | E02 |
| Pregnancy and systemic NSAIDs | Known pregnancy, gestational age, dose, route and clinician-directed exception | Timing-specific warning versus unknown context; never category-letter guessing | E03 |
| Amoxicillin allergy | Confirmed ingredient and recorded reaction history | Labeled serious hypersensitivity contraindication versus unclarified allergy | E04 |
| Rifampicin/contraception | Supported systemic hormonal methods and both medication-list directions | Counseling/management warning; preserve essential-treatment decision | E05 |
| Warfarin/aspirin/NSAIDs | Selected verified ingredients, including existing therapy | Bleeding-risk review rather than universal prohibition | E06 |
| TMP-SMX/ACE inhibitors | Supported combination ingredients and ACE-inhibitor aliases | Avoid-concurrent-use warning; no assumed normal laboratory results | E07 |
| Antibiotic stewardship | Supported antibiotics with recorded indication and planned regimen | Appropriateness review, not automated diagnosis/prescribing | E08, E09 |
| Anything outside these checks | Unknown medicines, ambiguous OCR, unsupported dose formats or missing context | **Unassessed**, visibly separate from **no flags found in supported checks** | Scope limit |

`No flags found` means only that the implemented checks did not flag the supplied inputs. It never means `safe`, `clinically approved`, `all interactions checked`, `dose verified` when not assessed, or `ready to dispense` without the required review. Recording an acknowledgment must not rewrite the original rule result as GREEN.

## Demonstration acceptance record

Record the actual command, date, rule-pack version, synthetic input, expected finding, observed finding and pass/fail outcome. The following cases are the minimum meaningful examples; do not fill in a pass until executed against the final code.

1. Consultation and Dawa receive equivalent normalized inputs and produce identical findings/source IDs.
2. A human prescription edit invalidates the earlier review and reruns the shared check; an old acknowledgment cannot approve new content.
3. Ingredient overlap appears across brands and combination products, including current medicines; list order does not change interaction findings.
4. Supported ordinary adult dose boundaries are tested immediately below, at and above the limit; missing frequency, pediatric/low-weight context and organ-risk context remain appropriately unassessed or under review.
5. Pregnancy boundary and exception cases use structured context; an uncertain week or unverified exception cannot become an unqualified pass.
6. Unknown reaction severity, uncertain OCR, unknown medication, empty extraction, malformed dose and an absent patient field remain visible as limitations.
7. Every generated finding links to the relevant evidence entry and precise section, not merely any existing corpus ID.
8. Finalization rejects a missing/stale required acknowledgment and records the human action and exact reviewed snapshot; original findings stay available.

These are software and workflow tests. Separately report clinical review, case adjudication and user evaluation as **pending** until actually conducted. Do not infer sensitivity, specificity, avoided harm or patient benefit from synthetic tests.

## Before clinical deployment

Assign a licensed clinical/pharmacy reviewer for each rule and local formulary mapping; record reviewer identity, review date, jurisdiction, decision and next review date. Validate product ingredients and source versions, resolve guideline conflicts, and maintain an auditable change history. Obtain independent case review for supported populations, define escalation responsibilities, evaluate usability with intended workers, and complete appropriate local governance and regulatory assessment.

Until then, present the feature as **a limited, source-backed medication review prototype with mandatory human review**. The strongest hackathon evidence is a transparent, reproducible process with honest limits—not a claim that software tests constitute medical proof.
