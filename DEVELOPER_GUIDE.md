# Tanglaw-Buhay backend developer guide

This guide describes the Phase 4C vulnerability engine, Phase 5B screening
rules, Phase 6A unified assessments and current authenticated staff assessment
views. The screening package separates the four programs from shared parsing
and metadata. Technical origins, rules and limitations remain here for
developers and capstone methodology; routine staff content follows
[UI_SPEC.md](docs/UI_SPEC.md).

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
| `app.py` | Creates Flask, registers protected routes and retains a development-only source marker; loads no records at startup. |
| `config.py` | Reads local environment configuration. |
| `routes/auth.py`, `services/auth.py` | Login/logout, CSRF and database-backed session/role checks. |
| `repositories/staff_users.py`, `commands.py` | Staff account SQL and explicitly invoked local administrator bootstrap. |
| `db.py` | Opens a PostgreSQL connection only when explicitly requested; the engines do not call it. |
| `migrations/` | Numbered SQL schema changes; Phase 6B.1 does not connect them to routes or engines. |
| `repositories/dataset_batches.py` | Stores/activates batch snapshots and provides batch-scoped, ordered read queries; callers own transactions. |
| `repositories/assessment_runs.py` | Stores batch-scoped runs/findings and explicit completed-run selections through approved operations; the protected prioritization page uses completed reads through the query service. Routes never invoke storage operations. |
| `services/cbms_import.py` | Runs preflight and orchestrates one atomic import; no public route invokes it. |
| `services/active_batch_loader.py` | Reconstructs four sources, three references and separate metadata from one read-only active-batch snapshot; no route invokes it. |
| `services/assessment_queries.py` | Resolves the explicitly designated completed active-batch run and retrieves filtered summaries or one household's stored findings through read-only snapshots. |
| `routes/assessments.py`, `services/prioritization.py` | Serve protected Prioritization and Assessment Details without engine execution. |
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

The frontend reset removed the Phase 6A staff screens. Later tasks rebuilt
protected Prioritization and Assessment Details with read-only PostgreSQL
queries. Flask startup loads no household records and does not run assessment
engines. The public `/test` route retains its fixture-only boundary and responds
without PostgreSQL; no configuration switch exposes actual CBMS records there.

`services/assessments.py` remains the backend integration layer: it combines
engines by UUID, preserves complete MPI/four-program payloads and rejects
ambiguous keys, incomplete programs, household-set and survey-year mismatches.
`services/development_data.py` still generates the same 32 synthetic households
for backend/CSV tests. Its obsolete UI workspace builder was removed.
`fixtures/development_metadata.json` retains finalized development-only metadata
and source hashes, with no household records. Actual batch integration must
provide its own finalized references; the fixture is not a production mapping.
The active-batch loader supplies the same Pandas input dictionaries from stored
CSV responses. Protected assessment routes instead use narrow, read-only query
services to access the selected completed run and relevant source evidence.

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

