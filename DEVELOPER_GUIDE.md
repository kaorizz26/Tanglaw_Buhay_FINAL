# Tanglaw-Buhay backend developer guide

This guide describes the Phase 4C vulnerability engine, Phase 5B screening
rules, and Phase 6A unified assessment / staff interface foundation. The screening package separates the four programs from shared parsing and
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
| `app.py` | Creates Flask, prepares the synthetic workspace once, and registers feature blueprints. |
| `config.py` | Reads local environment configuration. |
| `db.py` | Opens a PostgreSQL connection only when explicitly requested; the engines do not call it. |
| `migrations/` | Numbered SQL schema changes; Phase 6B.1 does not connect them to routes or engines. |
| `routes/main.py` | Provides the landing/health routes, fixture-source guard and shared pagination. |
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

The staff interface reads a synthetic batch prepared once in `create_app()`.
Public routes cannot select Excel files or connect a real CBMS source. The
`/test` route still responds without PostgreSQL. `services/assessments.py`
combines the engines by UUID and preserves each complete MPI row and all four
complete screening payloads. It rejects duplicate keys, incomplete programs,
different household sets and survey-year mismatches.

`services/development_data.py` generates 32 clearly identified sample households
using original field names. Scores, indicators and screening findings are
calculated by the existing engines, not hardcoded. The JSON in
`fixtures/development_metadata.json` is a development-only snapshot of exact
finalized metadata rows (with source hashes); it contains no household records.
It lets the UI run without the private `data/raw/` or `data/reference/` files.
Actual batch integration must provide its own finalized references; do not use
this development snapshot as an alternative production mapping.

`services/assessment_views.py` builds presentation context and decoded labels.
It shows source values next to meanings, preserves missing responses, and
identifies a household head only when exactly one roster relationship is 1.
Indicator panels display the existing engine flag and its inputs; the view layer
does not classify deprivation or recalculate filtered ranks.

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
invalid response remains unknown. Keep export spellings such as
`n01_internet_acess` unchanged.

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

## 9. Staff interface: running and extending Phase 6A

Run locally from the activated virtual environment:

```powershell
python app.py
```

Open http://127.0.0.1:5000/assessments. The entry point binds to loopback with
Flask debug disabled. Only development fixtures are served. No database,
credentials or original Excel datasets are needed to view these pages.

| Route | Purpose |
|---|---|
| `/` | Opens Assessments. |
| `/test` | Existing JSON health check. |
| `/households` | Searchable, barangay-filtered, paginated directory. |
| `/households/<uuid>` | Read-only profile, roster and source conditions. |
| `/assessments` | Prioritization with search, sorting, barangay, program-signal and MPI-coverage filters. |
| `/assessments?view=queue` | Honest placeholder for the next persistence phase. |
| `/assessments/<uuid>` | Overview, MPI Indicators, Subsidy Screening and Review History tabs. |

Use `?tab=mpi`, `?tab=subsidy&program=SLP`, or `?tab=history` for shareable
detail URLs. An ID such as `DEV-0001` identifies a synthetic fixture, not a
new official household identifier. UI ranks are labeled development-batch ranks;
a future authenticated full-municipality source will retain its original
municipality-wide ranks after any filters.

Feature routes live in `routes/households.py` and `routes/assessments.py`.
`routes/main.py` provides the fixture-source guard and shared pagination.
`templates/base.html` owns the sidebar, breadcrumbs and development notice.
Module templates inherit it; `templates/components/macros.html` shares icons,
status badges, metrics, source facts and pagination. MPI/program panels are
separate partial templates. `static/css/app.css` groups shared tokens and
component/responsive styles. `navigation.js` handles the mobile menu;
`assessment.js` handles program selection and keyboard arrow navigation.
Filters and tab links work through Flask; disclosures use native HTML details.
The layout also supplies navigation and program links when JS is unavailable.

All current summary counts come from the loaded fixture batch. A program-signal
filter means the selected program has For Review; MPI Needs Data Review is a
calculation gap, not a staff review status. Repeated requests use the startup
assessment results. Changes to the fixture generator or engines require a server
restart. Static assets do not use external fonts, frameworks or a build pipeline.

PostgreSQL persistence and staff authentication remain deferred. Review Queue
and Review History explicitly say they are unconnected; there are no fabricated
reviews, start/complete controls or temporary writes. Next-phase reviews remain
per household/program, may be continued by authorized staff, use Not Yet Reviewed
/ In Review / Completed and Further Verification Recommended / No Further Action,
and require justification when started without a historical trigger. Review
decisions must never overwrite system scores or screening results.

Before enabling actual CBMS access, add authenticated staff authorization and a
protected source/repository boundary. The current public feature routes explicitly
accept only a development workspace; there is no environment switch that exposes
real records. Do not remove that guard as a shortcut.

The tests in `tests/test_assessments.py` cover output preservation, UUID alignment,
source immutability, navigation, filters, pagination, score/rank independence and
the fixture-only boundary. Run the complete suite using the command in section 7.

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
