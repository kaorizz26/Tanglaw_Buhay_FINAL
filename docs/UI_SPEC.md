# Tanglaw-Buhay UI specification

This document records approved requirements for the future staff UI. The old Phase 6A interface is intentionally retired on `dev/frontend-reset`; approved requirements remain, but new screens are not implemented by this reset. Refine one screen per defined task, then review.

## Approved staff interface requirements

- Professional LGU interface using the Tanglaw-Buhay logo, navy, neutral colors and muted-gold accents.
- Responsive, accessible layouts with approximately 16px base text and 15–16px tables/controls, readable contrast, labeled controls and keyboard access.
- Sidebar destinations: **Dashboard, Household Records, Assessments, Submissions, Administration**.

| Area | Approved requirement |
|---|---|
| Household Records | Search, barangay filters and pagination; read-only household/member profiles. |
| Assessments | Prioritization and Review Queue. Staff can initiate household/program reviews from Prioritization or Assessment Details. |
| Assessment Details | Overview, MPI Indicators, Subsidy Screening and Review History sections. |
| MPI Indicators | Four expandable dimensions with plain-language indicator explanations and supporting household/member evidence. |
| Subsidy Screening | Four selectable program cards (4Ps, SLP, SPISC, TUPAD), showing findings and evidence. |
| Staff reviews | Separate staff-initiated reviews per household and subsidy, with statuses, notes and activity history. |

The Developer Guide records planned review labels **Not Yet Reviewed / In Review / Completed** and outcomes **Further Verification Recommended / No Further Action**, continuation by authorized staff, and justification when starting without a historical trigger. These are planned workflow requirements, not implemented persistence or a complete transition/permission specification.

## Approved permissions

- CBMS imports and staff account management are admin-only.
- Imported CBMS source records are read-only, including original household/member survey data.
- Authorized staff can manage subsidy reviews, including statuses, notes and activity history, without modifying original survey data.

Phase 6B.3 Task 1 implements authentication and a reusable server-side admin check. After the frontend reset, both roles access only a minimal protected landing. Import/account-management and review workflows still require separate implementation; this task exposes no CBMS data.

## Interpretation and interaction boundaries

- Show survey year, coverage, unknown indicators, assumptions and evidence provenance clearly. Preserve raw values alongside decoded meanings where relevant.
- Keep system screening statuses separate from staff review statuses. MPI **Needs Data Review** describes calculation gaps; subsidy **For Review** is a system signal, not a staff-assigned review.
- Staff reviews never overwrite system scores, ranks or program findings. Do not imply eligibility, approval or benefit allocation through labels or actions.
- Filters retain the original batch ranks. Any future fixture view must identify ranks as development-batch ranks; future full-municipality records retain municipality-wide ranks after filtering.

## Current interface after the approved reset

| Area | Current baseline |
|---|---|
| Authentication | Minimal login/logout templates; database-backed account/session and role checks are retained. |
| Landing | `/` shows the signed-in role and a CSRF-protected Sign Out form. |
| Retired records/assessments | Old list/detail URLs return HTTP 410 after authentication, with no household data. |
| Removed frontend | Sidebar, old records/assessment screens, filters, cards, review placeholders, shared UI components, CSS/JavaScript and presentation-only helpers. |
| Backend and data | Engines, unified assessments, PostgreSQL repositories/migrations, CSV preflight/importer and synthetic fixtures remain. The interface displays no household records. |

The old UI is recoverable from Git checkpoint `9a6b24c`. No new dashboard,
submissions, administration panels or design system are introduced. Final logo
assets and precise branding remain to be established during approved design work.

## Planned citizen module

The owner has identified the following intended scope for a later citizen portal:

- Subsidy information.
- A digital document dropbox.
- Submission tracking.
- Appropriately authorized household-profile viewing.

This module is not implemented and has no detailed approved screen/workflow specification yet. It must not approve benefits or replace official LGU/agency processes. Household-profile access requires defined authorization before implementation.

## Deferred decisions and proposed additions

Dashboard content, Administration capabilities beyond the approved permissions above, detailed submission workflows, review transitions and finer-grained permissions, citizen access verification and document handling rules remain unspecified. Do not invent them as approved requirements.

No additional future features are approved by this document. Any proposed feature beyond the requirements above needs its own scope and review before implementation. Backend persistence/import and authentication foundations are recorded in the implementation plan; admin workflows, actual CBMS views and visual refinements remain separately scoped tasks.

## Frontend Task 1 — Household Prioritization

The protected `/assessments` page now uses a reusable staff layout based on the
approved [Figma screen](https://www.figma.com/design/bDnkjYWFZW7cSmROuazJSp/Tanglaw-Buhay-UI?node-id=22-907).
After Task 1B it shows seven columns: Municipal Rank, Household ID, Household
Head (name and original UUID), Barangay, Vulnerability Score, Subsidies for Review
and a reserved Action column. Household IDs use `HH-` plus the zero-padded stored
CSV data-row number (for this batch, `HH-00001`–`HH-19304`). They are stable
Tanglaw-Buhay references within a source batch, not official PSA identifiers.
Only exact For Review findings receive badges. Shared ranks have equal priority;
their display order does not break ties. Original stored ranks/ties remain;
25-row server-side pagination provides counts, page numbers, Previous and Next.

The original local logo and municipal flag/seal are reused. Text, focus states,
semantic table headers and a keyboard-accessible mobile menu support readable
responsive presentation, with horizontal table scrolling on narrower screens.
The staff account displays the actual username and role; the account schema has
no personal display-name field. Unimplemented sidebar modules and Review Queue
are labeled as planned without destination links. Assessment actions remain
reserved; no fake controls, search, filters or detail navigation were added.

For current routes and technical details, see [Developer Guide, section 9](../DEVELOPER_GUIDE.md#9-minimal-staff-interface-after-the-frontend-reset),
[Frontend Task 1](../DEVELOPER_GUIDE.md#22-frontend-task-1-protected-household-prioritization)
and [project context](PROJECT_CONTEXT.md).
