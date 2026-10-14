# Tanglaw-Buhay development rules

## Scope and workflow

- This is the final, existing Flask BSIT capstone application. Extend it incrementally; do not replace it with a new app or prototype.
- Work on **one task with one defined deliverable**, then report the result for review. Do not start another phase or add unrelated functionality automatically.
- For every task, read `AGENTS.md` and any relevant skill, then inspect the actual implementation files directly. Do not automatically read entire project documents: use targeted searches or bounded ranges for only the needed sections of [project context](docs/PROJECT_CONTEXT.md), [UI specification](docs/UI_SPEC.md), and the [implementation plan](docs/IMPLEMENTATION_PLAN.md). Treat [DEVELOPER_GUIDE.md](DEVELOPER_GUIDE.md) as an optional technical reference, consulting its relevant sections when the task needs them. Read further when a change crosses a documented boundary; do not guess missing rules or mappings.
- Follow the user's current task boundaries. Implementation changes are allowed when explicitly requested; unrelated changes are prohibited.
- Follow the approved implementation plan one defined task at a time. Keep milestone status current: **Not Started** means work has not begun; **In Progress** means work has started; **Blocked** means an unresolved issue prevents progress; **Completed** means acceptance criteria have been verified. Only Completed requires successful verification. Report the result and stop before starting another task.
- Keep changes focused and preserve existing work. Report changed files, relevant verification and unresolved context. Do not treat planned requirements as implemented features.

## Protected behavior and data

- Do not change completed MPI or subsidy calculations without explicit approval. This includes mappings, weights, thresholds, uncertainty handling, assumptions, ranks, statuses, evidence and warnings.
- Keep MPI and the four subsidy engines independent. Presentation filters must not recalculate source-batch ranks; staff decisions must not overwrite system findings.
- Preserve source field names, values, survey year and join identities: household `uuid`; member `uuid` + `line_number`; Section L member line within the same household. Do not invent missing responses or data mappings.
- Retain provenance for synthetic support, provisional codes and pilot assumptions. Development metadata is not a production mapping substitute.
- Never commit private household/member data, credentials or household-level comparison exports. Use synthetic examples and aggregate verification output.
- Preserve the fixture-only guard on public routes. Actual CBMS access requires authenticated staff authorization and a protected source/repository boundary; do not bypass the guard.
- Describe results as historical decision support, never official poverty classification, eligibility, approval or benefit allocation. System flags do not automatically create staff reviews.

## Staff-facing UI and evidence

- Tanglaw-Buhay is an operational LGU decision-support system, not a developer dashboard or research methodology viewer. Design for authorized LGU personnel who should understand important household findings within seconds, without technical knowledge.
- Prioritize relevant household information, clear assessment findings and statuses, one short plain-language explanation, relevant household/member evidence, and staff actions. Avoid clutter, repeated warnings or metrics, unnecessary statistics and long paragraphs.
- For each MPI indicator, use **Indicator Name → Status → one short explanation → Points Added → View Evidence**. Support all 16 indicators. Evidence should show only relevant, understandable stored household/member values. Keep material missing or conflicting answers recognizable in concise language.
- Ordinary operational screens must not show Synthetic Support badges, pilot-approximation or data-preparation narratives, developer notes, internal task numbers, lengthy methodology, scoring formulas, mapping rules, questionnaire-routing discussions, provenance narratives, or statements that a view does not recalculate an indicator. Do not move this content into disclosures, tooltips or warning boxes.
- Optional CBMS Record Details may show only an understandable field label, stored field identifier, stored response and concise decoded meaning. Use neutral labels such as **Recorded Value** for supplemental values. Do not put weights, calculation rules, technical limitations, provenance narratives or developer assumptions there.
- Preserve accurate origins, transformations, assumptions and methodological limits in metadata, the Developer Guide and capstone methodology. Do not invent meanings, hide material uncertainty or claim supplemental/derived values were directly collected or verified by PSA. Backend availability alone does not make information suitable for staff-facing content.
- Apply this standard to Dashboard, Household Records, Assessments, Subsidy Screening, Review Queue/History, Submissions and Administration. Review each screen from the perspective of LGU staff before marking it complete; technical correctness alone is insufficient.

## Coding and verification standards

- Write clean, readable, maintainable code following repository conventions and PEP 8 for Python. Keep formatting, imports, naming and indentation consistent; preserve existing behavior unless a change is explicitly authorized.
- Keep frontend source expanded and readable: use consistent 2-space HTML/Jinja2, CSS and JavaScript indentation, separate logical blocks, break long attributes across lines, and organize CSS by purpose. Never minify source or pack unrelated elements/statements onto one line; keep shared layouts separate from page content.
- Separate responsibilities: Flask routes handle requests; services hold business logic; repository functions handle PostgreSQL access outside routes/templates; structured Jinja2 templates and reusable components present data; organized CSS styles it; focused JavaScript handles interaction.
- Keep functions focused with descriptive names. Avoid monolithic files, excessively long functions, deep nesting and duplicated logic; reuse existing utilities.
- Add concise docstrings for nontrivial functions, explaining purpose and important inputs/outputs. Comment complex logic, project assumptions and non-obvious decisions, without narrating obvious code line by line. Use type hints where they improve readability.
- Prefer simple solutions. Avoid unnecessary abstractions, dependencies, premature optimization, speculative features and overengineering.
- Refine UI screens individually against the approved specification, preserving responsive behavior, keyboard access and readable contrast/text sizes.
- Preserve essential technical documentation. Update `DEVELOPER_GUIDE.md` when architecture or developer-facing behavior materially changes; do not append lengthy development logs for minor UI changes.
- Run checks proportionate to the change, without unrelated audits. For small presentation or interaction changes, run focused tests and verify the affected screen at relevant desktop/mobile sizes. Run `.\.venv\Scripts\python.exe -B -m unittest discover -s tests` and relevant Developer Guide checks for backend/database changes, higher-risk application behavior changes, and final integration verification. Documentation-only changes need content/link and diff review, not private-data processing.
- For an explicitly authorized behavior-preserving engine refactor, compare complete outputs before/after; equal totals alone are insufficient. Keep private comparisons in Git-ignored storage.

## Frontend File and Folder Organization

- Organize frontend work by feature/module: Dashboard, Household Records, Assessments, Submissions and Administration. Use appropriately named Jinja2 module directories, and create future modules incrementally without empty scaffolding.
- Keep global design tokens, shared layouts and reusable components separate from page-specific code. Reuse existing shared styles/components instead of duplicating them; keep module-specific styles after shared styles in the cascade.
- Introduce dedicated module CSS and JavaScript files when size or complexity justifies separation. Avoid accumulating unrelated page styles, scripts or markup in one large file; create files only when necessary and avoid excessive fragmentation.
- Follow consistent folder structures, descriptive naming, proper indentation and logical spacing across HTML, Jinja2, CSS, JavaScript and Python. Never minify application source or use excessively compact one-line structures.
- Keep functions/components focused, readable and easy to debug. Maintain clear separation between request routes, services, repositories, templates, styles and interaction scripts.
