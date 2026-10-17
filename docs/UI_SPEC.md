# Tanglaw-Buhay UI/UX specification

## Purpose and authority

Tanglaw-Buhay helps authorized LGU staff understand historical household conditions, automated vulnerability assessments, and separate subsidy screening findings. It supports decisions; it does not issue official poverty classifications, determine eligibility, approve benefits, or replace staff judgment.

This document sets the current staff-facing UI direction and responsive expectations for a future citizen portal. [AGENTS.md](../AGENTS.md) holds permanent development, security, and data rules; [PROJECT_CONTEXT.md](PROJECT_CONTEXT.md) explains the system and approved assessment boundaries. The [implementation plan](IMPLEMENTATION_PLAN.md) and [Developer Guide](../DEVELOPER_GUIDE.md) retain task history and technical detail, but historical screen arrangements and testing records are not current UI requirements. Follow the current approved task within the permanent rules.

## General experience

- Design for LGU personnel who have not read the CBMS questionnaire or developer documentation. Use plain, precise labels and explain what a finding means for the household.
- Make the next relevant action and related information easy to find. Use a clear visual hierarchy: essential household information and findings first, supporting evidence on demand. Preserve existing branding while allowing layouts and components to improve when an approved task calls for it.
- Make available and planned destinations clear in navigation. Staff should be able to find Dashboard, Household Records, Assessments, Submissions, and Administration, then the relevant assessment information, without dead-end links.
- Keep pages readable and accessible at the viewports appropriate to each portal, with keyboard-operable controls, visible focus, sufficient contrast, and meaningful labels. Disclosures must open and close predictably without obstructing other controls.
- Show uncertainty where it matters, briefly and accurately. Never invent missing answers or imply that supplemental or derived values were directly collected or verified by PSA.
- Keep routine screens free of synthetic-support labels, pilot-development or provenance narratives, technical formulas, data-preparation notes, internal task references, developer assumptions, and lengthy methodology. Retain those details in internal metadata and technical documentation.
- Check usability from a first-time staff perspective as well as functional correctness before completing a screen.

## Responsive design by portal

### LGU staff portal

- Design primarily for desktop and laptop browsers. Use **1440px** as the primary design and testing viewport, with quick compatibility checks at **1366px** and **1920px**.
- Prioritize readable tables, efficient navigation, accessible controls, and professional desktop workflows. Keep important information and actions easy to scan and reach.
- Maintain basic responsiveness at smaller widths: layouts must not break, the page must not overflow horizontally, and functionality must remain accessible. Wide data tables may scroll within their own containers when necessary.
- Routine staff portal tasks do not require extensive mobile-specific optimization or testing. Check smaller widths when the affected layout or interaction warrants it.

### Citizen portal (future implementation)

- Design mobile-first for citizens using smartphones, tablets, laptops, or desktop computers. Provide a complete, usable experience across screen sizes.
- Keep forms, document uploads, navigation, and other citizen features accessible on both mobile and desktop.
- When the citizen portal is implemented, perform appropriate mobile and desktop usability testing for its affected workflows. Its access and security rules require an approved implementation task.

## Staff modules

| Module | Staff purpose and scope |
| --- | --- |
| Dashboard | Provide a clear entry point to available staff work and relevant system information. Add summary content only when its meaning and source are defined. |
| Household Records | Help authorized staff find and understand household and member records, with enough context to interpret answers. Define its navigation and any permitted staff actions in the approved implementation task; preserve original source data. |
| Assessments — Household Prioritization | Find households by their stored vulnerability results using the existing search, filters, and pagination. Explain the score and municipal rank in everyday language; these controls must not change the rank calculated from the full selected source batch. |
| Assessments — Household Assessment | Explain a household's Overview and MPI Indicators, with supporting evidence available when needed. Show the four dimensions and all 16 indicators without changing stored calculations or outcomes. |
| Subsidy Screening | Present separate historical findings for 4Ps, SLP, SPISC, and TUPAD with understandable reasons and focused recorded information. Use the staff-facing labels For Review, Not Enough Information, and No Review Signal while preserving stored statuses. Screening results are decision support, not an approval, eligibility decision, or opened staff review. |
| Review Queue and History | When implemented, let authorized staff independently initiate and follow household/program subsidy reviews, including households without an automated For Review signal. Keep staff decisions separate from automated assessment and screening findings. |
| Submissions | When implemented, provide a clear place for the approved staff submission workflow. Define its actions and permissions in the task that implements it. |
| Administration | Let System Admins manage approved import and staff-account functions. Keep these actions admin-only and behind the existing source-access controls. |

The table states intended purposes, not a claim that every module is already implemented. Consult the relevant implementation-plan section for current milestone status before changing a module. It does not prescribe specific cards, metric rows, bars, or component arrangements.

## Assessment content and evidence

- Make the household identity, historical survey context, score, municipal rank, and assessment findings understandable without requiring methodology knowledge. The score reflects recorded deprivation in the existing MPI; do not introduce new vulnerability categories or recompute results in the UI.
- On the Overview, explain what the household's results mean across the four MPI dimensions. Let staff locate indicators needing attention or those with unknown findings in the relevant dimension.
- For each of the 16 MPI indicators, present its name, Deprived, Not Deprived, or Unknown status, a concise explanation of the finding, its points contribution, and View Evidence for relevant household or member answers. An explanation should add meaning, not merely repeat the status.
- Evidence should identify the relevant household or member and show understandable recorded answers. Include enough original survey context—such as population, reason, period, or units—when a shortened field label would change the answer's meaning. Decode known values accurately; describe genuinely missing, conflicting, or uncertain responses briefly.
- CBMS Record Details was intentionally removed from MPI Indicators. Preserve original source values and mappings internally; keep staff-facing View Evidence understandable without technical record details.
- Keep the four subsidy programs' automated findings separate from MPI and from any later staff review. Do not add eligibility rules, change screening engines, or present a system flag as a staff decision.
- Use system statuses precisely: MPI indicator findings are **Deprived**, **Not Deprived**, or **Unknown**. For routine subsidy screens, display stored `For Review` as **For Review**, `Needs Data Review` as **Not Enough Information**, and `No Historical CBMS Trigger` as **No Review Signal**. These labels are separate from staff review decisions and do not automatically open a review.

## Design and change boundaries

Reuse shared branding, navigation, styles, and accessible interaction patterns where they serve staff well. An implemented layout is not a permanent requirement solely because it appears in an earlier task description. Change only the screen and behavior approved for the current task, preserve assessment and security rules, and verify that staff can understand the result.

Earlier versions of this file remain in Git as a record of frontend tasks, testing, the [historical Figma reference](https://www.figma.com/design/bDnkjYWFZW7cSmROuazJSp/Tanglaw-Buhay-UI?node-id=22-907), and a proposed future citizen portal. Those records do not authorize or prescribe current staff UI work; future citizen access and workflows need their own approved scope and security rules.
