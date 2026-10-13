# Tanglaw-Buhay implementation plan

Work on one approved task and deliverable at a time. Keep milestone status current using the definitions in [AGENTS.md](../AGENTS.md); only **Completed** requires verified acceptance criteria. Report results and stop for review before starting another task. This plan describes milestones, not authorization to implement all of them.

Phase 6B is a **single-municipality Pozorrubio pilot** supporting the **finalized 2022-compatible CBMS format**. System Admin manages dataset imports and staff accounts; staff cannot access import functions. Automatic ETL validates files and preserves source responses. A failed import must leave the active dataset intact. Imported source records are read-only; authorized staff manage subsidy reviews separately. Existing MPI and subsidy calculations remain unchanged.

## Phase 6A — Assessment engines and staff UI foundation

- **Status:** Completed.
- **Objective:** Establish the existing MPI and four subsidy engines, unified assessments and initial staff screens.
- **Small tasks:** Maintain independent engine outputs; combine results by household; serve labeled synthetic development fixtures in records and assessment views.
- **Acceptance criteria:** Existing synthetic tests and documented aggregate verification support the completed calculations; fixture pages show historical findings without implying official eligibility or real CBMS access.
- **Implementation and verification notes:** Phase 6A served 32 synthetic households with fixture-only views and placeholder reviews. Its backend/fixtures remain; the old staff interface is intentionally retired by the separately approved frontend reset below. The [Developer Guide](../DEVELOPER_GUIDE.md) records current routes and protected calculations. Completion here records the historical Phase 6A delivery.

## Phase 6B.1 — PostgreSQL database foundation

- **Status:** Completed.
- **Objective:** Establish a maintainable persistence foundation for the approved pilot.
- **Small tasks:** Define the approved eight-table, batch-scoped schema; add a numbered SQL migration; verify keys, JSONB value preservation and active-batch constraints with synthetic rows in a safe test database.
- **Acceptance criteria:** The migration applies transactionally to an empty development database; synthetic constraint checks pass; no private data or credentials are committed; the existing fixture UI and engines retain their behavior.
- **Implementation and verification notes:** Verified on 2026-10-13 with PostgreSQL 18.1. Both local databases authenticated successfully; `0001_database_foundation.sql` committed transactionally to the empty `tanglaw_buhay_dev` database. All eight tables remain empty; catalog checks confirmed eight primary keys, seven foreign keys, source-row uniqueness and the active-batch constraint. Synthetic constraint/value-preservation tests passed in `tanglaw_buhay_test`, with all test DDL and rows rolled back. The full suite passed all 124 tests without skips. The existing `db.py` helper was verified; routes/engines still use fixtures. Private connection URLs remain in Git-ignored `.env`; no real CBMS data was imported. Source-file retention and the import lifecycle remain decisions for 6B.2.

## Phase 6B.2 — Validated CBMS ETL and admin-only import workflow

