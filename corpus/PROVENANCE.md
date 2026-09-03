# SmartSehat Clinical Corpus — Provenance & Audit Trail

Every chunk in `chunks.json` is traceable to a published source. Verbatim
text was copied character-for-character from the published PDF; no
paraphrasing, no summarization, no LLM rewriting. Where a Pakistani source
could not be retrieved from the retrieval sandbox, the WHO equivalent was
used as an honest fallback — those placeholders have since been closed
with verbatim text from the actual documents (see the update section at
the bottom).

Initial retrieval date: **2026-09-01** (sandbox time, America/Los_Angeles).
Tools used: `curl`, `pdftotext` (poppler), and the platform's crawler
proxy. Pakistani documents were subsequently retrieved via Codex with the
source PDFs archived in `corpus/sources/`.

---

## Retrieval attempt summary (initial sandbox run)

| Domain                              | Reachable from sandbox | Notes |
|-------------------------------------|------------------------|-------|
| `iris.who.int` (bitstreams)         | ✅ (via `curl`)         | PDFs downloaded and text-extracted |
| `www.ncbi.nlm.nih.gov/books/*`      | ✅                      | HTML text |
| `tbksp.who.int` (TB Knowledge Sharing Platform) | ✅          | HTML text |
| `www.dra.gov.pk` (Pakistan DRA)     | ❌ Not reachable        | HTTP 000 / DNS + TLS failure — closed later via manual retrieval |
| `ntp.gov.pk` (Pakistan NTP)         | ⚠️ Landing page only    | Closed later via manual retrieval |

---

## Chunk-by-chunk provenance

### WHO chunks (10) — retrieved verbatim from WHO sources

**`WHO_TB_M2_4symptom_screen`**
* Document: WHO consolidated guidelines on tuberculosis. Module 2:
  Screening — systematic screening for tuberculosis disease (2021),
  Executive Summary, Recommendation 11, p.xxii.
* Retrieval URL (public mirror):
  `https://phc.org.ua/sites/default/files/users/user90/WHO_Module_2_Guidelines_eng.pdf`
* Method: `curl` → `pdftotext` → verbatim extract of lines 613–617.
* Status: ✅ verbatim.

**`WHO_TB_M2_general_pop_screen`**
* Same document, Executive Summary, Recommendation 1, p.xxi.
* Method: `pdftotext` → lines 540–542. Status: ✅ verbatim.

**`WHO_TB_M2_child_contact_screen`**
* Same document, Section 3.4, Recommendation 16.
* Method: `pdftotext` → lines 2523–2527. Status: ✅ verbatim.

**`WHO_TB_M4_DSTB_regimen`**
* Document: WHO TB Knowledge Sharing Platform, Section 3.4.1 — WHO-
  recommended treatment regimens for adults with DS-TB (mirrors WHO
  Module 4: Treatment, 2022).
* Retrieval URL: `https://tbksp.who.int/en/node/2774`
* Status: ✅ verbatim.

**`WHO_EML_amoxicillin`**
* Document: WHO Model List of Essential Medicines — 24th List (2025),
  Section 6.2.1, amoxicillin entry, p.11.
* Retrieval URL:
  `https://iris.who.int/bitstreams/17642505-ecd3-4940-a691-4f1dfa0d835a/download`
* Method: `pdftotext` → lines 985–1013; dosage forms and indications
  match the published table exactly (table joined into one paragraph for
  corpus retrieval). Status: ✅ verbatim.

**`WHO_EML_TB_medicines`**
* Same document, Section 6.2.5 Antituberculosis medicines, p.18.
* Method: `pdftotext` → lines 1598–1712, filtered to first-line agents
  and FDCs. Status: ✅ verbatim.

**`WHO_MAL_ACT_recommendation`**
* Document: WHO Guidelines for malaria, 14 March 2023, Section 5.2.1.1, p.19.
* Retrieval URL:
  `https://iris.who.int/bitstreams/2a89b703-a454-4622-895c-38bb838e33cb/download`
* Method: `pdftotext` → lines 720–762. Status: ✅ verbatim.

**`WHO_MAL_pregnancy_AL`**
* Same document, Section 5.2.1.4.1 Pregnant and lactating women, p.20.
* Method: `pdftotext` → lines 792–793. Status: ✅ verbatim.

