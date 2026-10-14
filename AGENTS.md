# Tanglaw-Buhay development rules

## Scope and workflow

- This is the final, existing Flask BSIT capstone application. Extend it incrementally; do not replace it with a new app or prototype.
- Work on **one task with one defined deliverable**, then report the result for review. Do not start another phase or add unrelated functionality automatically.
- Read [DEVELOPER_GUIDE.md](DEVELOPER_GUIDE.md), [project context](docs/PROJECT_CONTEXT.md), [UI specification](docs/UI_SPEC.md) and the [implementation plan](docs/IMPLEMENTATION_PLAN.md) before relevant work. Inspect the implementation rather than guessing missing rules or mappings.
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

## Coding and verification standards

- Write clean, readable, maintainable code following repository conventions and PEP 8 for Python. Keep formatting, imports, naming and indentation consistent; preserve existing behavior unless a change is explicitly authorized.
- Keep frontend source expanded and readable: use consistent 2-space HTML/Jinja2, CSS and JavaScript indentation, separate logical blocks, break long attributes across lines, and organize CSS by purpose. Never minify source or pack unrelated elements/statements onto one line; keep shared layouts separate from page content.
- Separate responsibilities: Flask routes handle requests; services hold business logic; repository functions handle PostgreSQL access outside routes/templates; structured Jinja2 templates and reusable components present data; organized CSS styles it; focused JavaScript handles interaction.
- Keep functions focused with descriptive names. Avoid monolithic files, excessively long functions, deep nesting and duplicated logic; reuse existing utilities.
- Add concise docstrings for nontrivial functions, explaining purpose and important inputs/outputs. Comment complex logic, project assumptions and non-obvious decisions, without narrating obvious code line by line. Use type hints where they improve readability.
- Prefer simple solutions. Avoid unnecessary abstractions, dependencies, premature optimization, speculative features and overengineering.
- Refine UI screens individually against the approved specification, preserving responsive behavior, keyboard access and readable contrast/text sizes.
- Update `DEVELOPER_GUIDE.md` when architecture or developer-facing behavior materially changes.
- Run checks appropriate to the task, without excessive tests or unrelated audits. For application behavior changes, use `.\.venv\Scripts\python.exe -B -m unittest discover -s tests` and relevant checks in the Developer Guide. Documentation-only changes need content/link and diff review, not private-data processing.
- For an explicitly authorized behavior-preserving engine refactor, compare complete outputs before/after; equal totals alone are insufficient. Keep private comparisons in Git-ignored storage.
