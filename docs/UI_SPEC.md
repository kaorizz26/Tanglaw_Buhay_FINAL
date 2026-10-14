# Tanglaw-Buhay UI specification

This document records approved staff-facing requirements. The old Phase 6A interface was retired during the frontend reset; Household Prioritization and Assessment Details were subsequently rebuilt. Refine one existing screen per defined task, then review it with LGU staff usability in mind.

## Operational presentation standard

Tanglaw-Buhay is an LGU decision-support system, not a developer dashboard or methodology viewer. An authorized staff member should find the household's important conditions, assessment findings, relevant evidence and available actions within seconds. Use short labels and explanations, readable text, accessible controls and responsive layouts. Do not repeat metrics, warnings or background information across a screen.

Do not place synthetic-support labels, pilot/data-preparation narratives, developer notes, internal task numbers, technical provenance, formulas, mapping or questionnaire-routing details, or lengthy methodology in routine staff screens, including disclosures and tooltips. Keep those details in internal metadata, the [Developer Guide](../DEVELOPER_GUIDE.md) and capstone methodology. Never claim a supplemental or derived value was directly observed or verified by PSA. Show material missing or conflicting answers with a concise, understandable message where they affect interpretation.

Apply this standard to Dashboard, Household Records, Assessments, Subsidy Screening, Review Queue and History, Submissions and Administration. Backend fields are not automatically UI content. A technically correct screen remains incomplete if staff cannot use it easily.

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

Phase 6B.3 Task 1 implements authentication and a reusable server-side admin check. Both roles can access the protected Prioritization and Assessment Details pages. Import/account-management and review workflows still require separate implementation.

## Interpretation and interaction boundaries

- Show survey year and concise coverage/unknown information where relevant. Present understandable stored answers in primary evidence; retain raw codes and decoded meanings only in optional CBMS Record Details. Keep technical assumptions and provenance in internal documentation, not routine staff content.
- Keep system screening statuses separate from staff review statuses. MPI **Needs Data Review** describes calculation gaps; subsidy **For Review** is a system signal, not a staff-assigned review.
- Staff reviews never overwrite system scores, ranks or program findings. Do not imply eligibility, approval or benefit allocation through labels or actions.
- Filters retain the original batch ranks. Any future fixture view must identify ranks as development-batch ranks; future full-municipality records retain municipality-wide ranks after filtering.

## Current staff interface

| Area | Current state |
|---|---|
| Authentication | Database-backed login, sessions and role checks protect staff pages. |
| Household Prioritization | `/assessments` shows stored Run 2 results, search, filters and pagination. |
| Assessment Details | Protected Overview and all 16 MPI indicators show stored findings and scoped evidence. Subsidy details and review actions remain planned. |
| Household Records and other modules | Not yet implemented as staff workflows. |
| Backend and data | Engines, PostgreSQL repositories/migrations, importer and synthetic test fixtures remain. Source records are read through protected, read-only routes. |

The retired Phase 6A UI is recoverable from Git checkpoint `9a6b24c`.
Dashboard, Submissions and Administration screens remain separate future tasks.

## Planned citizen module

The owner has identified the following intended scope for a later citizen portal:

- Subsidy information.
- A digital document dropbox.
- Submission tracking.
- Appropriately authorized household-profile viewing.

This module is not implemented and has no detailed approved screen/workflow specification yet. It must not approve benefits or replace official LGU/agency processes. Household-profile access requires defined authorization before implementation.

## Deferred decisions and proposed additions

Dashboard content, Administration capabilities beyond the approved permissions above, detailed submission workflows, review transitions and finer-grained permissions, citizen access verification and document handling rules remain unspecified. Do not invent them as approved requirements.

No additional future features are approved by this document. Any proposed feature beyond the requirements above needs its own scope and review before implementation. Backend persistence/import and authentication foundations are recorded in the implementation plan; admin workflows and additional visual refinements remain separately scoped tasks.

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
are labeled as planned without destination links. Assessment actions were initially
reserved; later tasks added search, filters and detail navigation as documented below.

