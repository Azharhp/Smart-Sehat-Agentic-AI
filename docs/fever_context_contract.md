# District and seasonal fever context

The worker confirms the country, province/region, district and reference date for where the illness occurred. The form defaults the country to Pakistan but requires explicit confirmation; it never derives a district from village text, device location or a language model. Location can remain unknown with a recorded reason. Travel/exposure notes are retained as reported context, not verified epidemiology. Future reference dates are rejected.

Draft fields survive navigation/restart without changing confirmed evidence. Confirmation creates a typed `fever_context` fact with an optimistic revision guard; saving the same value again is idempotent. Corrections supersede the earlier fact, invalidate the packet and require fresh read-back before sending it. Saving context alone does not send another remote packet. English/Urdu read-back, SBAR, encounter JSON and FHIR-shaped export retain the worker's confirmed information.

`config/fever_context.json` ships with version `fever-context-1`, review status `pending` and no entries. No actual malaria/dengue/typhoid priors, disease rates or local risk statements have been supplied. `epidemiology.py` provides deterministic matching for future reviewed content.

A reviewed catalogue requires a nonempty `version`, `review_status: reviewed` and entries with:

| Field | Contract |
|---|---|
| `id` | Unique versioned source entry ID |
| `country`, `province`, `district` | Exact place names; matching normalises case/spacing only |
| `months` | Unique integer calendar months, each from 1 to 12 |
| `valid_from`, `valid_through` | Ordered inclusive ISO date window for the reported reference date |
| `condition` | `malaria`, `dengue` or `typhoid` |
| `context` | Actual reviewed contextual statement for the doctor, with no generated patient claim |
| `reviewed_by`, `reviewed_on` | Real reviewer and review date; future review dates are rejected |
| `source` | Specific surveillance/guideline source, version and scope |

Every entry is schema-validated before use. Missing location/date, unreviewed/malformed content, no date/month/place match and overlapping matches for the same condition are explicit unavailable/ambiguous states. No national/default district prior is silently substituted. Different condition entries can be displayed together, each with its own source and review metadata.

Context is shown separately from AI proposals and exact fact verification. It does not change deterministic urgency, assign a diagnosis, calibrate disease probability or suppress danger signs. The model cannot invent an entry. The doctor interprets reviewed context alongside the case; local notes are not medically verified by the exact Fact Ledger verifier.

Packets bind the catalogue's content hash. Changing reference content invalidates a packet, rejects concurrent publication and makes an existing doctor draft require review of current context. The doctor view displays current confirmed context even if the earlier SBAR is superseded. Local wording approval also binds the catalogue hash. Approval metadata is not an authenticated clinical sign-off system.

`tests/test_consultation_context.py` uses fictional places and explicitly invented source/reviewer notes. None of these fixtures is added to the shipped catalogue. Real regional content still needs the appropriate clinical/public-health review.
