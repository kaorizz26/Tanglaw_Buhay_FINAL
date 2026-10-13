# Tanglaw-Buhay implementation plan

Work on one approved task and deliverable at a time. Keep milestone status current using the definitions in [AGENTS.md](../AGENTS.md); only **Completed** requires verified acceptance criteria. Report results and stop for review before starting another task. This plan describes milestones, not authorization to implement all of them.

Phase 6B is a **single-municipality Pozorrubio pilot** supporting the **finalized 2022-compatible CBMS format**. System Admin manages dataset imports and staff accounts; staff cannot access import functions. Automatic ETL validates files and preserves source responses. A failed import must leave the active dataset intact. Imported source records are read-only; authorized staff manage subsidy reviews separately. Existing MPI and subsidy calculations remain unchanged.

## Phase 6A — Assessment engines and staff UI foundation

- **Status:** Completed.
- **Objective:** Establish the existing MPI and four subsidy engines, unified assessments and initial staff screens.
- **Small tasks:** Maintain independent engine outputs; combine results by household; serve labeled synthetic development fixtures in records and assessment views.
- **Acceptance criteria:** Existing synthetic tests and documented aggregate verification support the completed calculations; fixture pages show historical findings without implying official eligibility or real CBMS access.
- **Implementation and verification notes:** Current code uses 32 synthetic households; public feature routes reject non-fixture workspaces. Review Queue/History are placeholders. The [Developer Guide](../DEVELOPER_GUIDE.md) holds the detailed rules and prior verification; this milestone records the existing Phase 6A baseline, not a new test run.

## Phase 6B.1 — PostgreSQL database foundation

- **Status:** Not Started.
- **Objective:** Establish a maintainable persistence foundation for the approved pilot.
- **Small tasks:** Agree on the minimum data contract and schema before coding; add versioned database changes and repository boundaries for future imports, assessments and reviews; configure connections without embedding credentials.
- **Acceptance criteria:** Database setup is repeatable in a development environment; application database access is isolated from routes/templates; no private data or credentials are committed; the existing fixture UI and engines retain their behavior.
- **Implementation and verification notes:** `db.py` currently only opens explicit connections. The schema, migration approach and retention policy are pending decisions. Verify setup and rollback with nonprivate data when this task is authorized.

## Phase 6B.2 — Validated CBMS ETL and admin-only import workflow

- **Status:** Not Started.
- **Objective:** Import the finalized 2022-compatible CBMS files automatically and safely for this municipality.
- **Small tasks:** Validate required files, references, fields and join keys using the existing loader rules; preserve raw responses and provenance; stage an import and activate it only after complete validation; provide an import workflow restricted to System Admin.
- **Acceptance criteria:** Valid files load without rewriting source answers; validation failures report useful aggregate issues and leave the active dataset intact; the import workflow is not exposed before 6B.3 enforces System Admin access.
- **Implementation and verification notes:** Build and verify the import path with synthetic inputs. Any exposed import control must wait for the enforced permissions in 6B.3; until then, keep the workflow inaccessible to ordinary users. Exact import lifecycle and schema depend on approved 6B.1 decisions.

## Phase 6B.3 — Authentication and role-based permissions

- **Status:** Not Started.
- **Objective:** Enforce access to staff records, imports, accounts and reviews.
- **Small tasks:** Authenticate staff; enforce System Admin access to imports and staff account management; allow authorized staff to manage subsidy reviews while keeping imported CBMS source records read-only.
- **Acceptance criteria:** Unauthenticated users cannot access protected CBMS views; non-admin staff cannot invoke import/account functions; authorized review changes do not alter source survey data or system findings.
- **Implementation and verification notes:** Verify permitted and denied requests with synthetic accounts/records before exposing import functions or real CBMS data. The approved roles are System Administrator and LGU Staff; account lifecycle and finer permissions remain pending.

## Phase 6B.4 — Real CBMS data integration

- **Status:** Not Started.
- **Objective:** Show authorized 2022 CBMS households and computed assessments through protected staff views.
- **Small tasks:** Read the active imported batch through repository functions; connect read-only household/member views and existing assessment services; retain evidence provenance, full-batch ranks and clear survey-year labels.
- **Acceptance criteria:** Only authenticated, authorized staff can view real records; the fixture-only guard is not bypassed on public routes; source records stay read-only; filtered views preserve original assessment results and ranks.
- **Implementation and verification notes:** Begin only after import safety and access controls are verified. Use aggregate or synthetic checks and keep household-level data out of logs, tests and documentation.

## Phase 6B.5 — End-to-end verification

- **Status:** Not Started.
- **Objective:** Verify the full approved pilot flow before treating Phase 6B as complete.
- **Small tasks:** Exercise valid and failed imports, admin/staff access boundaries, read-only source views, assessment output preservation and staff review isolation; check user-facing status/provenance labels.
- **Acceptance criteria:** A failed import cannot replace or corrupt the active batch; permissions hold across routes and direct actions; existing MPI/screening outputs remain unchanged; authorized reviews cannot modify original survey data.
- **Implementation and verification notes:** Use synthetic cases and aggregate comparisons, with private full-batch comparisons only in Git-ignored storage if authorized. Record results against each criterion without copying household data into reports.

Detailed database schema, migration choice, import lifecycle, finer permissions and account policy are pending. Resolve each within its approved milestone; do not infer them from this roadmap. Staff UI requirements and system boundaries remain in [UI_SPEC.md](UI_SPEC.md) and [PROJECT_CONTEXT.md](PROJECT_CONTEXT.md).
