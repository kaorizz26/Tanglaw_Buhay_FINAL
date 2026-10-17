# Tanglaw-Buhay development rules

## Scope and sources

- Extend the existing Flask BSIT capstone application one approved task and deliverable at a time. Follow the current prompt's scope, report the result for review, and stop before starting another task.
- For every task, read this file and any relevant skill, then inspect the affected implementation directly. Search or read bounded sections of project documents only as needed; `DEVELOPER_GUIDE.md` is an optional technical reference, not required preliminary reading.
- Use [UI_SPEC.md](docs/UI_SPEC.md) for current UI requirements, including portal-specific responsive design guidance, and [PROJECT_CONTEXT.md](docs/PROJECT_CONTEXT.md) for system context. The implementation plan and Developer Guide may record history and technical decisions; historical layouts and task notes do not override the current approved UI direction. Follow task-specific instructions within the permanent safety rules below.
- Preserve unrelated work. Keep milestone status accurate when the task calls for an implementation-plan update: mark Completed only after its acceptance criteria are verified.

## Permanent system, security, and data rules

- Do not change completed MPI or subsidy calculations without explicit approval, including mappings, weights, thresholds, uncertainty handling, assumptions, ranks, statuses, evidence, and warnings. Keep MPI and the four subsidy engines independent. UI filters must not recalculate source-batch ranks; staff decisions must not overwrite system findings.
- Preserve source field names, values, survey year, and join identities: household `uuid`; member `uuid` and `line_number`; Section L member line within the same household. Do not invent missing responses or data mappings. Retain synthetic-support provenance, provisional codes, and pilot assumptions in internal metadata and technical documentation.
- Never commit private household/member data, credentials, or household-level comparison exports. Use synthetic examples and aggregate verification output. Preserve the fixture-only guard on public routes; actual CBMS access requires authenticated staff authorization and a protected source/repository boundary.
- Describe results as historical decision support, never as official poverty classification, subsidy eligibility, approval, or benefit allocation. A system flag does not automatically create a staff review.

## Staff-facing UI

- Design for nontechnical LGU staff. Put understandable household conditions, assessment findings, supporting evidence, and staff actions first. Use clear labels, useful explanations, intuitive navigation, and readable, accessible responsive layouts.
- Never copy technical documentation into operational screens. Omit synthetic-support labels, pilot-approximation or provenance narratives, developer notes, formulas, internal implementation details, and unnecessary methodology. Do not hide these in disclosures or tooltips.
- Preserve material missing, conflicting, or uncertain information through brief, understandable messages. Do not imply that supplemental or derived values were directly observed or verified by PSA. Keep accurate origins, transformations, and limitations internally.
- Show all 16 MPI indicators with Deprived, Not Deprived, or Unknown findings and relevant household/member information through View Evidence. CBMS Record Details was intentionally removed from MPI Indicators; preserve original source values and mappings internally. Keep MPI and subsidy screening findings separate from staff review decisions.
- Review affected screens from an LGU staff member's perspective before marking UI work complete.

## Implementation and verification

- Keep code simple, readable, modular, and consistent with repository conventions and PEP 8. Separate routes, services, repository access, templates, styles, and interaction scripts. Keep frontend source expanded and organized by feature; preserve keyboard access and readable contrast.
- Prefer the smallest maintainable change. Avoid unnecessary files, abstractions, dependencies, unrelated refactoring, and lengthy documentation logs. Update essential technical documentation when architecture or developer-facing behavior materially changes.
- Check changes proportionately: focused tests and relevant viewport checks under [UI_SPEC.md](docs/UI_SPEC.md) for small UI work; the full unittest suite and relevant Developer Guide checks for backend/database changes, higher-risk behavior changes, or final integration. Documentation-only work needs content, link, and diff review rather than application tests.
- For an explicitly authorized behavior-preserving engine refactor, compare complete outputs before and after; equal totals alone are insufficient. Keep private comparisons in Git-ignored storage.