For current routes and technical details, see [Developer Guide, section 9](../DEVELOPER_GUIDE.md#9-minimal-staff-interface-after-the-frontend-reset),
[Frontend Task 1](../DEVELOPER_GUIDE.md#22-frontend-task-1-protected-household-prioritization)
and [project context](PROJECT_CONTEXT.md).

Task 1C preserves this interface and separates its stylesheet ownership:
shared foundations in `base.css`, the staff shell/page-header structures in
`staff.css`, and assessment presentation in `assessments.css`. They load in that
order through the shared layout and its reusable `page_styles` block. See
[Developer Guide section 24](../DEVELOPER_GUIDE.md#24-frontend-task-1c-stylesheet-organization).

## Frontend Task 2 — Prioritization search and filters

The seven-column table and shared layout now include head-name/Household ID
search, All Barangays plus distinct active-batch barangays, four subsidy
checkboxes, and an MPI completeness dropdown with All Households, All 16
Indicators Determined and With Unknown Indicators. Subsidy selections match
system For Review findings in at least one selected program by default. A radio
selection below the checkboxes offers "At least one selected subsidy" and "Only
the selected subsidy combination", with an explanation that follows the current
selection. Exact mode includes all selected findings and excludes any additional
For Review findings; other statuses are not additional findings. No checked
programs means no subsidy restriction in either mode. The table retains all
actual For Review badges. All other categories intersect. Completeness uses
stored unknown-indicator counts.

Apply Filters submits all controls and starts at page 1. Clear Filters restores
municipality-wide results. PostgreSQL filters the full selected completed run
before 25-row pagination, which retains selections. The page shows accurate
matching counts and an explanatory empty state while preserving original
municipal ranks/ties, scores and stable Household IDs. The original seven
columns remain; no MPI Data Status column, Needs Data Review KPI or vulnerability
categories are introduced. Labels, native keyboard controls, visible focus and
responsive form layout reuse the existing navy/neutral/gold styling.

See [Developer Guide section 25](../DEVELOPER_GUIDE.md#25-frontend-task-2-prioritization-search-and-filters)
for query semantics and verification. Assessment Details is implemented by
Task 3 below; Review Queue and Household Records remain deferred.

## Frontend Task 3 — Household Assessment Details

View Assessment now opens a protected household-specific page with the original
reference ID, head, barangay/municipality, survey year, selected run and stored
score/rank. Back to Prioritization preserves validated originating list filters
and page. Both existing authorized roles can view details.

A shared metric row shows Vulnerability Score / 100, Original Municipality-wide
Rank / batch count, Determined MPI Indicators / 16 and Unknown MPI Indicators.
Overview and MPI Indicators are working section links. Subsidy Screening and
Review History are marked Planned without links.

Overview keeps the household identity and four metrics once, then presents four
dimension-contribution bars / 25 points, compact historical screening statuses,
coverage and a short unknown-indicator note. How it works is an optional,
brief disclosure. Do not show recorded assumption lists or methodology narratives
in this operational view. No arbitrary poverty or eligibility categories are
introduced.

MPI Indicators uses four native expandable dimensions; only Education starts
open. All 16 indicators follow **Indicator Name → Status → one short explanation
→ Points Added → View Evidence**. Each View Evidence disclosure starts closed.
A visible status guide explains all three outcomes without relying on color.

View Evidence groups only relevant, understandable household/member answers by
condition, showing names when recorded, member lines and historical ages. Food
access uses an eight-response grid; assets group quantities by type. A second
collapsed CBMS Record Details section may show only the understandable field
label, stored field identifier, stored response and concise decoded meaning.
Use neutral **Recorded Value** wording for supplemental fields. Material missing
or conflicting answers remain recognizable in brief plain language. Do not
attribute a household result to an individual answer without a member-level
calculation trace. Do not show weights, rules, provenance narratives, technical
limitations or developer assumptions in either evidence disclosure. Preserve
these internally in the Developer Guide and metadata; do not delete or falsify
them. No raw JSON dump or unrelated member profile is shown.

Navy/neutral/gold styling, readable spacing, native keyboard disclosures and
visible focus reuse the existing staff design. A dedicated assessment details
stylesheet loads after shared/module styles to keep page-specific rules focused.

Task B applies the stricter content standard above to the existing Assessment
Details tabs. Synthetic desktop/mobile verification covers all 16 evidence views,
the four-field CBMS Record Details, concise missing/conflicting-answer messages
and distinct L34 illness meanings with verification wording. Internal methodology
and source metadata remain preserved; the approved layout is retained.
