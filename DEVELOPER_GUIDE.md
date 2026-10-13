# Tanglaw-Buhay backend developer guide

This guide describes the Phase 4C vulnerability engine, Phase 5B screening
rules, Phase 6A unified assessments and the authenticated frontend reset. The
screening package separates the four programs from shared parsing and
metadata. The cleanup preserves the rules, output fields and household results.

Tanglaw-Buhay provides decision support. A vulnerability rank indicates review
priority, and a program flag suggests further consideration. Neither is an
official poverty classification, subsidy eligibility decision, approval, or
assigned case.

## 1. Data flow and module responsibilities

The two engines use the same loaded data but calculate independently:

```text
Local Excel files and finalized reference CSVs
                    |
            load_data / verify_data
                    |
          +---------+---------+
          |                   |
   assess_households    screen_households
          |                   |
   16 flags, score,     Four program results,
   coverage and rank   reasons, evidence and warnings
```

| Module | Responsibility |
|---|---|
| `app.py` | Creates Flask, registers authentication/minimal routes and retains a development-only source marker; loads no records. |
| `config.py` | Reads local environment configuration. |
| `routes/auth.py`, `services/auth.py` | Login/logout, CSRF and database-backed session/role checks. |
| `repositories/staff_users.py`, `commands.py` | Staff account SQL and explicitly invoked local administrator bootstrap. |
| `db.py` | Opens a PostgreSQL connection only when explicitly requested; the engines do not call it. |
| `migrations/` | Numbered SQL schema changes; Phase 6B.1 does not connect them to routes or engines. |
| `repositories/dataset_batches.py` | Stores/activates batch snapshots and provides batch-scoped, ordered read queries; callers own transactions. |
| `services/cbms_import.py` | Runs preflight and orchestrates one atomic import; no public route invokes it. |
| `services/active_batch_loader.py` | Reconstructs four sources, three references and separate metadata from one read-only active-batch snapshot; no route invokes it. |
| `routes/main.py` | Provides the minimal protected landing, fixture-source guard, retired staff URLs and public health check. |
| `services/data_loader.py` | Reads sources, validates keys, joins records, and checks mapping inputs. |
| `services/vulnerability/direct.py` | Converts finalized CSV codes into deprivation outcomes. |
| `services/vulnerability/housing.py` | Calculates assets, sanitation, housing materials and overcrowding. |
| `services/vulnerability/employment.py` | Calculates employment, underemployment and working-child indicators, plus assumption counts. |
| `services/vulnerability/education.py` | Calculates school attendance and educational attainment. |
| `services/vulnerability/health.py` | Calculates food insecurity and illness/treatment. |
| `services/vulnerability/scoring.py` | Assembles all 16 indicators once, applies weights, ranks households, and reports aggregate sensitivity. |
| `services/subsidy_screening.py` | Keeps existing imports working; validates joins and runs the four program functions. |
| `services/screening/four_ps.py`, `slp.py`, `spisc.py`, `tupad.py` | Each file contains one program's screening function followed by its evidence helpers. |
| `services/screening/common.py` | Shared code parsing, Section E routing, evidence formatting and final status selection. |
| `services/screening/metadata.py` | Program references, policy notes, worker-code labels and output columns. |
| `scripts/` | Runs aggregate verification without printing names or household identifiers. |
| `tests/` | Checks rules and boundary cases using synthetic examples, not source household records. |

The frontend reset intentionally removes the Phase 6A staff screens. Flask startup
loads no household records and does not run assessment engines. The protected
landing checks the retained development-only source marker; no configuration
switch can expose actual CBMS records. `/test` still responds without PostgreSQL.

`services/assessments.py` remains the backend integration layer: it combines
engines by UUID, preserves complete MPI/four-program payloads and rejects
ambiguous keys, incomplete programs, household-set and survey-year mismatches.
`services/development_data.py` still generates the same 32 synthetic households
for backend/CSV tests. Its obsolete UI workspace builder was removed.
`fixtures/development_metadata.json` retains finalized development-only metadata
and source hashes, with no household records. Actual batch integration must
provide its own finalized references; the fixture is not a production mapping.
The active-batch loader now supplies the same Pandas input dictionaries from
stored CSV responses. Its implementation does not connect the minimal interface
or assessment services to real records; that integration remains pending.

## 2. Loading and joining CBMS data

`load_data()` returns two dictionaries: `datasets` and `references`.

The datasets are `household`, `section_a_e_indiv`, `section_l02_to_l41`, and
`section_l11_to_l14`. The reference dictionaries contain `codebook`,
`mpi_mapping`, and `definitions`.

The loader uses object values and `keep_default_na=False`. This preserves
original column names, empty strings, and literal `NA` responses. Engines
interpret those values locally; they never overwrite the Excel or CSV files.

Joins must use the correct level of identification:

- Household joins use `uuid`.
- Member identities use `uuid` together with `line_number`.
- Section L joins match roster `line_number` to `section_l_line_number`, within
  the same household UUID.

A left join retains a roster member whose Section L record is absent. That
absence is not automatically a negative health response. Duplicate, blank or
missing keys are rejected where they would make an association ambiguous.

`verify_data()` follows four stages: source statistics, reference statistics,
join checks, and indicator input checks. Its report contains counts and issues,
not individual records. Variable expressions such as `J01-J08` are resolved
against definitions; resolving an expression does not invent its scoring rule.