- **Status:** In Progress.
- **Objective:** Import the finalized 2022-compatible CBMS files automatically and safely for this municipality.
- **Small tasks:** Validate required files, references, fields and join keys using the existing loader rules; preserve raw responses and provenance; stage an import and activate it only after complete validation; provide an import workflow restricted to System Admin.
- **Acceptance criteria:** Valid files load without rewriting source answers; validation failures report useful aggregate issues and leave the active dataset intact; the import workflow is not exposed before 6B.3 enforces System Admin access.
- **Implementation and verification notes:** **Task 1 — CSV preflight: Completed.** The independent contract now also requires household L01/L06/L15 and member A05 screening inputs. All 20 focused synthetic preflight tests passed; original strings and allowed missing/repeated records remain intact.
- **Task 2 — Transactional backend importer: Completed** on 2026-10-13. The service/repository layers import one validated snapshot with generator-backed psycopg bulk insertion, retain source/reference provenance and previous batches, verify stored counts and atomically activate the new batch. All 11 importer tests passed using generated CSVs and isolated schemas in `tanglaw_buhay_test`, including nine executed PostgreSQL tests; rollback before/after activation and concurrent activation were verified. The full suite passed all 155 tests without skips. The importer suite was rerun successfully against the final Task 2 specification, and test schemas were removed. Neither `tanglaw_buhay_dev` nor the old database was modified during testing; no real CBMS files, UI, engines or extra migrations were changed.
- **First controlled local import: Completed** on 2026-10-13 after separate owner authorization. The existing importer created validated active batch `1` in local `tanglaw_buhay_dev` only. Aggregate checks verified all seven expected table counts, zero orphan relationships, file/provenance hashes, unchanged source CSVs/private configuration and preserved staff accounts/session authorization. The reviewed one-time runner is retained in `scripts/controlled_cbms_import_2022.py`; no web import controls, full-batch scoring or real-data views were added.
- **Remaining work:** Admin import controls, source-file retention and real-data integration require separate tasks. This milestone remains In Progress; any exposed import control must wait for enforced System Admin access in 6B.3.

## Phase 6B.3 — Authentication and role-based permissions

- **Status:** In Progress.
- **Objective:** Enforce access to staff records, imports, accounts and reviews.
- **Small tasks:** Authenticate staff; enforce System Admin access to imports and staff account management; allow authorized staff to manage subsidy reviews while keeping imported CBMS source records read-only.
- **Acceptance criteria:** Unauthenticated users cannot access protected CBMS views; non-admin staff cannot invoke import/account functions; authorized review changes do not alter source survey data or system findings.
- **Task 1 — Staff authentication foundation: Completed** on 2026-10-13. Migration 0002, account repository, login/logout, database-checked signed sessions, CSRF, shared route protection, reusable admin guard and prompted local administrator bootstrap are implemented. All 170 tests passed without skips; the final 15 authentication tests (12 executed PostgreSQL tests) also passed, covering roles, invalid credentials, CSRF, expiry/tampering/revocation, bootstrap, migration compatibility and the fixture guard. Tests used generated accounts in disposable `tanglaw_buhay_test` schemas. In subsequent explicitly approved local setup, migration 0002 was applied to `tanglaw_buhay_dev` and the owner created a development administrator using hidden password prompts. Browser checks verified administrator login, protected fixture pages, logout and denial after logout. A separate local LGU Staff account remains pending; existing staff templates/CSS, engines and importer are unchanged. No real CBMS data was imported and the old database was untouched.
- **Remaining work:** Admin import/account workflows, account lifecycle and finer review permissions remain pending. Verify each separately before exposing imports or real CBMS data; this milestone remains In Progress.

## Frontend reset — Approved preparation task

- **Status:** Completed.
- **Objective:** Retire Phase 6A presentation code while retaining authentication and the backend for a future approved UI design.
- **Small tasks:** Inspect dependencies; remove old screens/assets/UI-only helpers; preserve backend fixtures; redirect both roles to a minimal landing; safely retire old URLs; replace obsolete UI assertions and update documentation.
- **Acceptance criteria:** Login/logout, role/session checks, fixture boundary and `/test` remain; landing shows the role and Sign Out; retired URLs expose no records; engines, importer, repositories, migrations, private configuration and persistent database data remain unchanged.
- **Implementation and verification notes:** Branch `dev/frontend-reset` starts at approved authentication checkpoint `9a6b24c`. Sixteen obsolete UI files were removed, backend fixtures/tests retained, and the minimal landing and HTTP 410 retirement routes added. All eight focused synthetic interface tests passed. After explicit authorization for isolated PostgreSQL testing, the full suite passed all **167 tests without skips**, including 22 database integration tests. A read-only precheck verified local `tanglaw_buhay_test`; a runtime guard restricted all 140 test connections to that database and reviewed schema settings. All temporary schemas were removed, the persistent test catalog and private `.env` checksum remained unchanged, and development/old databases and the existing administrator were untouched. Protected backend files and synthetic source generation were verified unchanged. No new design system, staff panels or real CBMS import is included; unapproved assets are excluded from the checkpoint.

