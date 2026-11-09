# District dashboard

Select **Workflow → District View**. The default filters are **All time** and **All sources**, so saved consultation cases are shown alongside older ledger encounters. Counts include drafts and completed encounters; they do not represent unique patients or proven clinical outcomes.

The totals, clinic/village map counts, case table, assessment trend and legacy review summaries share the same filters. The dashboard refreshes every 10 seconds; **Refresh** also reloads the data immediately. Search by case ID, name, village or outcome. **Case source → Consultation** shows just the current consultation workflow. The case list includes a CSV export and a control to open a saved consultation.

Consultation summaries use the latest urgency output, doctor reply and follow-up per encounter, avoiding multiplication by audit events or packet versions. A closed referral may retain RED as its latest recorded urgency. Legacy YELLOW appears as AMBER in the combined view. Legacy closure means that the older encounter has a recorded outcome/completion; it does not prove completion of transport or treatment.

Map positions come from `config/dashboard_locations.json`. Existing BHU coordinates and the three fictional demonstration villages are explicitly illustrative, not verified facility locations or patient GPS. Fictional village entries only apply to synthetic consultations. No coordinate is guessed for another village. Unmapped cases remain included in the total and appear in the unmapped-location list. Circle size follows the filtered case count; hover for source, urgency and open-case counts. Map tiles need a connection; the location counts and case table remain available locally.

On October 24, 2026, the saved data contained **175 encounters: 168 legacy and 7 consultation cases**, mapped to **7 illustrative locations**. Counts will change as cases are created. These totals include seeded and rehearsal records.

Validation: `python -m unittest tests.test_district_dashboard -v` — 2 focused integration tests passed. Covered both stores, an ID collision across sources, historical date filtering, latest urgency after a correction, map reconciliation and unknown locations, refresh after a new case, empty/search filters, and opening a consultation from the dashboard. The live browser also displayed the combined 175-case count and map.