## 3. Deprivation indicators

Every indicator returns one result per household in the original index order:

- `1`: deprived under the approved project rule.
- `0`: not deprived under that rule.
- `None`: relevant information cannot establish the outcome.

These are different from raw questionnaire codes. For example, raw E01=2 means
the person did not work; an Employment Proxy result of 1 means the household
meets the majority-based deprivation rule.

### Direct mappings and housing

Water, tenure, electricity, internet, and public transportation use the finalized
MPI mapping CSV. `build_deprivation_map()` validates the mapping;
`map_code()` interprets a value without changing its source. An unmapped or
invalid response remains unknown. N01 uses `n01_internet_access`, matching the
authorized household CSV. Code, local reference variable names and development
fixtures use this spelling; deprivation codes and outcomes are unchanged.
Development metadata retains its Synthetic Support provenance, with reference
hashes refreshed for the corrected files. Raw source CSVs are unchanged.

The other housing rules are:

- **Assets:** a positive quantity means ownership. The selected communication,
  durable and mobility fields are listed in `housing.py`. A known mobility
  asset, or both a communication and durable asset, establishes non-deprivation.
  Definitely no mobility asset and definitely no owned item in a communication
  or durable group establishes deprivation. Other combinations remain unknown.
- **Sanitation:** combine mapped toilet and sharing outcomes. Any known
  deprivation wins; non-deprivation requires both known zeros.
- **Housing Materials:** combine mapped roof, wall and floor outcomes with the
  same rule. Relevant gaps remain unknown unless a known deprivation wins.
- **Overcrowding:** floor area below `4 × member_count` is deprived. Exactly
  four square metres per person is not deprived. Invalid area/counts are unknown.

### Education and health

| Indicator | Approved rule |
|---|---|
| School Attendance | Any member aged 5-17 with C03=2 establishes deprivation. Reliably no applicable children gives 0. |
| Educational Attainment | Any secondary-complete member aged 19+ establishes 0. HGC groups 7-17 count as complete; group 5 uses the approved age-25 cohort approximation. Groups 1-4 and 6 do not establish completion. Reliably no member aged 19+ gives 1 under this project rule. |
| Food Insecurity | Use J01-J08. Four confirmed Yes answers establish 1. If confirmed Yes plus every unknown still totals less than four, return 0. Otherwise return None. |
| Illness/Treatment | L33=2 establishes 0. For L33=1, a confirmed affected member with L38=2 establishes 1. Zero requires complete consistent evidence that relevant affected members received treatment. |

The food-insecurity cutoff is a pilot raw-count approximation, not official
calibrated FIES measurement. Illness uses provisional L34 Yes/No semantics.
An unaffected L34=2 member with a populated treatment response is contradictory.
A confirmed untreated affected member takes precedence over unrelated gaps.
The code does not infer medical necessity.

### Employment and uncertainty

The adult Employment Proxy is deprived when **strictly more than half** of
applicable members aged 18-65 are classified not working. Underemployment uses
the same strict majority among employed members aged 18-65 who may want more
hours. E15 is documented Synthetic Support. Working Child Not in School uses
any applicable member aged 5-17 with confirmed work and C03=2.

E01 questionnaire applicability requires age 5+ and B06=4/5/7. The approved
MPI assumptions are:

- Applicable missing E01 is assumed not working.
- Adult ages 18-65 with B06=1/2/3 and missing E01 are assumed working for the
  Employment Proxy only.
- That OFW assumption does not supply an E15 response or prove a working child.
- B06=6 retains non-applicability; unresolved routing is not fabricated.

`_contribution_bounds()` and `_majority_outcome()` preserve uncertainty in the
denominator. A positive response contributes +1 to the majority balance and a
negative response -1. An uncertain response may contribute either. An uncertain
member's eligibility may also contribute zero. The outcome is assigned only
when the majority remains the same across the possible completions.

For a simple example, one working and one nonworking adult is exactly 50%
nonworking, so the Employment Proxy is 0. Two nonworking adults and one working
adult exceed 50%, so it is 1. Do not drop uncertain members to simplify the ratio.

Education reuses basic age/roster helpers from `employment.py`; it does not run
the employment assumptions.

## 4. Weighted vulnerability score and ranking

`assess_households(datasets, mapping)` assembles the indicators and calls
`score_indicators()`. The output is a household-level DataFrame, including the
original `uuid`, all flags, score, rank, coverage and assumption information.

| Dimension | Indicators | Weight per indicator |
|---|---:|---:|
| Education | 2 | 1/8 |
| Health and Nutrition | 2 | 1/8 |
| Housing, Water and Sanitation | 9 | 1/36 |
| Employment | 3 | 1/12 |

Each dimension contributes exactly 25%; weights sum exactly to 1.

```text
Score = 100 × sum(weight of each deprived indicator)
```

Fractions preserve exact scores and ties internally. Numeric output fields are
converted to floats after the calculation. Unknown flags remain `None` but
contribute zero points; weights are not redistributed. Every household receives
one score and rank, including households with unknown indicators.

`known_weight` includes known zeros and ones. `coverage_pct` is
`100 × known_weight`. `Complete Data` means every indicator outcome is
determined, not that every raw response was observed. An approved imputation can
still be involved. At least one unknown gives `Needs Data Review`.

As a small illustrative example, deprivation in one education indicator and one
housing indicator contributes `100 × (1/8 + 1/36) = 15.2778` points. A separate
unknown contributes zero but remains listed as unknown.

