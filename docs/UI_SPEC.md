# Tanglaw-Buhay UI specification

This document records approved requirements and distinguishes them from the Phase 6A implementation. It authorizes no implementation work by itself. Refine one screen per defined task, then review; the existing UI is a foundation, not the final design.

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

Phase 6B.3 Task 1 implements login, protected fixture views for both roles and a reusable server-side admin check. Import/account-management and review workflows still require separate implementation; this task exposes no CBMS data.

## Interpretation and interaction boundaries

- Show survey year, coverage, unknown indicators, assumptions and evidence provenance clearly. Preserve raw values alongside decoded meanings where relevant.
- Keep system screening statuses separate from staff review statuses. MPI **Needs Data Review** describes calculation gaps; subsidy **For Review** is a system signal, not a staff-assigned review.
- Staff reviews never overwrite system scores, ranks or program findings. Do not imply eligibility, approval or benefit allocation through labels or actions.
- Filters retain the original batch ranks. Current fixture ranks must remain identified as development-batch ranks; future full-municipality records retain municipality-wide ranks after filtering.

## Phase 6A implementation status

| Area | Current baseline |
|---|---|
| Navigation | Household Records and Assessments are implemented sidebar links. Dashboard, Submissions and Administration are approved destinations with content still unspecified. |
| Records | `/households` supports search, barangay filters and pagination; `/households/<uuid>` shows read-only profiles/rosters. |
| Prioritization | `/assessments` supports search, sorting, barangay, program-signal and MPI-coverage filters. |
| Assessment details | `/assessments/<uuid>` provides four sections, expandable MPI evidence and selectable program cards. |
| Reviews | Queue and history are explicit placeholders; no persisted reviews or start/complete controls exist. |
| Data and access | All displayed records/counts remain synthetic fixtures. Phase 6B.3 adds staff authentication; actual CBMS access is not implemented. |

The current base template uses an inline brand mark and `static/favicon.svg`; the approved final logo asset and precise branding specifications are not established by this documentation. Existing accessibility/navigation behavior should be preserved during later refinements.

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

For current routes and technical details, see [Developer Guide, section 9](../DEVELOPER_GUIDE.md#9-staff-interface-running-and-extending-phase-6a) and [project context](PROJECT_CONTEXT.md).
