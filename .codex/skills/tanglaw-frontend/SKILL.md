---
name: tanglaw-frontend
description: Develop, debug, or refine existing Tanglaw-Buhay Flask/Jinja2/CSS/JavaScript staff interfaces.
---

# Tanglaw-Buhay frontend

- For every frontend task, read repository `AGENTS.md` for permanent system/security rules and this skill, then inspect the affected implementation directly. Use the current `docs/UI_SPEC.md` for staff-facing presentation. Preserve functionality, navigation, accessibility, and useful design patterns; improve layout, content hierarchy, or components when the current task explicitly authorizes it.
- Use targeted searches or bounded ranges to read only the needed sections of `docs/UI_SPEC.md`, `docs/PROJECT_CONTEXT.md`, or `docs/IMPLEMENTATION_PLAN.md`; never load those documents wholesale by default. Treat `DEVELOPER_GUIDE.md` as an optional technical reference. Reproduce a reported defect before editing when practical.
- Make the smallest maintainable fix; avoid unrelated refactoring or application changes outside the requested task.
- Preserve assessment calculations, stored findings and source values, permissions, and private data. Do not alter backend or database behavior to solve a presentation issue.
- For frontend implementation, review the result as a first-time LGU staff member: Is the purpose clear, are important household conditions and findings easy to identify, are explanations understandable without technical knowledge, is the relevant action easy to find, and is repeated or unnecessary content removed?
- Present relevant CBMS household/member evidence in plain language. Keep material missing, conflicting, or uncertain answers accurate and understandable; retain technical details internally rather than showing development terms, provenance narratives, methodology, or assumptions in routine screens.
- Preserve essential technical documentation, but do not add lengthy development logs for minor UI changes.
- Use focused tests for small UI changes. For minor UI changes, verify at 1440px (desktop) and 375px (mobile). For major layout or responsive changes, additionally check 1024px and 320px. Test other widths only when necessary. Prioritize readability, alignment, overflow, accessibility, and functionality over excessive screenshot generation. Reserve the full regression suite for higher-risk changes or final integration verification.
- Briefly report the fix, changed files, verification, and any remaining issues. Stop for review; never automatically commit, push, or merge.