Ranks use descending exact scores and competition ties: scores 25, 25 and 12.5
receive ranks 1, 1 and 3. Results retain household input order; the DataFrame is
not rearranged into ranking order.

`sensitivity_summary()` reports aggregate uncertainty if unknown flags could be
zero or one. Its conservative bounds are not predictions or a second user-facing
ranking. They do not measure uncertainty from E01 imputation.

## 5. The four subsidy functions

`screen_households(datasets, survey_year=2022)` validates keys, joins Section L,
groups members, and calls each program function. It returns four rows per
household, one for each program. The survey year is explicit; recorded ages are
never advanced to a later year.

| Implementation | CBMS variables | For Review | Needs Data Review without a supported trigger |
|---|---|---|---|
| `screening/four_ps.py`: `screen_4ps()` | A07 age; household L01/L06; joined L07, sex A05 and member line. | Any age 0-18, or household L06=1 without an L01=2 routing conflict. Provisional L07 attribution requires household corroboration and a joined female age 10+. | Missing roster/age or unresolved relevant pregnancy evidence, including skip and member conflicts. |
| `screening/slp.py`: `screen_slp()` | A07, B06, E01, E18; optional E11 and C07/C08 TVET evidence. | EF: age 18+, applicable observed E01=2. MD: age 16+, applicable observed E01=1 and E18=3/4/5/6. Both pathways can coexist. | Either relevant pathway remains unknown because age, routing or observed work evidence cannot establish it. |
| `screening/spisc.py`: `screen_spisc()` | A07; household L15 and joined member L16/line for optional PWD context. | Any age 60+. Disability support requires the senior's own joined L16=1 and household L15=1. | No roster or an unknown age. Disability gaps/warnings do not change the age-based status. |
| `screening/tupad.py`: `screen_tupad()` | A07, B06, E01, E11; E18 checks for contradictory work details. | Age 18+ with applicable observed E01=2, approved assumed nonwork from missing E01, or valid observed irregular E11=2/3 work. | Unresolved applicable age/work/routing, invalid codes, or missing E01 with populated job details. B06=6 remains excluded. |

The paths above are relative to `services/`. Each file starts with its main
screening function. Metadata is in `screening/metadata.py`; shared code/value
interpretation and result formatting are in `screening/common.py`. Callers can
continue importing all four functions and `screen_households` from
`services.subsidy_screening`.

E18=3 means self-employed without paid employees; 4 means employer in an own
family-operated farm/business; 5 and 6 mean paid and unpaid family-enterprise
work. E11=2 means short-term/seasonal/casual work; 3 means irregular work for
different employers/customers.

SLP uses observed responses. TUPAD derives its separately approved assumptions
from raw fields; it does not import MPI classifications, scores or ranks. The
MPI adult range is 18-65, while the TUPAD pilot uses age 18+ without an upper limit.
Synthetic E15 never supplies a subsidy trigger.

Program-local helpers build the associated evidence: `_member_pregnancy()` in
`four_ps.py`, `_member_disability()` in `spisc.py`, and `_nonwork_evidence()` in
`tupad.py`. SLP keeps its two pathways explicit and uses `_md_trigger()` only for
observed enterprise-work validation. `all_conditions()` in `common.py` combines
True/False/None: a known False excludes, all True confirms, otherwise it stays
unknown. It must not be replaced with ordinary `all()`, which treats None as False.

## 6. Status, evidence and warnings

- **For Review:** supported observed evidence or an approved labeled pilot
  assumption meets a project trigger. This is a suggestion, not an assigned case.
- **No Historical CBMS Trigger:** enough relevant information establishes that
  no project trigger applies. This is not present-day ineligibility.
- **Needs Data Review:** relevant missing/conflicting CBMS information prevents
  determining a trigger. Missing official income, pension or beneficiary
  documents alone do not cause this status.

`trigger_reasons` explains the trigger. `supporting_evidence` retains source
values, source dataset, member line where applicable, and observed/provisional
or assumed evidence labels. `triggering_member_line_numbers` identifies actual
triggering members; a household-only pregnancy signal can have no attributed
member line.

An evidence item with `evidence_role="supporting_context"` does not itself create
a review flag. For example, household disability or assumed overseas work can
be supporting context while the program status is No Historical CBMS Trigger.

`data_quality_warnings` preserves contradictions and attribution gaps. A valid
independent trigger can coexist with a warning. SPISC disability warnings do not
replace its age-based status. `key_unverified_requirements` remains concise and
informational; no follow-up tasks are created automatically.

## 7. Where to change rules and how to verify changes

Find indicator definitions in their relevant vulnerability module, weighting and
ranking in `scoring.py`, and each program's criteria in its `services/screening/`
file. Shared result/status formatting is in `common.py`; references and notes are
in `metadata.py`.
Keep evidence construction synchronized with any explicitly approved future
rule change. Do not edit finalized mappings or raw files to force a result.

From the project directory in PowerShell:

```powershell
.\.venv\Scripts\python.exe -B -m unittest discover -s tests
.\.venv\Scripts\python.exe -B scripts\verify_data_loading.py
.\.venv\Scripts\python.exe -B scripts\verify_vulnerability.py
.\.venv\Scripts\python.exe -B scripts\verify_subsidy_screening.py
```