The [2022 PSA Household Profile Questionnaire](https://psa.gov.ph/system/files/cbms/2022_Household_Profile_Questionnaire.pdf)
asks B06 only at age 15+ (printed page 5), while Section E starts at age 5 and
separately specifies B06=4/5/7 (printed page 9). B06's navigation to B09/Section C
does not replace Section E's own instructions. The corrected MPI treatment is:

- Preserve applicable E01=1 as recorded work and E01=2 as recorded nonwork.
- Applicable missing E01 is unknown, never automatically nonworking. Household
  bounds still return 0/1 when the evidence conclusively establishes the outcome.
- For ages 5–14, B06 is outside its questionnaire age scope. The implementation
  uses Section E's age scope and observed E01 without requiring/inventing B06=7.
  Missing E01 remains unknown; it is not a proven employment skip. Unexpected
  B06 values below age 15 are retained in source data but do not gate child work.
- Adult ages 18-65 with B06=1/2/3 and missing E01 are assumed working for the
  Employment Proxy only.
- That OFW assumption does not supply an E15 response or prove a working child.
- At age 15+, B06=1/2/3/6 establishes a Section E skip; B06=4/5/7 permits it.
  Missing/invalid B06 leaves applicability unresolved. B06=6 keeps its adult
  exclusion and existing OFW conflict handling remains unchanged.
- Reliably no applicable members retains 0; an absent roster remains unknown.

The under-15 treatment reconciles Section E's age scope with B06's age skip; it
does not assign an overseas/resident classification. This correction affects MPI
only. SLP/TUPAD routing and their separate approved assumptions are unchanged.

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
ranking. They do not measure uncertainty from the retained OFW proxy or from
historical nonworking E01 imputation in stored run 1.

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
asset crosswalks, raw-count FIES, the adult OFW proxy, synthetic E15, and
provisional L07/L16/L34 coding. The original questionnaire describes those
Section L person fields as line numbers; their apparent Yes/No recoding is not
independently confirmed by PSA export documentation.

E03 job-attachment routing is absent from the working export. SLP cannot declare
missing downstream work fields a verified negative solely from E01=2. Policy
notes in `screening/metadata.py` also retain the unverified full-text limitations
for the newer SPISC and TUPAD issuances.

Historical conditions do not establish current eligibility. Zero-point unknowns
can underestimate vulnerability, and approved employment assumptions can affect
ranking. Historical stored MPI run 1 retains its former nonworking E01 assumption;
the corrected in-memory logic and pending persistence are documented in section 20.

## 9. Minimal staff interface after the frontend reset

Run `python app.py` locally and open http://127.0.0.1:5000/login. The server binds
to loopback with debug disabled. Authentication requires the configured signing
key and existing staff accounts (section 13); no original datasets are read.

| Route | Current behavior |
|---|---|
| `/` | Fixture-guarded protected landing showing the role, a Household Prioritization link and CSRF-protected Sign Out. |
| `/login` | Minimal sign-in form; successful/already authenticated login redirects to `/`. |
| `/logout` | Sign-out confirmation; only a CSRF-protected POST ends sessions. |
| `/test` | Public JSON health check, independent of records/database availability. |
| `/assessments` | Protected real-data prioritization with read-only selected-run retrieval and 25 households per page; see section 22. |
| `/households`, household/assessment detail paths, `/assessments?view=queue` | Anonymous users redirect to login; authenticated requests return HTTP 410. No identifiers/responses are echoed. |

The old staff blueprints, sidebar, records/assessment templates, shared UI macros,
CSS, JavaScript, favicon, presentation helpers and pagination were intentionally
removed on `dev/frontend-reset`. Their prior implementation remains in Git at
`9a6b24c`. The landing and auth templates remain minimal. Frontend Task 1 adds
only the shared staff layout and protected prioritization screen (section 22).

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
except `/login`, static assets and `/test`. At this authentication checkpoint,
both roles accessed only a minimal landing and the old record/assessment pages
were retired; later frontend tasks restored protected assessment pages.
`services.auth.admin_required` checks the current database role for future
import/account routes; those workflows are not implemented yet.

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
Sign Out directly. At the authentication checkpoint, the old sidebar and staff
screens were intentionally retired; subsequent tasks rebuilt the staff shell
and assessment views.

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

These are historical findings under the rules retained in completed run 1.
The separately authorized MPI employment correction in section 20 does not
overwrite this run or change the subsidy findings recorded here.

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

## 17. Phase 6B.4 Task 3A: assessment persistence foundation

`migrations/0003_assessment_persistence.sql` adds two tables after migration 0001.
At the Task 3A checkpoint, it had been applied only inside rollback-isolated test
schemas, not to `tanglaw_buhay_dev`, and no real assessments had been stored.
The separately approved Task 3B execution is recorded below. Startup applies no
migrations. Existing source tables, calculations and routes are unchanged.

| Table | Stored information and boundaries |
|---|---|
| `assessment_runs` | Source `batch_id`, required calculation version/provenance, expected source-household count, pending/completed status and timestamps. Multiple runs per batch are allowed. |
| `household_assessments` | Run/batch/household identity and the complete unified MPI/four-program JSONB findings. Original vulnerability rank is a generated, indexed projection of the stored MPI rank. |

Composite foreign keys require each result's batch to match its run and its
source household. A `(run_id, household_uuid)` primary key prevents duplicates
within a run, while allowing the same household UUID in other batches/runs.
The primary key supports household lookup; an index on
`(run_id, vulnerability_rank, household_uuid)` supports original rank ordering
with stable ordering of ties. Neither lookup nor ordering recalculates ranks.
The foreign keys retain previous runs/results and do not cascade source deletes.

`repositories.assessment_runs` provides these focused operations:

- `create_run()` requires a validated, nonempty source batch, snapshots its
  household count and stores caller-supplied calculation version/provenance.
  It does not require the batch to be active or assume a particular batch ID.
- `store_household_assessments()` appends the existing unified result dictionary
  to a pending run. It validates matching household identities and source survey
  year, complete MPI fields and all four complete program outputs. It uses bulk
  insertion and never overwrites duplicates or executes calculations.
- `complete_run()` requires stored and current source counts to equal the
  expected count. Uniqueness and source foreign keys establish full household
  coverage. Inserts and completion lock the run row, preventing competing
  repository operations from modifying a completed run.
- `find_run()` returns technical run/source metadata for either status.
  `find_household_assessment()` and `iter_household_assessments()` return findings
  only for an explicitly selected completed run; pending/partial runs yield
  no findings. The iterator orders by preserved rank and household UUID.

Callers own the connection and transaction. Creating, storing and completing a
run must occur in an explicit caller-managed transaction when atomic completion
is required; failures must roll back that transaction. The repository never
commits, opens a database connection or updates source CBMS records. Completion
and immutability checks are repository guarantees, not a staff authorization
boundary or a defense against privileged SQL bypass. Authorization, real-data
execution/storage orchestration and error presentation remain separate work.
PostgreSQL exception details must never be logged or shown to users because
they may contain private identifiers or payloads.

The JSONB findings contain the unchanged unified result: household identity and
survey year, all MPI flags/metrics/ranks/assumptions and all four program
statuses, reasons, evidence, warnings, references and methodology notes.
JSON arrays are restored to the existing two MPI tuple fields and seven tuple
fields per program; program order is restored to 4Ps/SLP/SPISC/TUPAD. Nested
evidence dictionaries, strings, numbers and `None` are preserved. Retrieval
uses named fields because JSONB object-key ordering is not preserved.
Serialization rejects missing programs/fields, mismatched identities/years, nonfinite numbers
and unsupported new sequence structures instead of silently losing meaning.
This adapter supports the current engine-output contract; an approved new
output structure may require an explicit serialization update. Staff reviews
or decisions have no fields in these tables and cannot replace system findings
through this repository.

Assessment storage has no batch-1 or 2022 restriction. Survey year and
municipality are read from the referenced source batch, and payload years must
match that source. Independent runs can use any valid source batch supported
by the source schema. **Existing migration 0001, importer and active loader still
enforce the current 2022 Pozorrubio pilot**. Actual future-year imports/loading,
source-constraint changes and synthetic future demonstrations remain separately
scoped tasks; this foundation does not enable them. Tests used multiple current
2022 batches and a past-year metadata-only serialization check, with no future
dataset generation or import.

Run focused verification with the configured local test URL:

```powershell
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -p test_assessment_persistence.py -v
```

Verification on 2026-10-14 passed **13 focused tests**, including **10 executed
PostgreSQL tests**. Generated outputs from 32 synthetic households round-tripped
with complete values and Python types, including unknown flags, tuple evidence,
warnings, assumptions and original rank ties. Tests checked migration structure,
foreign keys, batch/run isolation, duplicate rejection, pending-result hiding,
partial-run completion rejection, rollback and unchanged synthetic source rows.
All DDL/rows remained in private test transactions and were rolled back; schemas
did not persist. The full suite passed **200 tests without skips**, with a runtime
guard restricting all 150 test connections to local `tanglaw_buhay_test`.
Read-only catalog comparison confirmed no leftover schemas/catalog changes and
the private `.env` checksum was unchanged. Development/older databases, private
sources, engines and UIs were not modified. Applying migration 0003 or storing
real assessments required separate approval, supplied for Task 3B below.

## 18. Phase 6B.4 Task 3B: controlled real assessment persistence

`scripts/controlled_assessment_persistence_2022.py` is an explicitly invoked
one-time operational runner. Its fixed pilot restrictions do not change the
batch-aware repository design. It reuses the existing local development
connection guard, read-only active-batch loader, unchanged engines, Task 2A
output verification and assessment repository. No route invokes it.

```powershell
.\.venv\Scripts\python.exe -B scripts\controlled_assessment_persistence_2022.py --check
# --execute requires explicit owner authorization; it is not a routine command.
```

Both modes verify local `tanglaw_buhay_dev`, `public`, installed source/auth
migration structures, validated active batch 1, survey year 2022, Pozorrubio and
all seven expected source/reference counts. Partial assessment schemas or any
existing pilot run/result cause a safe stop, including pending runs. Following
the completed execution below, repeat checks/execution will refuse the existing
pilot results; the script never replaces them.

The authorized `--execute` operation rechecks safeguards under a transaction
advisory lock. Share locks protect source/metadata and staff tables from concurrent
writes during the operation; an existing assessment schema is also locked against
competing writers. These locks can briefly delay other writes. Only migration
0003 and derived assessment tables are written. Migration, insertion, completion
and pre-commit verification share one explicit atomic transaction. Any failure
before commit rolls back the entire transaction; there is no repair or retry.
An additional fresh read-only snapshot verifies the committed results. A
post-commit verification failure is reported with `writes_committed: true` and
requires review; a committed transaction cannot be rolled back afterward.

Each engine runs once. The runner checks emitted identities, all 16 indicators,
scores, ranks, program statuses and aggregate agreement with Task 2A, without
changing rules or recomputing filtered ranks. Calculation provenance records the
actual imported batch provenance, engine source hashes, migration hash, version
and authorized task. Every retrieved complete household finding is compared
in memory with its calculated output before and after commit, including evidence,
assumptions, warnings and original rank order. No household comparison is logged
or exported. Console output contains aggregates and sanitized failure codes only.

### Verified execution on 2026-10-14

Read-only `--check` passed, followed by **one authorized `--execute` operation**.
Migration 0003 committed to local `tanglaw_buhay_dev`, schema `public`, together
with **completed assessment run 1**, linked to **source batch 1**, 2022 Pozorrubio.
Stored coverage is **19,304 household assessments** and **77,216 nested subsidy
findings**, with exactly four programs per household. There are no pending or
additional pilot runs. Fresh read-only verification passed without recalculation.

All MPI and program aggregates matched the verified [Task 2A baseline](#verified-execution-on-2026-10-13):
MPI mean score **8.3175047198**, mean coverage **99.8981932357%**, **19,093 Complete
Data / 211 Needs Data Review** households and **212 unknown indicator outcomes**.
Original ranks remain **1–14,366**, with **37 score groups** and **35 tied groups**.
Program totals (For Review / No Historical CBMS Trigger / Needs Data Review) are
4Ps **12,147 / 7,150 / 7**, SLP **12,770 / 2,385 / 4,149**, SPISC **6,339 / 12,965 / 0**
and TUPAD **16,612 / 2,692 / 0**. Existing assumptions and informational warnings
were retained; results remain historical decision support, not eligibility decisions.

Source counts remained households **19,304**, members **69,350**, health **18,282**,
child mortality **33**, codebook **705**, MPI mappings **89** and definitions **214**.
Private in-memory row fingerprints confirmed unchanged source responses and
batch metadata, plus unchanged complete staff account state before/after commit.
All seven local CSV checksums and the private `.env` checksum were unchanged.
The older and test databases were not accessed by the real operation. There were
no authentication, staff permission, UI, source-file or engine changes.

Total controlled execution took **113.261 seconds**, including safeguards,
fingerprints, calculation, persistence and pre/post-commit verification. Peak
Python process working set was **598.91 MiB** (not PostgreSQL/system memory).
All **nine focused synthetic runner tests** passed without database connections,
covering read-only mode, explicit invocation, target rejection, existing-run and
partial-schema rejection, preservation checks, transaction failure propagation,
no retries, complete findings comparison and suppression of private error text.
The full suite was not repeated. Task 3B is Completed; Phase 6B.4 remains In Progress
until separately approved protected real-data staff views are implemented.

## 19. Phase 6B.4 Task 3C: read-only assessment queries

`services.assessment_queries` is an internal backend boundary for stored
assessments, not an endpoint or authorization mechanism. Future protected routes
must enforce staff authorization before calling it; authentication and the
fixture-only public-route guard are unchanged.

The existing assessment repository adds two SELECT-only operations:

- `find_completed_runs(connection, batch_id)` returns at most two completed-run
  metadata rows so the service can detect ambiguity. Pending runs are excluded.
- `list_assessment_summaries(connection, run_id, limit=..., offset=...)` returns
  only household UUID, original stored rank/score, coverage, MPI data-quality
  status, unknown-indicator count and the four program statuses. SQL projects
  these fields from JSONB and applies LIMIT/OFFSET; it does not transfer complete
  household evidence to Python. Ordering uses the existing rank index and UUID
  as the stable tie-breaker. It does not calculate replacement ranks.

Three public service functions reuse repository reads and the existing complete-output
`find_household_assessment()` reader:

- `get_active_assessment_context()` resolves exactly one validated active source
  batch and its explicitly designated completed run (migration 0004, section 21).
  Missing/ambiguous active batches or missing/invalid designations fail with fixed
  `AssessmentQueryError` codes. Multiple completed and pending runs can coexist;
  neither creation order nor the latest run determines which findings are shown.
- `list_active_assessment_summaries(page=1, page_size=25)` returns `context`,
  `items`, `page`, `page_size` and `total`. Pages start at 1; page size is bounded
  to 1–100. A page beyond the end returns an empty list and the same total.
  Total uses the completed run's verified household-count snapshot, avoiding a
  full-count query per request. No search/filter or dashboard aggregation is
  implemented by this task.
- `get_active_household_assessment(household_uuid)` returns `context` and complete
  `findings` for that household in the resolved run. Missing household findings
  produce a fixed error without echoing the UUID. Existing tuple restoration
  retains full MPI/four-program outputs, including evidence and assumptions.

Context includes batch/run IDs, historical survey year, municipality, verified
household count, completion time and original calculation/source provenance.
The service does not hardcode batch 1 or a survey year; existing source/import
constraints still enforce the current pilot, and no future-year import support
was added. List/detail results are private internal objects and must not be
printed, exported or exposed through unprotected routes.

Each call owns a fresh idle connection from the existing `db.get_connection()`
pattern (or a supplied factory) and one REPEATABLE READ, READ ONLY transaction.
Run resolution and retrieval use the same snapshot so concurrent active-batch
changes cannot mix context and findings. Database/configuration failures are
translated into fixed error codes without private exception details. Repository
helpers perform only SELECTs; they leave connection/transaction ownership to
callers. Neither layer invokes engines, imports, migrations or storage writes.

Focused verification on 2026-10-14 passed **12 tests without skips**, including
**six executed PostgreSQL integration tests** in isolated local
`tanglaw_buhay_test` schemas. Generated 32-household outputs verified active-run
resolution, missing/unvalidated/ambiguous states, pending-run exclusion,
pagination boundaries and rank ties, exact complete household retrieval and
isolation between two source batches sharing UUIDs. Tests also confirmed summary
projection, bound parameters, invalid-input/non-idle-connection rejection,
sanitized errors, unchanged stored rows and database rejection of attempted
writes within the service snapshot. Test fixture DDL/rows were confined to the
test database; all created schemas were removed. No development database access,
real-data recalculation or full-suite rerun was needed.

```powershell
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -p test_assessment_queries.py -v
```

Task 3C is Completed. Phase 6B.4 remains In Progress; protected staff routes,
Dashboard/Prioritization/Details UI and authorization integration require a
separately scoped task. No blocker was identified.

## 20. MPI employment NA correction: verified impact, persistence pending

On 2026-10-14, the owner explicitly authorized removing MPI's applicable-missing
E01 nonworking imputation while preserving the adult OFW proxy. Official 2022
HPQ printed pages 5 and 9 were inspected visually and independently for B06 and
Section E applicability. No source values, B06 classifications or E15 responses
were manufactured. Employment and underemployment retain their strict >50%
household bounds; confirmed working-child evidence still takes precedence over
other unknowns. All 16 weights and zero-point unknown scoring remain unchanged.

The legacy `e01_assumed_not_working_count` output field is retained for storage
compatibility, now zero in corrected results. Its former assumption note is no
longer emitted. Stored historical results retain their original fields/notes.
`scoring.py` changes only its explanatory module docstring; scoring/ranking code
and all subsidy modules are unchanged.

`scripts/compare_mpi_employment_correction.py` loads the pilot sources and stored
completed run 1 through verified local read-only snapshots. It calculates the
corrected MPI in memory, audits identities/weights/ranks using the existing
verifier and compares all households with the stored baseline. A separate
counterfactual reuses employment code from checkpoint `54e682a`, removing only
the former nonworking imputation while holding its age/routing rules fixed.
This distinguishes imputation dependence from the under-15 age-scope correction.
Private findings and comparisons remain in memory; console output is aggregate
only. The script invokes no import, migration, persistence or subsidy engine.

```powershell
# Full-batch read-only impact calculation; invoke only for an approved comparison.
.\.venv\Scripts\python.exe -B scripts\compare_mpi_employment_correction.py
```

Successful comparison covered all **19,304 households** in source batch 1, 2022
Pozorrubio, against completed run 1 in local `tanglaw_buhay_dev`. Outcomes are
ordered Deprived / Not Deprived / Unknown:

| MPI employment indicator | Stored baseline | Corrected in memory | Outcomes dependent on former nonworking imputation |
|---|---:|---:|---:|
| Employment Proxy | 4,500 / 14,804 / 0 | 2,026 / 14,804 / 2,474 | 2,474 |
| Underemployment Proxy | 1,949 / 17,355 / 0 | 1,035 / 15,308 / 2,961 | 2,961 |
| Working Child Not in School | 46 / 19,117 / 141 | 51 / 19,069 / 184 | 48 |

**5,483 household-indicator outcomes** depended on the former imputation, not
5,483 distinct households. The former audit field recorded use for **8,442
members in 7,823 households**; use does not imply the outcome depended on it.
Employment's 2,474 changed from 1 to unknown; underemployment's 914 changed from
1 to unknown and 2,047 from 0 to unknown; working-child's 48 changed from 0 to
unknown. In addition, the under-15 applicability correction resolved **five**
previously unknown working-child outcomes as deprived from recorded evidence.

- Mean vulnerability score: **8.3175 → 6.8571**; median **5.5556 → 2.7778**.
  Scores decreased for **3,188**, increased for **5**, and were unchanged for
  **16,111** households. Maximum score remains **56.9444**. Lower scores reflect
  zero-point unknowns, not evidence of reduced real vulnerability.
- Complete Data / Needs Data Review: **19,093 / 211 → 15,238 / 4,066**.
  Unknown indicator outcomes: **212 → 5,690**; mean weighted coverage:
  **99.8982% → 97.5334%**.
- Original full-municipality competition-rank method is unchanged. Recalculated
  in-memory ranks span **1–13,117**, versus stored **1–14,366**; distinct scores
  **37 → 36**. Rank positions improved for **16,115**, worsened for **3,188**,
  and stayed equal for **one** household. No household was excluded or filtered.

All other **13 MPI indicators** matched the stored findings exactly, and **2,120
adult OFW assumed-working members** were retained. The complete stored-run digest,
source-frame fingerprints, seven CSV checksums and private `.env` checksum were
unchanged. All database connections were read-only; run 1 and source records were
not modified. Successful comparison took **48.313 seconds**. An initial comparison
stopped on a Pandas 3 UUID dtype mismatch; the comparison alignment was corrected
before the successful rerun, without changing indicator logic or stored data.

All **113 focused synthetic tests** passed with PostgreSQL connections blocked,
covering indicators, scoring, subsidy screening and unified outputs. Two existing
test files were updated only for the corrected expectations and age boundaries;
comparison-helper synthetic checks also passed. No UI or unrelated engine changes
were made, and no private findings were exported.

At the correction checkpoint, persistence awaited separate approval. The owner
subsequently authorized the controlled operation recorded in section 21; corrected
Run 2 is now designated. Historical Run 1 remains unchanged. Do not overwrite
existing results or reuse the historical Task 3B runner. Phase 6B.4 remains In Progress.

## 21. Corrected MPI version and explicit selection: completed

The verified MPI correction, focused tests, comparison script and documentation
were committed locally as **`f8a9362`** (`Correct MPI missing employment evidence
handling`). Persistence/selection preparation was verified before separate owner
authorization. The successful development execution is recorded below.

`0004_assessment_run_selection.sql` adds only `assessment_run_selections`:
one row per source `batch_id`, a `run_id` and a technical `selected_at` timestamp.
The composite foreign key requires the run to belong to that source batch.
Migration 0004 does not seed a selection or alter source tables/historical runs.
`designate_run()` permits a selection only for a validated batch and completed
run. Readers independently join completed/validated state; even a pending run
selected by privileged SQL is never exposed. These are repository/read guarantees,
not staff authorization or a ban on all privileged SQL operations.

`find_designated_run()` supplies the existing query service with explicit run
metadata. Summaries/details keep their read-only snapshot, pagination, original
rank ordering and complete-output restoration. There is no fallback to the
latest, only, first or pending run. Without migration 0004 or a valid designation,
the updated queries fail closed (`assessment_query_unavailable`
for a missing table, `approved_assessment_run_missing` for a missing/invalid
selection). No route invokes these functions and the minimal UI is unaffected.
Migration 0004 and the designation now exist in the local development database;
the query service resolves corrected Run 2. The historical impact comparison
continues to read Run 1 explicitly, independently of current selection.

Current designated version: **`tanglaw-buhay-mpi-e01-unknown-v2`**. It means corrected MPI
missing-E01/under-15 handling, unchanged weights/ranking formulas and all four
original subsidy outputs copied from Run 1. The original version remains
`tanglaw-buhay-approved-mpi-screening-v1`. Version provenance records source/run
identity, actual source provenance, engine/migration hashes, correction checkpoint
and the separate unchanged subsidy source/version. PostgreSQL allocated Run 2;
the runner does not hardcode the new run ID.

`scripts/persist_corrected_mpi_2022.py` is a new controlled path, not a rerun of
the importer or Task 3B script. The following commands document the approved
operation; **do not rerun them on the completed pilot**:

```powershell
# Historical pre-execution check; repeat checks now stop on existing Run 2.
.\.venv\Scripts\python.exe -B scripts\persist_corrected_mpi_2022.py --check
# Historical owner-authorized execution; already completed, do not repeat.
.\.venv\Scripts\python.exe -B scripts\persist_corrected_mpi_2022.py --execute
```

Both modes use the existing verified loopback-only `tanglaw_buhay_dev`, `public`
connection guard and migration 0001–0003/source-count checks. They require validated
active batch 1, 2022 Pozorrubio, completed original Run 1/version v1 with 19,304
findings and 77,216 nested program results, and no other pilot run (pending or
completed). A pre-existing selection must be absent or point to Run 1. An earlier
corrected/partial replacement or unexpected selection causes a stop, not repair,
overwrite or automatic retry.

The controlled execution obtains the existing advisory lock and table locks,
rechecks conditions, loads sources read-only and calculates MPI once in memory.
All four complete subsidy payloads are deep-copied from Run 1; no subsidy engine
is executed. Existing verifiers check household/program coverage, identities,
scores, exact full-municipality ranks and statuses. The corrected MPI must match
the approved comparison counts/mean/coverage/rank range before storage. All other
13 flags and retained OFW proxy counts must match Run 1.

One outer transaction covers migration 0004 (when absent), new-run creation,
complete findings, completion, explicit designation and verification. A focused
`persist_version()` transaction/savepoint preserves atomicity within that outer
operation. Every retrieved finding must equal the expected complete payload,
with 19,304 households and four findings each (77,216 total), before commit.
Run 1 metadata/full findings, all source/metadata rows, staff accounts and local
CSV/configuration checksums must remain unchanged. A fresh read-only post-commit
check is required; failure after commit is explicitly reported as committed and
never triggers an automatic repair/retry. Errors suppress private details and
no household comparison is exported.

Verification on 2026-10-14 passed **20 focused tests without skips**, including
**11 executed PostgreSQL tests** in isolated local `tanglaw_buhay_test` schemas.
Generated 32-household/128-program results verified exact payload copying and
historical retrieval, explicit selection among multiple runs, pending/cross-batch
rejection, preserved pagination/rank ties, read-only service behavior, and rollback
of both results/pointer after failure. A separate failure rolled back migration
0004 and partial new-run writes together. Only synthetic fixture schemas received
test writes; all schemas were removed. No development database access, real-data
recalculation, original import, original one-time persistence execution or UI
change occurred. The correction's earlier 113 passing synthetic tests and real
read-only comparison remain the recorded evidence, not repeated in this task.

```powershell
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -p test_assessment_queries.py -v
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -p test_corrected_assessment_version.py -v
```

### Authorized development execution on 2026-10-14

Read-only preflight passed for local `tanglaw_buhay_dev`, schema `public`, validated
active source batch 1, 2022 Pozorrubio, preserved historical Run 1 and no prior
replacement. The owner then explicitly authorized **one** controlled execution.
It succeeded in **145.677 seconds**, applying migration 0004, creating/completing
**Run 2** and designating it for **source batch 1** in one atomic transaction.
No original import or historical Task 3B persistence operation was rerun.

Stored coverage is **19,304 household assessments** and **77,216 subsidy findings**,
with all four complete program outputs copied unchanged from Run 1. MPI retains
all 16 indicators: **15,238 Complete Data** households and **4,066 Needs Data Review**,
with **5,690 unknown indicator outcomes**. Mean vulnerability score is
**6.8570992771**, mean coverage **97.5333983745%**, and full-municipality ranks span
**1–13,117** with unchanged competition-tie rules. The 2,120 adult OFW working
proxy members were retained; nonworking E01 imputation counts are zero.

Fresh read-only post-commit verification compared every complete stored finding
with the expected corrected payload and confirmed designated completed **Run 2**,
version `tanglaw-buhay-mpi-e01-unknown-v2`. Full Run 1 findings/metadata, source
responses and batch metadata, staff accounts, all seven CSV checksums and private
`.env` checksum were preserved. Subsidy status/evidence/assumption/warning values
are unchanged. No household data was printed or exported.

**Preparation/testing and controlled persistence/designation are Completed.**
The current-run query layer is ready for separately scoped protected staff views.
Phase 6B.4 remains In Progress; no frontend task was started by this checkpoint.

## 22. Frontend Task 1: protected Household Prioritization

`GET /assessments` is now a separately protected real-data route registered in
the existing app. The unchanged application-wide staff guard checks both
approved roles, account activity, session version and expiry before any query.
The minimal `/` landing keeps its fixture-source guard and provides a link to
Prioritization. Login redirects and authentication policies are unchanged.
`/test` remains public and independent of PostgreSQL. Household and assessment
detail routes, including the old queue query URL, remain unavailable (HTTP 410).

`services.prioritization.get_prioritization_page()` requests 25 summaries from
`list_active_assessment_summaries(include_household_identity=True)`. The existing
query resolves the validated active batch and explicitly designated completed
run; local batch 1 currently designates corrected Run 2. There is no fallback
to Run 1, latest-run selection, source loading or engine calculation.
SQL LIMIT/OFFSET and stored rank/UUID ordering remain unchanged. Page identities
are retrieved through `repositories.dataset_batches.find_household_identities()`
on the same read-only repeatable-read connection, using only those page UUIDs.

The actual imported columns were inspected before implementation: household
`barangay`, and member `a01_first_name`, `a01_middle_name`, `a01_last_name`,
`a02_relation_to_hh_head`. The imported codebook confirms A02=`1` means Head.
Joins use both source `batch_id` and household `uuid`; member position is not
used as proof of headship. No suffix is invented from the definition reference:
the actual imported roster has no suffix column. Missing or ambiguous heads
receive explicit display text, never another member's name. Names remain
observed source components; blank/literal-NA components are omitted for display
without rewriting stored responses. The pilot contains one head per household.

The initial Task 1 table had six columns. Task 1B adds Household ID, giving seven:
original Municipal Rank, Household ID, Household Head with UUID, Barangay,
stored Vulnerability Score out of 100 (two display decimals), Subsidies for Review,
and reserved Action. See section 23 for ID provenance. Badges require exact `For Review`
status and follow 4Ps/SLP/SPISC/TUPAD order; otherwise the row says
`No For Review signals`. There are no new score classes or replacement ranks.
Pagination includes counts, bounded page links, Previous and Next. Malformed
page parameters return 400, pages beyond the final page return 404, and missing
batch/designation or database failures return sanitized 503 without fixtures.

`templates/staff/layout.html` is reusable for later approved staff screens.
The original local Tanglaw-Buhay logo and municipal flag (visually cropped to
the seal by CSS) are copied unchanged into `static/branding/`. Eight SVG icons
were downloaded from the approved Figma reference into `static/icons/`; no
temporary asset URLs, new framework, font service or dependency is required.
After Task 1C, `static/css/base.css` contains shared foundations,
`static/css/staff.css` contains the staff shell/shared page header, and
`static/css/assessments.css` contains assessment presentation and pagination.
The shared layout loads base/staff styles before the page's `page_styles` block.
`static/js/staff-navigation.js` only handles the responsive navigation toggle.
Unimplemented modules and Review Queue have planned labels and no fake links.
Account display uses the real username and role because `staff_users` does not
store a personal display name. Sign Out uses the existing CSRF-protected POST.

Verification on 2026-10-14 passed the full **241-test suite without skips**,
including synthetic route/rendering/pagination tests and isolated PostgreSQL
identity/selection tests. A runtime connection guard confined all **218** test
connections to local `tanglaw_buhay_test`; none targeted development records.
Independent read-only development checks sampled pages 1, 2, 387, 772 and 773:
**104 households** matched their complete stored Run 2 score/rank/program fields
and batch-scoped source identities. Page 773 contained four rows; shared ranks
continued across pagination boundaries. No engines or write operations ran.

Browser checks used synthetic summaries and long names/UUIDs at 1440, 1366,
1024, 375 and 320px widths. They verified original local images and icon
geometry, no document overflow, controlled horizontal table scrolling with no
nested vertical table scrollbar, 16px row text, 44px control heights, keyboard
menu/skip-link behavior and working next/final-page links. Browser error/warning
logs were empty. Temporary QA files/screenshots remain in Git-ignored `notes/`.

Task 1 changes are limited to app/route registration, the identity projection,
query/presentation services, shared/page/pagination templates, static assets,
focused tests and these project instructions/context notes. MPI, screening,
imports, source records, stored runs/designation, migrations and authentication
policies are unchanged. Task 2 filters, Task 3 assessment details, Review Queue
and the other modules remain deferred; Phase 6B.4 remains In Progress.

## 23. Frontend Task 1B: household reference IDs and table refinement

Household IDs are derived for display as `HH-{source_row_number:05d}`. The
existing importer assigns `source_row_number` by enumerating original CSV
data rows from 1, excluding the header. A read-only check on 2026-10-14 verified
every stored `(source_row_number, uuid)` pair against the original household
CSV sequence: all **19,304** matched exactly, positions **1–19,304**, and the
file's SHA-256 matched batch 1's imported provenance. No source data was changed.

The existing page-scoped identity SELECT now projects `h.source_row_number`.
It retains the batch/UUID boundary and the query service's single read-only
repeatable-read snapshot. `services.prioritization` formats this position and
rejects missing/nonpositive/noninteger values with a sanitized query error.
IDs never depend on municipal rank, member line, current row order or page
offset. The 2022 batch therefore displays `HH-00001` through `HH-19304`.
They are Tanglaw-Buhay reference IDs, not official PSA identifiers; other source
batches can reuse the same labels, so batch context and UUID remain authoritative.

The seven-column table gives IDs their own column and keeps the original UUID
beneath the household head's name. Numeric ranks/scores are right-aligned,
IDs use tabular numerals, and the head column retains room for long names/UUIDs.
Adjusted widths and controlled horizontal scrolling preserve readable text and
the existing navy/neutral/gold design. Shared municipal ranks mean equal priority;
the unchanged UUID ordering only stabilizes pagination and does not break ties.
The page explicitly explains both ID provenance and equal priority within ties.

Task 1B modifies only the identity projection, presentation service, table/CSS,
focused tests and relevant documentation/plan. It adds no database column,
migration, engine calculation, filter, detail view or authentication change.

Verification passed **243 tests without skips**, with a runtime guard restricting
all **218** PostgreSQL test connections to local `tanglaw_buhay_test`. Focused
tests cover both ID endpoints, non-rank-based IDs across pages/reordered subsets,
invalid-source-position rejection, seven-column rendering, batch isolation and
unchanged ranks/ties. Read-only development checks verified **54 distinct
households** on pages 1, 2 and 773, plus repeated retrieval of page 1: reference
IDs matched stored source positions and all Run 2 scores/ranks/program statuses
remained unchanged. No real records were written or exported.

Synthetic browser checks at 1440, 1366, 1024, 375 and 320px verified readable
seven-column rendering, correct next/final-page navigation, full IDs, long
names/UUIDs and keyboard horizontal scrolling. The page has no document
overflow or nested vertical table scrollbar; browser warning/error logs were
empty. Task 1B is Completed. Subsequent filters and assessment details were not
started. A synthetic screenshot is retained only in Git-ignored `notes/`.

## 24. Frontend Task 1C: stylesheet organization

The behavior-preserving refactor uses three directly served stylesheets, without
imports, a framework or build tools:

| File | Responsibility |
|---|---|
| `static/css/base.css` | Design tokens, global defaults/resets, typography, accessibility utilities and global focus indicators; responsive heading typography. |
| `static/css/staff.css` | Staff workspace/sidebar, branding, navigation/profile/Sign Out, sidebar focus customization, shared page-header structures and responsive staff layout/navigation. |
| `static/css/assessments.css` | Assessment tabs, summary/historical notice, seven-column ranking, household display, subsidy badges, empty states, pagination and assessment-specific responsive rules. |

`templates/staff/layout.html` loads `base.css`, then `staff.css`, followed by an
empty reusable `page_styles` block. The prioritization template overrides that
block to load `assessments.css`. Its content markup and pagination partial remain
unchanged. Future staff pages can add their own module stylesheet through the
same block without duplicating the document shell or loading unrelated styles.
Responsive rules stay with their owning styles; their selectors, conditions and
declarations are preserved. The permanent Frontend File and Folder Organization
section in `AGENTS.md` guides incremental module separation and shared reuse.

No application Python, JavaScript, assets, data or assessment behavior changed.
The existing synthetic rendering test also checks the three stylesheet URLs in
load order and successful static-file responses. Task 1C remains separately
scoped from filters, details and all other future modules.

Verification preserved all **125** original CSS selector/declaration/media-rule
entries. Synthetic before/after screenshots were byte-identical at 1440, 1366,
1024, 375 and 320px widths. Style/geometry comparisons matched for **94**
representative elements at each size; rendered body HTML and assessment data
were identical on three synthetic pages. All **18 focused existing tests** passed
(10 prioritization and eight frontend-reset tests), without database connections.
All three stylesheets and existing branding/icons loaded successfully. Keyboard
menu, visible focus, skip link and horizontal table scrolling passed; next and
final-page navigation retained seven columns, correct IDs and the final four
rows. Browser warning/error logs were empty. No production/test database writes,
real-data inspection or backend recalculation were performed for Task 1C.

Task 1C is Completed and was committed as `666e754`, then explicitly fast-forward
merged and pushed to `dev/frontend-reset`. Synthetic QA comparisons/screenshots
stay in Git-ignored `notes/`.

## 25. Frontend Task 2: prioritization search and filters

The existing authenticated `GET /assessments` route now accepts these optional
GET parameters. Controls submit together with Apply Filters; the form carries
no page parameter, so applying changed selections starts at page 1. Clear
Filters links to the unfiltered route.

| Parameter | Meaning |
|---|---|
| `q` | Case-insensitive literal substring of the displayed unique household-head name, or an exact canonical Tanglaw-Buhay reference such as `HH-00001` (prefix case-insensitive). |
| `barangay` | Exact recorded barangay, with surrounding spaces omitted for display/matching; empty means All Barangays. |
| `program` (repeatable) | 4Ps, SLP, SPISC or TUPAD; match stored `For Review` according to the selected subsidy mode. No selection imposes no subsidy restriction. |
| `subsidy_mode` | `any` (default): at least one selected program has `For Review`. `exact`: all selected programs, and no unselected programs, have `For Review`. |
| `completeness` | `all` (All Households), `determined` (All 16 Indicators Determined) or `unknown` (With Unknown Indicators). |
| `priority` | `all` (default), `both`, `primary`, `secondary` or `other`; four compact group links plus Outside Priority Groups under More Filters. |
| `page` | Existing positive page number, with 25 records per page. |

`services/assessment_filters.py` validates an immutable filter selection, rejects
repeated scalar parameters, unsupported choices, NULs and search/barangay text
over 200 characters, trims inputs and canonicalizes the selected program order.
Malformed filter input returns a generic 400 without connecting to assessment
data. Route authorization still runs first; authentication policy is unchanged.
Literal `%`, `_` and the SQL escape character in name searches are escaped before
binding, rather than interpreted as user-supplied wildcard syntax.

`services/prioritization.py` passes filters through the existing query service
and supplies selected values/options and pagination parameters to templates.
The route remains thin. `templates/assessments/filters.html` owns the GET controls;
the existing pagination partial includes all active values in every page link.
Labels, native selects, checkbox/radio fieldsets, visible focus and 44px-or-larger
control targets support keyboard/mobile use. Assessment-specific control styles
remain in `assessments.css`, loaded after unchanged base/staff styles. No new
JavaScript, dependency or stylesheet is needed.

`list_active_assessment_summaries(filters=..., include_barangays=True)` resolves
the validated active batch and its explicitly designated completed run, retaining
corrected Run 2 in the pilot. Context, distinct source-batch barangays, filtered
count, SQL page and page-bounded identities share one read-only repeatable-read
snapshot. `list_barangays()` omits blank/literal-NA values and retains all available
batch options regardless of current filters. An unavailable requested barangay
remains visible as unavailable, with no invented source mapping.

`repositories/assessment_runs.py` shares the same parameterized WHERE predicates
between counting and summary pagination. Categories use AND, and program
predicates use OR in broad mode without joins that duplicate households. Exact
mode requires each selected program's stored status to equal `For Review` and
each unselected program's status to be distinct from `For Review`. Other statuses,
including `Needs Data Review`, do not count as additional findings. The complete
four-program summary and all actual badges remain visible. Both modes leave
households unrestricted when no program is selected. MPI completeness uses
only stored `mpi.unknown_indicator_count`: zero for determined, greater than zero
for unknown. Determined outcomes can include documented engine assumptions;
this filter does not claim all source responses were observed. No MPI/status
calculations run, and no arbitrary score categories, MPI Data Status column or
Needs Data Review KPI are added.

Canonical ID search binds the existing source-row number through batch/UUID joins
and existing indexes; it avoids scanning head names. Name matching requires
exactly one A02=`1` member within the household's batch and UUID, omits blank/NA
name components as the display does, and never selects between ambiguous heads.
Source joins are added only when required; unfiltered total retains the verified
completed-run count. Filtered totals use a separate count SELECT with the same
predicates, so even an empty/out-of-range page has an accurate matching total.
Only bounded summary fields and current-page identities reach the presentation.

The shared project policy in `services/subsidy_priority.py` derives
`subsidy_priority` in returned summaries from stored scores and the four stored
program statuses. Primary Priority requires a score strictly above 25 and at
least one `For Review`; Secondary Priority requires 20 through 25 inclusive and
at least one `For Review`. Everything else is No Subsidy Priority. `both` selects
either tier; `other` is their complement. The SQL filters share the policy's
threshold constants and the existing count/page selection. Unknown MPI
indicators neither grant nor remove priority; `completeness=unknown` remains an
independent filter. These categories are staff prioritization policy, not MPI
classifications, program eligibility or staff review decisions. Nothing is
persisted or recalculated. Policy is applied to the dynamically selected run;
current import restrictions and all stored findings/ranks remain unchanged.

Selector counts use `count_priority_groups()` in the same read-only snapshot as
the list. They retain all non-priority filters and omit the selected priority
restriction, so each group shows its available matches. Counts and rows reuse
the same stored-result predicates. Group links preserve applied filters and
reset pagination; one priority select in More Filters carries form state.
`completeness=unknown` is presented as Needs Assessment Attention, separately
from program Needs Data Review findings. `priority_display()` shares staff
labels between the table and the existing score card on both detail tabs;
the latter calls the approved policy over stored results. Internal No Subsidy
Priority is displayed as Not in Priority Groups. The three-card summary,
Overview, indicator/evidence content and planned tabs retain their existing roles.

All views preserve original municipal ranks, scores, ties, UUID order and ID
generation. The top summary retains the municipality's assessed-household total;
active filters show matching counts beside the ranking and in the pagination
footer. Empty filters show a clear zero-results message and retain controls.
Out-of-range pages still return 404, and unavailable data returns sanitized 503.
Source records, stored findings/runs/selection, engines, authentication and
schema/migration files are unchanged. Details, Review Queue and Household Records
remain deferred.

Verification on 2026-10-14 passed **32 focused tests** (13 prioritization tests and
19 query tests), including executed PostgreSQL tests in isolated
`tanglaw_buhay_test` schemas. They cover case-insensitive full-batch name search,
source-row ID lookup, all four program filters, OR/AND combinations, both MPI
completeness selections, exact counts, empty sets, 25-row paging, original ranks
and ties, designation/batch isolation, literal wildcard/injection-like search
text, missing/ambiguous heads, escaped rendering and authorization for both
approved roles. Read-only snapshots and unchanged stored findings were checked.
The broader **251-test suite passed without skips**, with all **246** connections
confined to the isolated test database. A first broad run was rejected by an
overstrict disposable test guard; allowing only the existing auth/import test
schema options resolved that harness issue. Final ID-query optimization was
subsequently covered by the focused query tests.

Browser checks used a separate synthetic PostgreSQL schema and the real
prioritization route/templates, verifying search, retained selections, page 2,
reset to page 1, combined/empty results, Clear Filters and keyboard submission.
All seven columns remain at 1440, 1366, 1024, 375 and 320px; controls remain inside
the viewport with 16px text, visible focus and controlled horizontal table
scrolling. Browser warning/error logs were empty. A separate generated
19,304-household / 57,912-member query fixture completed ID, name, unmatched-name
and combined requests in approximately **0.14–1.67 seconds** locally, including
count, options and page identities; this is a synthetic local measurement.
No real CBMS records were read or modified for verification. Synthetic fixture
schemas are removed after checks, and disposable QA scripts/logs/screenshots
remain Git-ignored under `notes/`. Task 2 was reviewed, committed as `3e3f37a`,
pushed to its feature branch and subsequently fast-forward merged/pushed to
`dev/frontend-reset` before Task 3 began.

### Task 2 refinement: subsidy combination controls

The radio selection below the four checkboxes offers "At least one selected
subsidy" and "Only the selected subsidy combination". Native radio state updates
the concise explanation through the existing CSS `:has()` pattern, including
before Apply Filters is pressed; no JavaScript or dependency is added. Native
fieldset/legend labels and focus styles preserve keyboard access.

Server-side normalization rejects unsupported or repeated `subsidy_mode` values
before connecting to assessment data. Pagination retains exact mode even with
zero checked programs, without making mode alone an active restriction. Clear
Filters returns to default broad mode; the GET form omits page so applying
changes starts at page 1. Search, barangay and MPI completeness still intersect
with either subsidy mode in the shared count/page SQL predicates.

Refinement verification passed all **36 focused tests** and the complete
**255-test suite without skips**. All **287** full-suite connections were guarded
to local `tanglaw_buhay_test`. Synthetic cases cover every checkbox subset in both
modes, single/multiple exact selections, additional findings, other statuses,
empty selection, intersecting search/barangay/completeness, 25-row pagination,
counts, mode retention, complete badges and unchanged stored scores/ranks.
Browser checks confirmed live explanation changes, native keyboard mode
switching, Clear Filters and responsive controls at 1440, 375 and 320px without
document overflow or console warnings/errors. Synthetic 4Ps/TUPAD selection
returned 18 broad matches and two exact matches; extra badges remained visible
in broad results. The disposable preview was stopped after verification.

## 26. Frontend Task 3: stored Assessment Details and MPI evidence

Task 3 starts from reviewed Task 2 commit `3e3f37a` on
`dev/assessment-details`. Its scope is Overview and MPI Indicators. The
Prioritization table's View Assessment link opens
`GET /assessments/<uuid:household_uuid>`. The unchanged application-wide staff
guard authorizes both existing roles before querying any findings or sources.
Malformed UUID paths retain the retired-page response; valid but absent
households return a sanitized 404. Missing designated context or database
failures return 503. Responses retain `Cache-Control: no-store`.

### Read-only source boundary

`get_active_assessment_detail()` resolves the active validated batch and
designated completed run through the existing read-only repeatable-read context.
It retrieves one stored finding, that household's source-row/head identity,
projected MPI household fields, relevant roster fields and Section L answers,
plus the same batch's codebook, mapping and definition rows. All reads share one
connection/snapshot. `repositories/assessment_evidence.py` contains only SELECTs;
parameters bind batch, UUID, variable names and projection fields. Section L
joins retain batch + UUID + member line. No raw household/member JSON reaches a
template, no source-batch loader is called and no engine is executed by a route.

The active batch's column metadata and non-household reference tables were
inspected read-only: all required fields are present, reference keys match the
projection/decoder and completed Run 2 remains designated. Private household
records are not printed or exported for verification.

### Presentation responsibilities

- `services/assessment_details.py` retains stored metrics, flags, ranks and
  screening statuses. Presentation-only contributions use the scoring module's
  authoritative dimensions and Fraction weights. Their sum must reconcile with
  the stored score; a mismatch fails safely without replacing that score.
- `services/mpi_indicator_content.py` holds plain-language outcome wording,
  source fields, approved rule descriptions and limitations, reviewed against
  the existing direct, housing, education, health and employment implementations.
  This is descriptive content, not another outcome engine.
- `services/mpi_evidence.py` selects relevant member age groups, retaining
  uncertain ages rather than silently omitting them. Illness follow-up is
  omitted when L33 establishes the skip. Overcrowding shows the original roster
  count without displaying unrelated member identities. Missing joins and
  recorded routing/follow-up conflicts receive clear notices. Documented adult
  assumed-working combinations remain labeled internally; staff evidence shows
  the missing recorded work answer without an assumptions narrative.
- `services/assessment_reference_labels.py` matches original codes to the
  same-batch reference rows while preserving raw text internally. Missing,
  unmapped and conflicting meanings remain explicitly unavailable. Quantity and
  age descriptions do not assign deprivation. E15 Synthetic Support, provisional
  L34 semantics and variable-definition provenance remain preserved in metadata
  and technical documentation; they are not routine staff-facing content.
- `services/assessment_evidence_view.py` arranges those existing decoded cells
  into short staff-facing groups without assigning outcomes. Food responses use
  an eight-item grid; assets are grouped by type; relevant members show their
  recorded work, education or health responses. Task B removes technical
  assumptions, weights, rules and provenance from rendered content, retaining
  the original source cells, notes and limitations internally. A separate
  `record_cells` projection exposes only label, identifier, original recorded
  value and concise meaning to the secondary disclosure. Overcrowding's
  space-per-member figure is a presentation comparison from recorded area and
  roster size.

The stored MPI payload does not include a per-member calculation trace. Evidence
therefore presents the stored outcome and relevant original answers without
claiming an unsupported individual causal attribution. A missing
answer is never automatically Not Deprived, and determined outcomes can coexist
with gaps when the approved rule is conclusive. Educational group-5 cohort,
FIES cutoff, asset crosswalk and employment assumptions remain documented.

### Navigation and frontend organization

Overview shows actual stored counts/coverage, four contribution bars out of 25
and all four exact screening statuses. How it works uses a native expandable
disclosure with three short guidance items. Task B removes the recorded
assumptions/limitations disclosure. A shared header/metric
row is rendered for both Overview and MPI Indicators. Section links retain list
page/search/filters through validated internal parameters; arbitrary return
URLs are never used.

MPI Indicators groups all 16 results into four native details disclosures.
Education starts open; the other dimensions and every View Evidence disclosure
start closed. Each indicator shows its text status, one short household-focused
sentence and score contribution. Primary evidence presents decoded answers and
relevant named members, lines and historical ages. CBMS Record Details is limited
to field label, stored identifier, Recorded Value and concise decoded meaning.
It contains no weights, rule text, notes, dataset/section metadata or provenance.
Technical metadata remains internal.
User content is escaped. The status guide distinguishes Deprived, Not Deprived
and Unknown.
Subsidy Screening is available through `tab=screening`. Its presentation lives
in `services/subsidy_screening_details.py`, with staff wording and bounded
source-field lists in `services/subsidy_screening_content.py`. It retains stored
statuses, translates structured trigger/context evidence and warning codes,
and decodes original batch-scoped answers. The UI labels `Needs Data Review` as
Not Enough Information and `No Historical CBMS Trigger` as No Review Signal;
stored values and filtering remain unchanged. Evidence prioritizes members named
by saved findings or warnings for For Review results, while inconclusive findings
retain the broader information needed to understand uncertainty. Expected blank
follow-up answers are not presented as errors solely because they are absent.
Unverified-requirement metadata remains internal. Review History remains planned;
no staff review or case workflow is created.

The existing staff layout and styles load before `assessment-details.css`, which
owns the new page's metrics, sections, disclosures, evidence and responsive
layout. The existing assessment stylesheet owns the table action link. Modular
templates live under `templates/assessments/`. The small
`static/js/assessment-details.js` script closes nested evidence when a dimension
closes; native summaries retain mouse and keyboard activation. No dependency or
framework is added.

`services/assessment_request_logging.py` masks assessment request targets in the
Werkzeug development access logger while retaining request method and HTTP
status. Household UUIDs and search/filter values are not printed in those logs.
No authentication policy, engine, source record, stored run, schema or migration
is changed by this task. Details do not create staff reviews.

### Verification

All **52 focused tests** passed (12 detail presentation/route tests, 26 query tests
and 14 prioritization tests). The complete **271-test suite passed without skips**;
the guarded runner restricted all **292** database connections to local
`tanglaw_buhay_test`. Synthetic fixtures cover both approved roles and revoked,
inactive/expired/unknown-role sessions, missing/malformed UUIDs, sanitized errors,
safe return parameters, all 16 original outcomes/weights, score reconciliation,
unknowns, relevant ages, missing names/joins/references, conflicting reference
meanings, E15/L34 provenance and recorded source assumptions/conflicts. PostgreSQL
checks verify batch/designation isolation, narrow field projection, exact member
lines, read-only snapshots and unchanged source/findings fingerprints.

Synthetic browser checks used the real route/templates and repository SELECTs
against a disposable test schema. View Assessment, Overview, How it works,
all four dimensions and all 16 evidence disclosures were verified. Education
alone starts open, evidence starts closed and native Enter controls work.
Return navigation retains search/program selections. Shared metrics remain
consistent; 16px evidence text and 48px disclosure targets were observed.
Layouts at **1440, 1024, 375 and 320px** have no document overflow; browser
warnings/errors were empty. The preview and its disposable schema are removed
after verification. No private household screenshots, exports or logs were
created, and no development source or assessment data was changed.

The subsequent UI/UX refinement was checked with synthetic browser views of
Overview, compact MPI rows, opened school/food/asset/employment evidence and
keyboard-opened CBMS Record Details. Browser widths 375 and 320px showed no
horizontal document overflow. Fourteen focused detail tests and the guarded
full **273-test** suite passed; all **292** suite connections targeted the
isolated local test database. The original source, stored findings, ranking,
route authorization and repository queries were not changed by this refinement.

Task 3 was completed and pushed as `abd2d59` on `dev/assessment-details`.
Detailed subsidy screening and review history remain separate tasks. The existing
private development server must be restarted after Python/template changes because
automatic reload is disabled; refreshing the browser then loads the new page.

### Task B: verified staff-facing content cleanup

Completed on 2026-10-14. The approved
header, metrics, compact Overview, four contribution bars, subsidy summary,
four MPI dimensions and all 16 indicators remain. Indicator reading order is
name, status, short explanation, points and View Evidence. Small CSS changes
retain the desktop points column and put the explanation before points on
mobile; long indicator titles wrap without colliding with the status.

Routine screens and both evidence disclosures no longer render synthetic/pilot
terminology, preparation/provenance narratives, methodological cautions,
assumptions, weights, rules or technical limitation lists. Missing answers remain
Not recorded; recorded conflicts receive a brief verification message. Internet,
transport and E15 values use their existing decoded meanings without origin
classifications or claims of official PSA observation. Original source metadata,
E15 provenance, provisional L34 semantics and all methodological descriptions
remain in the existing reference/evidence services and indicator catalog.

The former L34 display collapsed both provisional meanings into affected.
Staff-facing primary and secondary evidence now distinguish code 1 as
Affected by illness and code 2 as Not affected by illness, each followed by
needs verification. Missing values remain Not recorded; unsupported meanings
remain Meaning needs verification. This changes presentation only; the original
decoder, raw values, provisional interpretation and stored outcomes are unchanged.

All **17 focused detail tests**, **26 query tests**, and the required
`.\.venv\Scripts\python.exe -B -m unittest discover -s tests` passed. The complete
**276-test** suite had no skips; a temporary connection guard confined all **292**
connections to local `tanglaw_buhay_test`. Existing source/findings preservation,
read-only database and authorization tests remain; new regressions cover both
L34 codes (including decimal forms), missing/unknown codes, all 16 four-field
record projections, internal metadata retention and rendered technical-content
exclusion. No private-data verification scripts were needed for this presentation
change; no development database connection or real-data recalculation occurred.

Synthetic browser checks used real rendered routes/templates in an isolated local
preview with database connections blocked. Screenshots were inspected at desktop
and mobile sizes; **1440, 1024, 375 and 320px** checks verified four bars, unchanged
metrics/statuses, all 16 evidence/record disclosures, native keyboard operation,
title wrapping and no document overflow or technical-content leakage. The preview
harness handles its favicon request separately; the final checks had no browser
warnings/errors. Routes, authentication, queries, engines, source data, stored
Run 2 and unrelated modules are unchanged. Local preview artifacts remain under
Git-ignored `notes/assessment-cleanup/`.

### Task B follow-up: dimension controls and View Assessment alignment

Verified on 2026-10-15 after staff review. The dimension summary now spans the
full card header, including its previously inactive side padding. Closing a
dimension also closes its View Evidence and CBMS Record Details disclosures, so
reopening the dimension starts with a compact indicator list. Native summary
activation remains available by mouse and Enter. The Prioritization table gives
the View Assessment action enough width and inset at standard desktop zoom; its
text stays on one line. The narrower table minimum continues to allow horizontal
scrolling on smaller viewports.

Synthetic browser checks at **1440, 1366, 1024 and 375px** followed the View
Assessment link, opened nested disclosures, clicked the dimension card edge,
reopened it and repeated the close with Enter. All checks passed with no document
overflow or browser warnings/errors. At 1366px the action had more than 14px of
space on either side. The required full suite passed **276 tests without skips**;
all **292** database connections were guarded to local `tanglaw_buhay_test`.
Original source data, stored findings, calculations, routes and authentication
were untouched.

## Staff review initiation

Migration `0005_staff_reviews.sql` adds independent staff review records. Apply it
explicitly after migrations 0001-0004; application startup never migrates the database:

```powershell
psql -h localhost -d tanglaw_buhay_dev -U YOUR_DB_USER -1 -v ON_ERROR_STOP=1 -f migrations/0005_staff_reviews.sql
```

Replace `YOUR_DB_USER` with the local database account. The transaction creates only
`staff_reviews` and its partial unique index; existing source, finding and account
tables are unchanged. Until applied, Staff Reviews fails safely; existing assessment
tabs continue to use their original queries.

Both authorized roles can use the Household Assessment Staff Reviews tab. A GET
opens confirmation; a session-authenticated, CSRF-protected POST creates `In Review`.
The service rechecks active validated batch/designated completed run, the selected
household's stored program finding, and the initiating active staff account in the
write transaction. Shared locks keep batch, designation, run and account state
stable through insertion. Confirmation context is validated again to reject stale
forms. Stored `For Review` requires no reason; the other two stored screening
statuses require a trimmed reason of up to 1,000 characters.

Foreign keys retain household batch, assessment run and starting staff identity.
A partial unique index on batch/household/program prevents duplicate active reviews,
including concurrent POSTs. A run redesignation does not relink or erase an existing
review. Display reads share the assessment's read-only repeatable-read snapshot.
No assessment engine, source update or stored-finding update is invoked. The new
review status is separate from screening status, priority and eligibility.

Focused tests: `python -B -m unittest discover -s tests -p test_staff_reviews.py`.
Database-writing tests require `TANGLAW_TEST_DATABASE_URL` for loopback
`tanglaw_buhay_test` and use disposable `review_check_*` schemas with synthetic
stored findings. Notes, closing outcomes, history, queue and citizen workflows
remain outside this implementation.
