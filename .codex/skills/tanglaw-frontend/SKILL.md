---
name: tanglaw-frontend
description: Develop, debug, or refine existing Tanglaw-Buhay Flask/Jinja2/CSS/JavaScript staff interfaces.
---

# Tanglaw-Buhay frontend

- For every task, read repository `AGENTS.md` and this skill, then inspect the actual implementation files directly. Preserve approved layouts, navigation, accessibility, and assessment presentation unless the task explicitly changes them.
- Use targeted searches or bounded ranges to read only the needed sections of `docs/PROJECT_CONTEXT.md`, `docs/UI_SPEC.md`, or `docs/IMPLEMENTATION_PLAN.md`; never load those documents wholesale by default. Treat `DEVELOPER_GUIDE.md` as an optional technical reference. Reproduce a reported defect before editing when practical.
- Make the smallest maintainable fix; avoid unrelated refactoring or application changes outside the requested task.
- Preserve assessment calculations, stored findings and source values, permissions, and private data. Do not alter backend or database behavior to solve a presentation issue.
- Preserve essential technical documentation, but do not add lengthy development logs for minor UI changes.
- Use focused tests for small UI changes and check the affected screen at relevant desktop and mobile sizes. Reserve the full regression suite for higher-risk changes or final integration verification.
- Briefly report the fix, changed files, verification, and any remaining issues. Stop for review; never automatically commit, push, or merge.