The tests cover boundaries, missing answers, skips, contradictions, joins,
weights, ties and engine independence. Verification scripts use the local
datasets and print aggregate results only. Their fixed 19,304-record checks and
historical comparisons belong to this pilot batch, not a general future-import
validation system.

To call the services directly without Flask or PostgreSQL:

```python
from services.data_loader import load_data
from services.vulnerability.scoring import assess_households
from services.subsidy_screening import screen_households

datasets, references = load_data()
assessment = assess_households(datasets, references["mpi_mapping"])
screening = screen_households(datasets, survey_year=2022)
# These DataFrames contain private household information; keep them local.
```

For a behavior-preserving refactor, run the tests before and after and compare
all household rows, flags, ranks, evidence and warning payloads. Equal totals
alone cannot prove that the same households received the same results. Private
comparison files belong in Git-ignored storage and must not be committed.

## 8. Limits to explain during the defense

The project preserves documented pilot adaptations: cohort-based HGC group 5,
asset crosswalks, raw-count FIES, E01/OFW assumptions, synthetic E15, and
provisional L07/L16/L34 coding. The original questionnaire describes those
Section L person fields as line numbers; their apparent Yes/No recoding is not
independently confirmed by PSA export documentation.

E03 job-attachment routing is absent from the working export. SLP cannot declare
missing downstream work fields a verified negative solely from E01=2. Policy
notes in `screening/metadata.py` also retain the unverified full-text limitations
for the newer SPISC and TUPAD issuances.

Historical conditions do not establish current eligibility. Zero-point unknowns
can underestimate vulnerability, and approved employment assumptions can affect
ranking. These are existing limitations, not rules changed by this refactor.

## 9. Minimal staff interface after the frontend reset

Run `python app.py` locally and open http://127.0.0.1:5000/login. The server binds
to loopback with debug disabled. Authentication requires the configured signing
key and existing staff accounts (section 13); no original datasets are read.

| Route | Current behavior |
|---|---|
| `/` | Protected landing showing System Administrator or LGU Staff and a CSRF-protected Sign Out form. |
| `/login` | Minimal sign-in form; successful/already authenticated login redirects to `/`. |
| `/logout` | Sign-out confirmation; only a CSRF-protected POST ends sessions. |
| `/test` | Public JSON health check, independent of records/database availability. |
| `/households`, `/assessments`, and their detail paths | Anonymous users redirect to login; authenticated requests return HTTP 410 with a generic retirement message. No identifiers/responses are echoed. |

The old staff blueprints, sidebar, records/assessment templates, shared UI macros,
CSS, JavaScript, favicon, presentation helpers and pagination were intentionally
removed on `dev/frontend-reset`. Their prior implementation remains in Git at
`9a6b24c`. `templates/landing.html` and existing minimal auth templates contain
no new dashboard, panels, design system or record summaries.

The development boundary now holds only a source-kind marker, not a calculated
workspace. A non-fixture marker blocks the landing with 403. Authenticated real
CBMS access still requires a separately approved protected repository/view flow;
do not remove the guard to connect records. Backend engines, fixtures, validation,
importer, SQL migrations and repositories are retained.

Future review requirements remain per household/program: continuation by
appropriately authorized staff, Not Yet Reviewed / In Review / Completed,
Further Verification Recommended / No Further Action, and justification when
starting without a historical trigger. These workflows remain unimplemented;
review decisions must never overwrite system scores or screening findings.

`tests/test_assessments.py` retains backend output/identity/immutability checks.
`tests/test_frontend_reset.py` checks the minimal routes and real auth service
with generated accounts and mocked account SQL, including role checks and
revocation. Removed UI behavior is no longer asserted. Existing PostgreSQL auth
tests now expect the minimal landing and retired URLs.

For verification with a strict no-database-change boundary, run the focused
`test_frontend_reset.py` suite. For the broader suite, set
`TANGLAW_TEST_DATABASE_URL` to an empty string only in the test process and block
`psycopg.connect`; database integration cases then skip. Record those skips
explicitly, rather than treating them as fresh PostgreSQL verification.

## 10. Phase 6B.1 database foundation

`migrations/0001_database_foundation.sql` creates the eight approved tables.
Every source/reference row belongs to a dataset batch. Household UUIDs and
member line numbers are explicit join keys; Section L health rows reference the
matching roster member. Child-death rows reference only their household and use
their source-row number as a separate key because a household can have multiple
death rows without a member identifier. A partial unique index permits at most
one active batch, and an active batch must be marked validated.

The four source tables hold original response objects in JSONB. Reference CSV
rows also hold string-valued response objects. Order by `source_row_number` to
reconstruct source row order; `dataset_batches.source_columns` holds each
dataset's ordered column-name array because JSONB object keys have no column
order. Keep blank strings, literal
`NA`, original identifiers and documented synthetic/pilot provenance distinct.
This migration does not import files, enforce staff permissions or expose data in
the UI; those steps belong to later milestones.

Apply the numbered SQL file once to an empty development database using a local
PostgreSQL client. Use `-1` so all DDL commits or rolls back together:

```powershell
psql -X -1 -v ON_ERROR_STOP=1 -d tanglaw_buhay_dev -f migrations/0001_database_foundation.sql
```

The application continues to use `DATABASE_URL` from local `.env` through
`config.py` and `db.py`; never commit the URL or private datasets. For constraint
verification, create a separate local database named `tanglaw_buhay_test`, set
`TANGLAW_TEST_DATABASE_URL` in the same Git-ignored `.env`, then run:

