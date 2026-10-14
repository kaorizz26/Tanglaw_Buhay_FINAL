# Tanglaw-Buhay project context

## Purpose and boundaries

Tanglaw-Buhay is the final BSIT capstone Decision Support Analytics Platform for household vulnerability assessment and subsidy pre-screening in Pozorrubio, Pangasinan. It assists LGU/CSWDO staff using historical CBMS evidence.

The staff interface is an operational decision-support tool for authorized LGU personnel. It prioritizes household conditions, findings, concise explanations, relevant evidence and staff actions. Technical methodology and data provenance belong in internal metadata, the [Developer Guide](../DEVELOPER_GUIDE.md) and capstone documentation, not routine staff screens. Material missing or conflicting answers still need brief, clear presentation. Supplemental or derived values must never be described as directly collected or verified by PSA.

It does not establish official poverty classification or subsidy eligibility, approve applications, distribute benefits or replace LGU/agency processes. Staff reviews are separate human activities; a system finding is not an assigned case or review decision.

## Existing baseline

Phase 6A was completed on `dev/phase-6a`; its original staff interface was
retired on `dev/frontend-reset` after authentication checkpoint `9a6b24c`.
The current baseline includes:

- MPI and four independent subsidy engines, with synthetic tests and aggregate verification scripts.
- Unified assessments preserving engine outputs and validating identities, program completeness and survey year.
- PostgreSQL foundations, account repositories, CSV preflight and transactional backend imports.
- An internal read-only active-batch loader reconstructing the seven stored CSV DataFrames and separate survey/provenance metadata; protected query services separately read selected stored assessment results.
- Login/logout, database-checked sessions and a reusable System Administrator guard.
- A protected staff layout, Household Prioritization with search/filters/pagination, and Assessment Details Overview/MPI Indicators for both authorized roles; `/test` remains public and fixture-only.
- Synthetic household generator and metadata fixtures for backend tests.

Old Phase 6A record URLs remain retired. Household Records, Dashboard,
Submissions, Administration, detailed subsidy evidence and staff review workflows
remain separately scoped work. The current assessment pages use authenticated,
batch-scoped, read-only PostgreSQL access; they do not expose CBMS records through
public routes.

The separately authorized first local import is validated active batch `1` in
`tanglaw_buhay_dev`, for 2022 Pozorrubio. Phase 6B.4 Task 1 now verifies exact
DataFrame reconstruction against the local CSV snapshot using read-only queries.
Later completed assessment work is recorded in Developer Guide sections 15–21:
corrected Run 2 is completed and explicitly designated, with 19,304 assessments
and unchanged findings from the four subsidy engines. Historical Run 1 remains.
Frontend Tasks 1–2 add protected read-only Household Prioritization and filters.
Task 3 adds stored Overview and all 16 MPI Indicators with scoped original
evidence; subsidy details, staff reviews and Household Records remain
unimplemented. The separately authorized Task B cleanup now applies the stricter
operational-content standard to these tabs, with verified concise evidence and
distinct, uncertainty-labeled illness meanings. Internal methodology and metadata
remain preserved. The public test route retains its fixture guard.

## CBMS source and provenance

The project owner identifies the authorized **2022 CBMS working dataset** as **19,304 households and 69,350 members**. These are historical batch counts, not current population estimates.

`services/data_loader.py` reads local Excel datasets `household`, `section_a_e_indiv`, `section_l02_to_l41` and `section_l11_to_l14` from `data/raw/`, plus finalized codebook, MPI mapping and variable-definition CSVs from `data/reference/`. Exact filenames and input rules are in the loader and [Developer Guide](../DEVELOPER_GUIDE.md#2-loading-and-joining-cbms-data).

Households join by `uuid`; roster members by `uuid` and `line_number`; Section L records by household UUID and `section_l_line_number`. Original names, blank strings and literal `NA` responses are preserved. Missing joined records do not automatically mean negative responses. Screening uses the explicit survey year; recorded ages are not advanced to the current year.

Preserve documented pilot provenance: synthetic E15 support, E01/OFW assumptions, cohort-based attainment approximation, asset crosswalks, raw-count food insecurity and provisional L07/L16/L34 semantics. The apparent Section L Yes/No recoding is not independently PSA-confirmed. `fixtures/development_metadata.json` contains finalized metadata snapshots and hashes, not household records; it must not replace production references.

The prepared working dataset includes `n01_internet_access`,
`h01_access_to_public_transportation` and `e15_want_more_hours`. Existing
assessments use their stored values. Ordinary staff views should present relevant
values plainly, without development-origin labels or narratives; metadata and
methodology must retain the actual origins and limitations. These supplemental
values must not be called official PSA-collected or verified answers.

The system is not intended to remain limited to the 2022 batch. Under the
adviser-approved project assumption, future Pozorrubio imports will follow the
finalized 2022-compatible CBMS structure. Each future import requires validation
before activation, preservation of historical batches, batch-specific assessment
selection and existing role-based permissions. Source records remain separate
from staff decisions. Household progression across periods is a future goal only
when reliable cross-period identity is available. Future importing through a
staff workflow, progression tracking and identity matching are not yet
implemented; the current controlled import runner is specific to the authorized
2022 batch.

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

The two approved roles are **System Administrator** and **LGU Staff**. Both can sign in and access protected assessment pages; a reusable server-side admin guard is implemented. CBMS imports and staff account management are admin-only requirements, with no web workflows yet. Authorized staff may manage subsidy reviews once that workflow is implemented; imported CBMS source records remain read-only. Account lifecycle and detailed review permissions remain pending. See [UI_SPEC.md](UI_SPEC.md).

Admin import controls, source-file retention, citizen identity and household-access rules, and operational access arrangements remain pending. Resolve these within separately scoped tasks, without copying private records or credentials into documentation. The project's dataset authorization is stated by the owner.