## Phase 6B.4 — Real CBMS data integration

- **Status:** In Progress.
- **Objective:** Show authorized 2022 CBMS households and computed assessments through protected staff views.
- **Small tasks:** Read the active imported batch through repository functions; connect read-only household/member views and existing assessment services; retain evidence provenance, full-batch ranks and clear survey-year labels.
- **Acceptance criteria:** Only authenticated, authorized staff can view real records; the fixture-only guard is not bypassed on public routes; source records stay read-only; filtered views preserve original assessment results and ranks.
- **Task 1 — Read-only PostgreSQL active-batch loader: Completed** on 2026-10-13. Repository reads select the validated active batch and order all seven source/reference tables by original row position. The service reconstructs object-dtype DataFrames in recorded column order from unchanged JSON strings, returns separate survey/provenance metadata and uses a read-only repeatable-read snapshot. All 14 focused synthetic tests passed, including exact values/identities/order, safe failures and complete existing engine-output compatibility. Read-only verification of local `tanglaw_buhay_dev`, `public`, batch `1` (2022 Pozorrubio) matched all seven CSV snapshots exactly: 19,304 households, 69,350 members, 18,282 health rows, 33 child-mortality rows, 705 codebook rows, 89 MPI mappings and 214 variable definitions. Schema/join checks passed with 16 indicator inputs and zero orphan/duplicate join keys; stored file hashes matched. The broader suite passed 159 executed tests with 22 database integration tests deliberately skipped to avoid writes. No imports, database writes, real-batch scoring/screening or route changes were performed.
- **Task 2A — Full-batch assessment execution: Completed** on 2026-10-13. The read-only verification script loaded local `tanglaw_buhay_dev`, `public`, validated active batch `1` (2022 Pozorrubio), ran each unchanged engine once and validated the results with the existing unified combiner. All 19,304 MPI results and 77,216 unique household/program results passed identity, survey-year, status, evidence-container, weighted-score/coverage and exact competition-rank checks; all 16 indicators were present. MPI had 19,093 Complete Data households and 211 Needs Data Review households, retaining 212 unknown indicator outcomes. Score min/median/mean/max were 0/5.5556/8.3175/56.9444; mean coverage was 99.8982%. Program status distributions and data gaps are recorded in the Developer Guide. The run took 34.016 seconds with a 466.64 MiB peak process working set; source frames were unchanged. All 65 focused synthetic tests passed without database connections, including six new verifier tests. No database writes, imports, persistence, routes, scoring changes or UI changes were made.
- **Remaining work:** Assessment persistence/integration and authenticated, authorized real-data views require separately scoped tasks. This milestone remains In Progress; the minimal interface retains its fixture guard and displays no real CBMS records.

## Phase 6B.5 — End-to-end verification

- **Status:** Not Started.
- **Objective:** Verify the full approved pilot flow before treating Phase 6B as complete.
- **Small tasks:** Exercise valid and failed imports, admin/staff access boundaries, read-only source views, assessment output preservation and staff review isolation; check user-facing status/provenance labels.
- **Acceptance criteria:** A failed import cannot replace or corrupt the active batch; permissions hold across routes and direct actions; existing MPI/screening outputs remain unchanged; authorized reviews cannot modify original survey data.
- **Implementation and verification notes:** Use synthetic cases and aggregate comparisons, with private full-batch comparisons only in Git-ignored storage if authorized. Record results against each criterion without copying household data into reports.

Source-file retention, finer permissions and account policy remain pending. Resolve each within its approved milestone; do not infer them from this roadmap. Staff UI requirements and system boundaries remain in [UI_SPEC.md](UI_SPEC.md) and [PROJECT_CONTEXT.md](PROJECT_CONTEXT.md).
