# Tanglaw-Buhay project context

## Purpose and boundaries

Tanglaw-Buhay is the final BSIT capstone Decision Support Analytics Platform for household vulnerability assessment and subsidy pre-screening in Pozorrubio, Pangasinan. It assists LGU/CSWDO staff using historical CBMS evidence.

It does not establish official poverty classification or subsidy eligibility, approve applications, distribute benefits or replace LGU/agency processes. Staff reviews are separate human activities; a system finding is not an assigned case or review decision.

## Existing baseline

Phase 6A was completed on `dev/phase-6a`; its staff interface is intentionally
retired on `dev/frontend-reset` after authentication checkpoint `9a6b24c`.
Retained features are:

- MPI and four independent subsidy engines, with synthetic tests and aggregate verification scripts.
- Unified assessments preserving engine outputs and validating identities, program completeness and survey year.
- PostgreSQL foundations, account repositories, CSV preflight and transactional backend imports.
- Login/logout, database-checked sessions and a reusable System Administrator guard.
- A minimal protected landing showing the signed-in role and Sign Out; `/test` remains public.
- The same synthetic household generator and metadata fixtures for backend tests; no household records are loaded/rendered by the minimal interface.

Old record/assessment URLs now return a generic retirement response after login.
The sidebar, records/assessment templates, CSS/JavaScript and UI-only helpers are
removed. Admin import/account screens, real CBMS display and the new staff design
remain separately scoped work; no dashboard or replacement staff panels exist.

## CBMS source and provenance

The project owner identifies the authorized **2022 CBMS working dataset** as **19,304 households and 69,350 members**. These are historical batch counts, not current population estimates; the frontend reset does not revalidate private source files.

`services/data_loader.py` reads local Excel datasets `household`, `section_a_e_indiv`, `section_l02_to_l41` and `section_l11_to_l14` from `data/raw/`, plus finalized codebook, MPI mapping and variable-definition CSVs from `data/reference/`. Exact filenames and input rules are in the loader and [Developer Guide](../DEVELOPER_GUIDE.md#2-loading-and-joining-cbms-data).

Households join by `uuid`; roster members by `uuid` and `line_number`; Section L records by household UUID and `section_l_line_number`. Original names, blank strings and literal `NA` responses are preserved. Missing joined records do not automatically mean negative responses. Screening uses the explicit survey year; recorded ages are not advanced to the current year.

Preserve documented pilot provenance: synthetic E15 support, E01/OFW assumptions, cohort-based attainment approximation, asset crosswalks, raw-count food insecurity and provisional L07/L16/L34 semantics. The apparent Section L Yes/No recoding is not independently PSA-confirmed. `fixtures/development_metadata.json` contains finalized metadata snapshots and hashes, not household records; it must not replace production references.

## MPI vulnerability assessment

The adapted MPI has 16 indicators across four dimensions, each contributing 25%:

| Dimension | Indicators | Weight per indicator |
|---|---|---|
| Education | School Attendance; Educational Attainment | 1/8 |
| Health and Nutrition | Food Insecurity; Illness/Treatment Proxy | 1/8 |
| Housing, Water and Sanitation | Sanitation; Water; Assets; Tenure; Housing Materials; Electricity; Internet; Overcrowding; Public Transportation | 1/36 |
| Employment | Employment Proxy; Underemployment Proxy; Working Child Not in School | 1/12 |

Flags are `1` (deprived), `0` (not deprived) or `None` (unknown). The score is `100 × sum(deprived indicator weights)`. Unknowns remain unknown and contribute zero points without weight redistribution, which can underestimate vulnerability. Coverage reports known indicator weight; approved assumptions can produce known outcomes without observed answers.

Exact fractional scores preserve ties; descending competition ranks belong to the full assessed batch and remain unchanged by UI filters. These are adapted vulnerability measures, not official PSA MPI classifications. Detailed calculations remain in the [Developer Guide](../DEVELOPER_GUIDE.md#3-deprivation-indicators) and `services/vulnerability/`; changes require explicit approval.

## Independent subsidy screening

Each household receives four separate historical program results:

| Program | Existing project signal, summarized |
|---|---|
| 4Ps | Recorded child ages 0–18 or supported historical household pregnancy evidence. |
| SLP | Observed work evidence for Employment Facilitation (18+) and/or Microenterprise Development (16+) pathways. |
| SPISC | Recorded age 60+; senior-specific disability is optional supporting context. |
| TUPAD | Applicable age 18+ nonwork or irregular-work evidence, including separately approved and labeled pilot nonwork assumptions. |

These summaries do not replace the routing, conflict and uncertainty rules in `services/screening/` and the [Developer Guide](../DEVELOPER_GUIDE.md#5-the-four-subsidy-functions). Subsidy findings do not derive from MPI scores/ranks; synthetic E15 does not create subsidy triggers.

- **For Review:** supported project trigger suggests staff consideration.
- **No Historical CBMS Trigger:** sufficient relevant historical evidence establishes no project trigger; this is not present-day ineligibility.
- **Needs Data Review:** relevant missing/conflicting evidence prevents determining a trigger.

Outputs retain reasons, evidence, member lines, provenance, warnings and unverified requirements. Supporting context alone is not a trigger. A valid trigger can coexist with warnings; informational requirements do not automatically create tasks. Existing metadata records unverified full-text limitations for newer SPISC/TUPAD issuances; do not invent additional policy rules.

## Context still needed for later tasks

The two approved roles are **System Administrator** and **LGU Staff**. Both can sign in and access the minimal protected landing; a reusable server-side admin guard is implemented. CBMS imports and staff account management are admin-only requirements, with no web workflows yet. Authorized staff may manage subsidy reviews once that workflow is implemented; imported CBMS source records remain read-only. Account lifecycle and detailed review permissions remain pending. See [UI_SPEC.md](UI_SPEC.md).

Admin import controls, source-file retention, citizen identity and household-access rules, and operational access arrangements remain pending. Resolve these within separately scoped tasks, without copying private records or credentials into documentation. The project's dataset authorization is stated by the owner.