**`WHO_EML_AL_form`**
* Document: WHO Model List of Essential Medicines — 24th List (2025),
  Section 6.5.3.1, artemether + lumefantrine entry, p.24.
* Method: `pdftotext` → lines 2081–2092. Status: ✅ verbatim.

**`WHO_IMCI_amox_dosing`**
* Document: WHO. Revised WHO classification and treatment of childhood
  pneumonia at health facilities — Quick Reference Guide (2014), Table:
  Doses of amoxicillin for children 2–59 months of age with pneumonia.
* Retrieval URL:
  `https://iris.who.int/bitstreams/63a61e10-b493-4b31-b279-5e8d0a3842b1/download`
* Method: `pdftotext` → lines 177–226 (table serialized to one
  paragraph; numeric values and wording preserved character-for-
  character). Status: ✅ verbatim.

---

## Update — Pakistani sources retrieved manually (via Codex, 2026-09)

The two initial `NOT_RETRIEVED` placeholders have been closed with
verbatim text from the actual documents, retrieved outside the sandbox:

- **NTP_PK_2019_* chunks (8):** National Guidelines for the Control of
  Tuberculosis in Pakistan (Revised 2019), Ministry of National Health
  Services, Regulations & Coordination, Government of Pakistan
  (www.ntp.gov.pk). Sections: Foreword, Ch. 1 (epidemiology), Ch. 2.2
  (WHO TB Standards 1, 4, 6, 12, 27 as incorporated nationally), Ch. 3.2
  (signs & symptoms). Chunks: `NTP_PK_2019_pulmonary_symptoms`,
  `NTP_PK_2019_burden`, `NTP_PK_2019_free_care`,
  `NTP_PK_2019_firstline_regimen`, `NTP_PK_2019_xpert_diagnosis`,
  `NTP_PK_2019_cxr_role`, `NTP_PK_2019_prompt_evaluation`,
  `NTP_PK_2019_notification`.

- **NEML_PK_2023_* chunks (5):** Official Gazette of Pakistan
  (Extraordinary) Part II, S.R.O. 1423(I)/2023, 16 October 2023 — DRAP
  notification adopting the WHO Model List of Essential Medicines 23rd
  List (2023) as Pakistan's essential medicines list under the Drug
  Pricing Policy 2018. Sections: 6.2.1 (amoxicillin), 6.2.2
  (azithromycin), 6.2.3 (anti-TB medicines), 6.5.3.1 (artemether +
  lumefantrine), and the S.R.O. notification itself. Chunks:
  `NEML_PK_2023_notification`, `NEML_PK_2023_artemether_lumefantrine`,
  `NEML_PK_2023_amoxicillin`, `NEML_PK_2023_azithromycin`,
  `NEML_PK_2023_tb_medicines`.

- **NIH_* chunks (4):** NIH Pakistan Advisory No.
  F.1-22/Advisory/CDC/2024 (22 May 2024) — Advisory for Prevention and
  Treatment of Typhoid Fever including XDR Typhoid, published in full
  inside the NIH Public Health Bulletin–Pakistan, Week 22, 2024 (Center
  of Disease Control, National Institute of Health, Islamabad).
  Verbatim. Chunks: `NIH_XDR_typhoid_2024_resistance`,
  `NIH_XDR_typhoid_2024_stewardship`, `NIH_typhoid_case_defn_diagnosis`,
  `NIH_typhoid_prevention_vaccination`.

The corpus now holds **27 verified chunks** (10 WHO + 17 Pakistan-source).
The WHO entries are retained as cross-edition confirmation — the Gazette
legally connects the EML entries to Pakistan's notified list. Source PDFs
are archived in `corpus/sources/` where available.

Verification: chunk text was compared character-for-character against the
retrieved PDFs; NEML dosage-table entries extracted via OCR were
confirmed against page images from the same Gazette document.

---

## Integrity checklist

- [x] All retrieved text was copied verbatim; no LLM paraphrasing.
- [x] The initial NOT_RETRIEVED placeholders were honestly labelled at
      retrieval time and later closed with real document text — nothing
      was ever invented to fill a gap.
- [x] For each chunk the corpus records document title, section/page,
      and retrieval method.
- [x] Pakistan-specific chunks cite Pakistani government documents
      (NTP 2019, NEML Gazette S.R.O. 1423(I)/2023, NIH advisory 2024).
- [x] `corpus/chunks.json` is the retrieval-ready store; anything an
      agent cites via chunk id lives in this file.