```powershell
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -p test_database_schema.py
```

The test checks synthetic joins, duplicate keys, active-batch uniqueness and
preservation of blank/`NA`/CSV strings. It creates a temporary schema and rolls
back its DDL and rows. It skips when the test URL is unset and refuses database
names other than `tanglaw_buhay_test` or non-local hosts. A skip does not verify
the migration.

## 11. Phase 6B.2 Task 1: CSV preflight

`services.cbms_preflight.validate_csv_bundle(data_dir)` reads the four
`raw/<dataset>.csv` sources and the existing three finalized reference filenames
under `reference/`. It returns `passed`, aggregate `errors`/`warnings`, source and
reference statistics, join counts and the indicator count. Each issue has a
fixed `source`, `code` and `count`; uploaded values, paths and parser messages
are excluded. This service has no database or UI dependency.

The UTF-8 reader accepts an optional BOM, preserves column names, row order and
all parsed response strings (including leading zeros, empty strings, `NA` and
`0`), and rejects duplicate/blank headers and malformed or uneven rows. Existing
key, join, reference and indicator helpers are reused. Finalized definitions
supply the declared source-column contract; a separate required engine-column
contract reuses the existing asset/FIES field lists and requires the other
engine inputs even when their uploaded declarations are removed. Existing
direct mapping compilers check the rules consumed by the engines. The Excel
loader is unchanged.

The independent contract includes household L01/L06 pregnancy and L15 disability
context plus member A05 sex, preserving the inputs used by 4Ps/SPISC even if their
reference declarations are removed. Screening rules are unchanged.

Missing files/required columns, blank or duplicate keys, orphan records, invalid
integer member lines, integer-key collisions and invalid references/indicator
inputs are blocking errors. Joins compare original key text exactly; mismatched
line-number spellings are not silently reconciled. Repeated household UUIDs in
child-death records are valid, including a header-only child-death file. A
nonempty household dataset with an entirely empty roster is a blocking error.
Partial household roster gaps, missing individual Section L records, identical
child-death rows, additional undefined columns and blank provenance produce
warnings without rewriting responses or inferring negative evidence.

`passed` requires no blocking errors, including reference/indicator checks; it
does not reuse `loading_and_joins_passed` as complete validation. No fixed pilot
row totals are enforced. Preflight does not certify source authorization or
survey-year provenance, calculate assessments, or insert/activate a dataset.
Import metadata, insertion and staff authorization remain separate tasks.

Run the focused checks using only generated synthetic CSVs:

```powershell
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -p test_cbms_preflight.py
```

## 12. Phase 6B.2 Task 2: transactional backend importer

`services.cbms_import.import_csv_bundle()` requires a CSV directory and explicit
`survey_year=2022`, `municipality="Pozorrubio"` and nonempty `source_provenance`
metadata. It uses the existing `db.get_connection()`/Flask configuration by
default; a supplied connection factory must return a new connection owned by
the importer. No upload route or admin/staff authorization is implemented here;
keep this backend function unexposed until enforced admin access is available.

Preflight and import share one parsed snapshot through `load_csv_bundle()`.
Those private frames must stay inside the backend. Original CSV strings are
stored in JSONB; only relational member line columns become integers. One-based
data-row positions and ordered source columns preserve row/column order.
Batch provenance records caller-declared context and each parsed file's name,
SHA-256 and row count, including finalized reference and synthetic provenance.
This does not certify dataset authorization or infer survey-year provenance.

One transaction locks the batch table to serialize importers, creates a pending
inactive batch, inserts all seven source/reference tables, verifies row counts,
then replaces the active pointer and commits. Previous survey/reference rows are
retained. Validation errors open no database connection. SQL/count/activation
failures roll back the entire new batch and any active-pointer change; no failed
batch is retained. Results contain only `imported`, technical `batch_id`, the
aggregate `validation` report and a fixed `error` code, never SQL error details.

Insertion uses generator-backed psycopg `executemany()` per table, avoiding a
second full list of row parameters. The installed psycopg/libpq client supports
automatic pipeline mode for these calls, reducing repeated network round trips.
The CSV frames themselves are loaded in memory; this is not a streaming reader.

`tests/test_cbms_import.py` uses only generated CSVs and isolated schemas in local
`tanglaw_buhay_test`. It verifies committed storage, failed preflight, partial
writes, count mismatches, activation rollback, concurrent activation and file
snapshot consistency; schemas are dropped after each test. Source-file retention,
an admin workflow and real-data integration remain separately scoped work.

Run the importer checks with the configured local test URL:

```powershell
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -p test_cbms_import.py -v
```

The PostgreSQL tests must execute without skips before marking Task 2 verified.

### One-time controlled 2022 import runner

`scripts/controlled_cbms_import_2022.py` is the reviewed operational wrapper for
the owner's separately authorized first local import. It calls the existing
transactional importer; it does not add routes or change calculations. The first
import completed on 2026-10-13 as validated active batch `1`, with all seven
expected row counts, zero orphan relationships and matching file hashes.

The runner requires an explicit `--check` (read-only pre-import safeguards) or
`--execute` (one import attempt) argument. Both modes require an empty dataset
boundary and therefore stop on the already imported database. It verifies the
local `tanglaw_buhay_dev` target, `public` schema, migration structures and CSV
preflight; execution rechecks emptiness under the import lock. It checks source
and configuration preservation and administrator session authorization without
changing staff accounts. It never retries an import automatically.

This script records the original authorization and fixed pilot counts; retaining
it in Git does not authorize another import. Private `.env`, CSVs and database
contents remain outside Git. No credentials or source household records are
embedded in the runner.

## 13. Phase 6B.3 Task 1: staff authentication

`0002_staff_authentication.sql` adds only `staff_users`, separate from CBMS
records. Roles are `system_administrator` and `lgu_staff`. Werkzeug scrypt stores
salted password hashes; login failures use one generic credential message.
The application guard requires login for existing and future application routes,
except `/login`, static assets and `/test`. Both roles can access the minimal
protected landing; record/assessment pages are retired. `services.auth.admin_required` checks the current database role
for future import/account routes; those workflows are not implemented yet.

Flask signs the cookie using a private `SECRET_KEY` (no default); staff access
returns 503 if it is missing or shorter than 32 characters. Generate a random
key and save it in Git-ignored `.env`. This local command replaces only its key
setting without printing the result; key rotation invalidates existing cookies:

```powershell
.\.venv\Scripts\python.exe -B -c "from pathlib import Path; import re, secrets; p = Path('.env'); s = p.read_text(encoding='utf-8') if p.exists() else ''; s = re.sub(r'(?m)^SECRET_KEY=.*(?:\n|$)', '', s); p.write_text(s.rstrip() + '\nSECRET_KEY=' + secrets.token_urlsafe(48) + '\n', encoding='utf-8')"
```

Cookies default to Secure, HttpOnly and SameSite=Lax. For the local loopback
HTTP server only, set `SESSION_COOKIE_SECURE=false` in private `.env`; deployment
requires HTTPS and the secure default. Sessions expire eight hours after login,
without extending on page visits. Only account ID, session version, issuance
time and CSRF state are stored, never passwords or survey data. Each protected
request rechecks the account's activity, role and session version. Logout revokes
all sessions for that account, including replayed cookies. Login clears previous
session state and rotates CSRF; login/logout POSTs require the session token.
See the [Flask security guidance](https://flask.palletsprojects.com/en/stable/web-security/)
and [Werkzeug hashing documentation](https://werkzeug.palletsprojects.com/en/stable/utils/#werkzeug.security.generate_password_hash).

After approval, apply migration 0002 **once** to the development database, with a
prompted PostgreSQL password. Startup never applies migrations automatically:

```powershell
& 'C:\Program Files\PostgreSQL\18\bin\psql.exe' -X -h localhost -U tanglaw_final -W -d tanglaw_buhay_dev -1 -v ON_ERROR_STOP=1 -f migrations/0002_staff_authentication.sql
.\.venv\Scripts\python.exe -B -m flask --app app create-admin
```

The bootstrap command refuses the old, test or remote database and requires an
empty staff account table. It prompts for a username and a confirmed password
(12-1024 characters), refuses terminal input that could echo the password, and
stores only its hash. Usernames use 3-64 lowercase letters/digits/dot/dash/underscore
and are normalized at creation/login. There are no password command arguments,
default credentials, automatic accounts or web account-management screens.
Open `/login` to sign in and `/logout` to sign out; the landing also provides
Sign Out directly. The old sidebar and staff screens are intentionally retired.

Run synthetic authentication verification with the local test URL configured:

```powershell
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -p test_auth.py -v
```

Tests apply migration 0002 only in disposable schemas in `tanglaw_buhay_test`,
create generated test credentials, and remove their schemas. They check roles,
CSRF, sessions, revocation, bootstrap and the fixture guard. Run the full suite
as well. Neither development nor old databases, private `.env`, source files,
assessment engines or importer behavior are changed by authentication tests.
Real CBMS access, staff account lifecycle, review permissions and deployment
hardening remain separately scoped work.

## 14. Phase 6B.4 Task 1: read-only active-batch loader

`services.active_batch_loader.load_active_batch(connection_factory=get_connection)`
returns `(datasets, references, metadata)`. The default connection uses the
existing Flask `DATABASE_URL`; an explicit factory must return a new, idle,
caller-owned psycopg connection. The service owns its lifetime and begins a
`REPEATABLE READ, READ ONLY` transaction before reading any batch data. All seven
frames therefore come from one consistent snapshot, even if a separately
authorized importer activates a different batch concurrently.

`repositories.dataset_batches.find_active_batches()` selects only rows with
`is_active = true` and `validation_status = 'validated'`, reading at most two so
an ambiguous state can be rejected. The service requires one batch with a
positive ID, survey year 2022, municipality Pozorrubio and complete imported
column/provenance metadata. It does not hardcode batch ID 1 or the pilot counts.
`iter_batch_records()` uses only the repository's trusted table mapping and a
parameterized batch ID, with `ORDER BY source_row_number ASC` for every table.

The service rebuilds `dtype=object` DataFrames from the original JSONB response
objects, with columns explicitly ordered by `source_columns`. It uses response
UUIDs and member line strings, preserving leading zeros, blanks, literal `NA`
and all other strings; it does not replace them with relational integer keys.
Source positions must be contiguous from 1, response keys must match the recorded
columns, every cell must remain a string and counts must match file provenance.
Header-only datasets retain their columns. Missing cells or rows are rejected,
never filled in. Metadata is returned separately with `batch_id`, `survey_year`,
`municipality`, `validation_status`, `is_active`, `source_columns` and
`source_provenance`, including original file names, counts and SHA-256 hashes.

`ActiveBatchError` exposes only fixed codes: missing/invalid active metadata,
invalid records, count mismatch, non-idle connection or unavailable database.
Database exception details are suppressed. Frames and provenance are private
backend objects, not a public response or an authorization boundary. The loader
does not read source files, re-run preflight, score households, screen subsidies,
create reviews or perform database writes. It trusts the validated import's
format; independent schema/join checks and file comparisons belong to verification.
Full-batch assessment integration and protected views remain pending; the
fixture-only route guard is unchanged.

Focused synthetic tests require no database:

```powershell
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -p test_active_batch_loader.py -v
```

All 14 focused tests passed, including complete synthetic engine-output
comparisons. On 2026-10-13, read-only verification of local `tanglaw_buhay_dev`,
schema `public`, active batch 1 confirmed all seven expected counts, original
row/column order and exact response equality with the local CSV snapshot.
PostgreSQL reported read-only/repeatable-read execution; existing structural
checks passed with 16 indicator inputs and zero orphan/duplicate join keys.
The broader suite ran 181 tests: 159 passed and 22 database integration cases
were deliberately skipped with database connections blocked. No real-batch
assessment or screening was performed and no database, source or configuration
files were modified by these checks.

## 15. Phase 6B.4 Task 2A: full-batch assessment execution

`scripts/verify_active_batch_assessments.py` is an explicitly invoked, read-only
verification runner for active batch 1, 2022 Pozorrubio. It uses
`load_active_batch()` with the existing `connect_verified()` loopback/development
database guard from the controlled-import script. It never calls that script's
import runner or write-capable factory. All loading uses a read-only snapshot;
engine execution then occurs in memory with the database connection closed.

```powershell
.\.venv\Scripts\python.exe -B scripts\verify_active_batch_assessments.py
```

This command performs a full-batch calculation and should be run only for an
explicitly authorized assessment verification. It calls `assess_households()`
once and `screen_households()` once, then uses `combine_assessments()` to verify
unified household/program coverage without recalculating either engine. The
verifier checks household identity/order, all 16 flags, weighted scores and
coverage, known/unknown counts, quality labels, exact competition ranks,
historical year, program uniqueness/statuses and evidence containers. It also
checks trigger/status consistency and fingerprints the source frames before and
after execution. Its additional score/rank arithmetic audits emitted results;
it does not re-run indicators or define a new scoring engine.

Console output contains only progress and aggregates: descriptive score bins,
quartiles/mean/range, coverage, rank/tie statistics, per-indicator outcomes,
existing assumption/conflict counts, program/status totals, warning-code counts,
missing source responses, timings and optional Windows process memory. Failures
report only the execution phase and a fixed verification code or exception type;
private exception text and evidence are suppressed. No household results are
printed or exported, and no assessment data is persisted. The script stops on
failure without retries or engine redesign.

### Verified execution on 2026-10-13

Local `tanglaw_buhay_dev`, schema `public`, batch 1 produced **19,304 unique MPI
results**, **77,216 household/program results** and **19,304 unified assessments**.
Every household had exactly one result for each of 4Ps, SLP, SPISC and TUPAD, with
survey year 2022. All consistency checks passed and source frames were unchanged.

| MPI measure | Aggregate result |
|---|---|
| Score min / p25 / median / p75 / max | 0 / 0 / 5.5556 / 12.5 / 56.9444 |
| Mean score | 8.3175 |
| Score bins [0,10], (10,20], (20,30], (30,40], (40,50], (50,60] | 12,502 / 4,682 / 1,623 / 406 / 83 / 8 |
| Score bins above 60 | 0 |
| Coverage min / mean / median / max (%) | 79.1667 / 99.8982 / 100 / 100 |
| Complete Data / Needs Data Review households | 19,093 / 211 |
| Unknown indicator outcomes | 212: 210 households with one unknown, one household with two |
| Ranked households / distinct scores | 19,304 / 37 |
| Competition-rank min / max | 1 / 14,366 |
| Tied score groups / households in ties | 35 / 19,302 |

Score bins are descriptive summaries, not poverty or eligibility thresholds.
The maximum competition rank is below the household count because ties occupy
shared positions. Unknown flags remain unknown, contribute zero points under
the existing rule and can underestimate vulnerability; approved assumptions can
also affect results.

| Program | For Review | No Historical CBMS Trigger | Needs Data Review |
|---|---:|---:|---:|
| 4Ps | 12,147 | 7,150 | 7 |
| SLP | 12,770 | 2,385 | 4,149 |
| SPISC | 6,339 | 12,965 | 0 |
| TUPAD | 16,612 | 2,692 | 0 |

There were 18,762 households with at least one review signal and 4,156
household/program results needing data review. Informational warnings remained
visible: 43 pregnancy warning events across 25 4Ps households and two disability
warning events across two SPISC households. These warnings are separate from
status labels and do not create staff reviews. Source gaps included 51,068
members without health rows and 28,544 blank/literal-NA E01 responses; recorded
ages had no blank/literal-NA responses. A missing E01 response is interpreted only
by the existing program-specific routing/assumption rules, not automatically
counted as unresolved or negative evidence by this report.

Observed timings were 10.580 seconds loading, 9.953 seconds MPI, 6.073 seconds
screening and 6.434 seconds output verification, with **34.016 seconds total**
including initial source fingerprinting. Windows process working set was
464.36 MiB at reporting and **466.64 MiB peak**. This measures the Python process,
not PostgreSQL or total system memory; timings and memory vary by machine/run.

All **65 focused synthetic tests** passed with database connections blocked:
scoring, screening, unified assessments and six new aggregate-verifier tests.
No PostgreSQL integration tests or additional real runs were performed for this
task. Engines, private sources, migrations and UIs were unchanged. Assessment
persistence, authorization integration and real-data views remain pending.

## 16. Phase 6B.4 Task 2B: targeted real-data sanity check

On 2026-10-13, a read-only check of the same local active batch 1 reproduced the
three Task 2A findings. Household outputs had intentionally not been persisted,
so the check called only the existing TUPAD/SLP functions and assembled MPI
indicator flags needed for coverage. It did not repeat MPI scores/ranks, 4Ps,
SPISC, imports or full-batch verification infrastructure. Only aggregates were
printed; source-frame fingerprints remained unchanged. The check took 28.69
seconds including loading. Three small synthetic checks also confirmed that
missing E01 with populated job details is not imputed by TUPAD, remains unresolved
for SLP, and observed nonwork at age 17 leaves SLP's MD pathway unresolved under
the existing approved rule. No defect or blocker was found.

### TUPAD evidence behind the 16,612 review signals

| Exclusive household evidence group | Households |
|---|---:|
| Observed evidence only | 8,560 |
| Assumed nonwork only | 3,155 |
| Both observed and assumed nonwork | 4,897 |
| Total For Review | 16,612 |

The observed union covered 13,457 households: recorded nonwork supported 9,571
households (13,950 member evidence events), and recorded irregular work supported
6,809 households (8,609 events). These household groups overlap. Assumed nonwork
supported 8,052 households (8,699 member evidence events), including the mixed
group above. Member events are not additional household review counts.

Every assumed-nonwork trigger had age 18+, applicable B06=4/5/7, blank/literal-NA
E01 and absent E11/E18 job details, exactly as the existing pilot rule requires.
Observed nonwork used E01=2; irregular work used observed E01=1 and E11=2/3.
Irregular work is labeled observed evidence, not an imputation. Assumed overseas
work appeared as supporting context in 1,899 households and supplied no nonwork
trigger. B06=6 remains non-applicable. Missing E01 with populated job details
remains conflicting evidence and is not assumed nonwork. None of these signals
establishes official unemployment or present-day TUPAD eligibility.

### SLP uncertainty behind the 4,149 Needs Data Review results

| Exclusive household uncertainty group | Households |
|---|---:|
| Applicable missing E01 only | 3,447 |
| Observed nonwork at ages 16-17, unresolved MD only | 309 |
| Both causes | 393 |
| Total Needs Data Review | 4,149 |

Across overlapping causes, applicable missing E01 affected 3,840 households
(4,091 unresolved members), and the age-16/17 observed-nonwork MD case affected
702 households (754 unresolved members). SLP uses observed work responses and
does not borrow MPI/TUPAD nonwork assumptions. For the second cause, EF requires
age 18+; MD requires observed work plus a valid enterprise-worker class. The
approved `_md_trigger()` deliberately retains uncertainty when applicable
observed work does not establish MD; the absent E03/job-attachment export does
not prove a negative downstream response from E01=2. This is the existing
conservative rule, not a newly introduced criterion.

In these 4,149 households, no additional uncertainty causes were found: no
missing/invalid age or B06 routing, invalid E01, or observed-work/invalid-E18
cause contributed. No members with B06-skip/job-response conflicts were found
in this subset; none of its unresolved members had missing E01 with populated
job details. These absence findings apply to the inspected subset; existing
conflict handling remains intact.

### Why MPI coverage remains high

Coverage counts the weights of determined household indicators. It does not
measure the percentage of completed survey cells or independently observed
responses. There were 18,195 households with recorded L33=2 (no illness); all
14,961 households without any Section L health row were in that group. The
approved L33=2 rule determines their illness indicator. Other relevant missing
or contradictory illness evidence can still remain unknown.

Approved E01 nonwork assumptions were recorded for 8,442 applicable members in
7,823 households (MPI audit scope ages 5-65). The adult Employment Proxy also
recorded 2,120 assumed-working OFW members aged 18-65 under its separate rule.
Verified skips, known exclusions and bounded household outcomes can determine
an indicator despite other missing responses. These assumptions remain labeled
and original E01 responses are unchanged. MPI classifications do not supply
subsidy triggers; TUPAD applies its separate approved assumptions independently.

| Unknown MPI indicator | Unknown household outcomes |
|---|---:|
| Food Insecurity | 60 |
| Illness/Treatment Proxy | 1 |
| Sanitation | 2 |
| Housing Materials | 8 |
| Working Child Not in School | 141 |
| Total | 212 |

These unknown outcomes occurred in 211 households: 210 had one unknown and one
had two. The other 19,093 had determined outcomes for all 16 indicators. Applying
the existing indicator weights to those unknown counts reproduces average
coverage **99.8981932357%**. High coverage is consistent with the approved rules;
it does not establish complete observed data, validate the pilot assumptions,
or remove the documented zero-point-unknown/provisional-coding limitations.
No rules, weights, thresholds, source responses or staff workflows were changed.
