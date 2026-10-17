# Tanglaw-Buhay

Tanglaw-Buhay is a Flask decision support system for LGU/CSWDO personnel in
Pozorrubio, Pangasinan. It presents historical CBMS household vulnerability
assessments, separate subsidy screening findings, and attributed staff reviews.
Findings support staff judgment; they do not determine official poverty
classification, subsidy eligibility, approval, or benefit allocation.

This guide helps a teammate run the completed system on a Windows laptop.
**You need both the Git repository and the privately supplied database backup.**
Cloning Git alone does not provide household records or assessment results.

## Current Development Status

Tanglaw-Buhay is an ongoing capstone decision-support system for LGU personnel
in Pozorrubio, Pangasinan. The core assessment and subsidy screening
functionality is implemented, while several staff and citizen modules are still
planned. **A navigation item or placeholder does not mean a feature is complete.**

### Implemented Features

| Feature | Available functionality |
| --- | --- |
| Staff Authentication | Database-backed login, logout, staff sessions, and role-based access protection. |
| PostgreSQL Data Foundation | CBMS households, members, reference mappings, dataset batches, assessment runs, and review tables. |
| CBMS Data Import Foundation | Validated, transactional backend import of the authorized 2022 dataset; no general-purpose admin upload interface yet. |
| MPI Vulnerability Assessment | Sixteen indicators across four dimensions, weighted vulnerability scores, uncertainty handling, and stored results. |
| Household Prioritization | Municipality-wide rankings, search, barangay and other filters, pagination, and assessment links in stored municipal rank order. |
| Household Assessment Details | Overview, all sixteen MPI indicators, supporting evidence, and plain-language findings. |
| Subsidy Screening | Four separate historical screening outputs for 4Ps, SLP, SPISC, and TUPAD, with supporting evidence. |
| Staff Reviews | Start, notes, staff responsibility/reassignment, finish, cancel, and attributed review history. |
| Review Queue | Three clickable status cards, assigned/shared scopes, search and filters, date sorting, and review navigation. |
| Recommendation Management | Correct or withdraw a completed recommendation with a reason, confirmation, and retained audit history. |
| Development Review Reset | Developer-only, safeguarded reset of disposable local staff-review records; **never use on real LGU history**. |

### Features Still to Be Developed or Completed

These describe the intended direction; detailed workflows and acceptance criteria
still require approval.

| Module | Planned work |
| --- | --- |
| Dashboard | Municipal summary statistics, charts, and interactive analytics using stored household assessments. |
| Household Records | Searchable household directory and demographic/socioeconomic CBMS record details. |
| General Welfare Review | Staff-initiated and system-suggested welfare consideration; the suggestion rule and threshold require validation and approval. |
| Administration | Administrator screens for managing staff accounts, roles, and other authorized operations. |
| CBMS Import Management | Administrator-only upload, validation, import history, and controlled activation of additional CBMS batches. |
| Assessment Run Management | Authorized assessment execution, storage, verification, and selection for new validated batches. |
| Submissions | Staff review and management of citizen-submitted documents. |
| Citizen Portal | Public subsidy information, application guidance, and secure document dropbox using staff-issued access codes. |
| Staff Login UI Refinement | Optional visual/usability improvements to the **already functional** login page. |
| Household Progression Tracking | Comparisons across survey periods once reliable household identity matching is established. |
| End-to-End Testing and Deployment | Verify workflows, roles, privacy, security, usability, and LGU pilot readiness. |

### Future CBMS Data Support

Tanglaw-Buhay is **not a one-time-use system**. Its database structure supports
multiple CBMS dataset batches and multiple assessment runs, including preservation
of historical results. The current database schema restricts source records to
**survey year 2022 and Pozorrubio**. Supporting later survey years requires an approved schema change,
even when the questionnaire structure stays the same. Changes to questionnaires
may also require approved mapping and validation updates. The full
administrator-facing future-import and assessment-run management workflows are
**not yet implemented**. Tracking household
changes across periods additionally requires reliable identity matching.

### Development Guidelines

Create a separate feature branch for each assigned module. Use `AGENTS.md`, the
relevant project skill, and the relevant parts of `docs/UI_SPEC.md` and
`docs/PROJECT_CONTEXT.md` when planning with ChatGPT or Codex. Design for
first-time, nontechnical LGU staff; preserve finalized MPI and subsidy rules,
original CBMS records, and existing review behavior. Use synthetic data for tests,
protect authorized household information, and coordinate migrations and pull
requests. See [Safe team development](#11-safe-team-development) and
[ChatGPT Chat + Codex workflow](#12-chatgpt-chat--codex-workflow).

**Stack:** Python 3.13, Flask/Jinja2, HTML/CSS/JavaScript, PostgreSQL 18,
psycopg, pandas/NumPy, and pinned dependencies in [requirements.txt](requirements.txt).
The existing Windows environment was verified with Python 3.13.7 and PostgreSQL 18.

## 1. Install the tools

Skip tools already installed. Use a regular PowerShell terminal in VS Code.

1. Install [Git for Windows](https://git-scm.com/downloads/win), keeping Git available from the command line.
2. Install **64-bit Python 3.13** from [Python for Windows](https://www.python.org/downloads/windows/).
   Enable the Python launcher/PATH option if offered. Use 3.13 for this handoff;
   newer Python versions have not been verified with these pinned packages.
3. Install **PostgreSQL 18** using the [Windows installer](https://www.postgresql.org/download/windows/).
   Include PostgreSQL Server, command-line tools, and pgAdmin 4. Keep your own
   PostgreSQL `postgres` password privately. The usual local port is `5432`.
   Stack Builder add-ons are not required. If pgAdmin is missing, install
   [pgAdmin 4 for Windows](https://www.pgadmin.org/download/pgadmin-4-windows/).
4. Install [VS Code](https://code.visualstudio.com/download) and its Microsoft Python extension.
5. Restart VS Code/PowerShell after installation, then check:

```powershell
git --version
py -3.13 --version
& "C:\Program Files\PostgreSQL\18\bin\pg_restore.exe" --version
```

Expected: Git responds, Python reports `3.13.x`, and pg_restore reports PostgreSQL `18.x`.
If PostgreSQL was installed elsewhere, adjust its `bin` path in subsequent commands.

## 2. Clone and open the project

Clone the current stable `main` branch:

```powershell
git clone https://github.com/kaorizz26/Tanglaw_Buhay_FINAL.git
Set-Location Tanglaw_Buhay_FINAL
git switch main
git pull --ff-only origin main
code .
```

GitHub may ask you to sign in and must grant you access to the repository.
The completed handoff has been merged into `main`. Create a separate feature
branch for development; do not commit new feature work directly on `main`.

## 3. Create your Python environment

Run inside the project folder:

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m pip check
```

Expected: the prompt shows `(.venv)` and pip reports no broken requirements.
In VS Code, choose **Python: Select Interpreter → .venv\Scripts\python.exe**.
Activate the environment whenever you open a new terminal.

If PowerShell blocks activation, you can use the environment directly without
changing your security policy:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Use `.\.venv\Scripts\python.exe` in place of `python` in all later commands if
you do not activate it. Each teammate creates their own `.venv`; do not copy it.

## 4. Create two local databases in pgAdmin

These steps are for a **new teammate installation**. If either database already
exists, stop and inspect it with the team lead; do not overwrite or delete it.

1. Open pgAdmin 4. Its master password, if requested, protects saved connections;
   it is separate from the PostgreSQL server password and the application's login.
2. Expand your local PostgreSQL 18 server. If not listed, use **Register → Server**:
   name `Local PostgreSQL 18`, host `localhost`, port `5432`, username `postgres`,
   and your installation password.
3. Under **Login/Group Roles → Create → Login/Group Role**, create a local role
   named `tanglaw_local`. Set a private password in **Definition** and enable
   **Can login?** in **Privileges**. Leave superuser and role/database creation off.
4. Under **Databases → Create → Database**, create `tanglaw_buhay_dev` with
   Owner `tanglaw_local`. In **Definition**, choose template `template0` and
   encoding `UTF8`.
5. Create `tanglaw_buhay_test` with the same owner/template/encoding. Leave it
   empty. Never restore real CBMS data into the test database.

Expected: two distinct empty databases. The test suite creates disposable
synthetic schemas in `tanglaw_buhay_test`; it does not require a copy of the
development database or manually applied migrations.

## 5. Restore the private handoff backup

Obtain the owner-supplied **PostgreSQL 18 custom-format `.dump` backup and adjacent
JSON manifest** through the team's authorized private channel. Keep them together
under `notes/handoff_backups/` (Git-ignored). They contain private CBMS information:
never commit, attach to a public issue, or upload them to GitHub. Google Drive
sharing, if used, must be restricted to authorized teammates.

The handoff backup includes imported household/member/reference records, batch
metadata, completed Run 1 and selected Run 2, stored MPI rankings and all four
program findings, and schema migrations **0001–0008**. It retains account/review
table structures but excludes all rows from `staff_users`, `staff_reviews`,
`staff_review_notes`, `staff_review_reassignments`, and
`staff_review_recommendation_changes`. Each teammate creates their own account.

**Choose one restore method. Restore once into the empty development database.**
Do not run migrations 0001–0008 after restoring this backup. Do not rerun the
historical CBMS import, assessment persistence, or Run 2 correction/selection
scripts; their results are already included. Application startup never migrates.

### PowerShell method

Enter the full path to the supplied backup when prompted. The restore command
then prompts privately for the PostgreSQL `tanglaw_local` password:

```powershell
$pgBin = "C:\Program Files\PostgreSQL\18\bin"
$backupFile = Read-Host "Full path to the supplied .dump file"
$manifestFile = [System.IO.Path]::ChangeExtension($backupFile, ".json")
$manifest = Get-Content -LiteralPath $manifestFile -Raw | ConvertFrom-Json
$actualHash = (Get-FileHash -LiteralPath $backupFile -Algorithm SHA256).Hash
if ($actualHash -ine $manifest.sha256) { throw "Backup checksum does not match. Stop." }
& "$pgBin\pg_restore.exe" -h localhost -p 5432 -U tanglaw_local -W -d tanglaw_buhay_dev --no-owner --no-acl --no-tablespaces --exit-on-error --single-transaction $backupFile
if ($LASTEXITCODE -ne 0) { throw "Restore failed. Stop and inspect before continuing." }
```

The command checks SHA256 against the supplied manifest before restoring. Expected:
pg_restore exits successfully, often without output. No `--clean` or
`--create` option is needed. The account used for restoration becomes the owner
of restored objects; use the same account in `.env`.
See the official [pg_restore options](https://www.postgresql.org/docs/18/app-pgrestore.html).

### pgAdmin method

1. Register a second local server connection using `tanglaw_local` and its
   password, then select its empty `tanglaw_buhay_dev` database.
2. Right-click the database → **Restore**. Select **Custom or tar** and the `.dump` file.
3. Include **Pre-data, Data, and Post-data**. Leave **Only data** and **Only schema** off.
4. Under **Do not save**, enable **Owner**, **Privileges**, and **Tablespaces**.
5. Enable **Single transaction** and **Exit on error**. Keep **Clean before restore**
   and **Include CREATE DATABASE** off. Use one job.
6. Click **Restore** and check **Processes** for success. If the binary is missing,
   set pgAdmin's PostgreSQL binary path to `C:\Program Files\PostgreSQL\18\bin`.

Names may vary slightly by pgAdmin version; consult its
[Restore dialog documentation](https://www.pgadmin.org/docs/pgadmin4/latest/restore_dialog.html).

### Check the restored records

Open the Query Tool on **tanglaw_buhay_dev** and run this read-only check:

```sql
SELECT 'households' AS item, count(*) AS rows FROM households
UNION ALL SELECT 'household_members', count(*) FROM household_members
UNION ALL SELECT 'household_assessments', count(*) FROM household_assessments
UNION ALL SELECT 'staff_users', count(*) FROM staff_users
UNION ALL SELECT 'staff_reviews', count(*) FROM staff_reviews
UNION ALL SELECT 'staff_review_notes', count(*) FROM staff_review_notes
UNION ALL SELECT 'staff_review_reassignments', count(*) FROM staff_review_reassignments
UNION ALL SELECT 'staff_review_recommendation_changes', count(*) FROM staff_review_recommendation_changes;

SELECT id, status, expected_household_count FROM assessment_runs ORDER BY id;
SELECT batch_id, run_id FROM assessment_run_selections ORDER BY batch_id;
```

For this handoff, expect **19,304 households**, **69,350 members**, two completed
runs with **19,304 assessments each** (38,608 total), and batch `1` selecting
Run `2`. Before administrator bootstrap, all five account/review tables should
have zero rows. Confirm these values against the supplied manifest.

The prepared handoff backup was restored into a separate disposable database.
All retained rows and schema definitions matched the source, including stored MPI
and four-program findings. Administrator creation, sign-in, and protected pages
also passed. The adjacent private manifest records the verification results.

## 6. Configure your private `.env`

Copy the template once. If `.env` already exists, edit it without overwriting it:

```powershell
Copy-Item .env.example .env
python -c "import secrets; print(secrets.token_hex(32))"
```

Paste the generated random value into `SECRET_KEY`. Edit `.env` locally:

```dotenv
DATABASE_URL=postgresql://tanglaw_local:YOUR_URL_ENCODED_PASSWORD@localhost:5432/tanglaw_buhay_dev
TANGLAW_TEST_DATABASE_URL=postgresql://tanglaw_local:YOUR_URL_ENCODED_PASSWORD@localhost:5432/tanglaw_buhay_test
SECRET_KEY=PASTE_YOUR_GENERATED_RANDOM_VALUE_HERE
SESSION_COOKIE_SECURE=false
```

Replace every placeholder. URL-encode special password characters such as `@`,
`#`, `%`, and `/` in the connection URLs; pgAdmin still takes the original password.
Use `SESSION_COOKIE_SECURE=false` **only for local loopback HTTP development**.
Deployment requires HTTPS and secure cookies. Keep `.env`, passwords, and signing
keys private; never paste them into ChatGPT, Codex prompts, screenshots, or Git.

## 7. Create your System Administrator account

In an interactive VS Code PowerShell terminal, run:

```powershell
python -m flask --app app create-admin
```

Enter your chosen application username (3–64 letters/digits, dot, dash, or
underscore) and a private password of at least 12 characters, then confirm it.
The password is hidden while typing. Expected:
`Initial administrator created. No survey records were changed.`

This command permits only the local `tanglaw_buhay_dev` target and an empty
account table. It is an initial bootstrap, not an account-management/reset tool.
Your application account is different from your PostgreSQL role. Do not reuse
the original programmer's application credentials.

## 8. Start and check the application

```powershell
python app.py
```

Open [Staff sign in](http://127.0.0.1:5000/login) and use your new application
account. Visit Household Prioritization, open a household assessment, and inspect
Overview, MPI evidence, and Subsidy Screening. Review Queue starts empty because
the handoff intentionally excludes staff reviews. `/test` is only a health check;
it does not verify the database. Stop the server with **Ctrl+C**.

The built-in Flask server is for local development; production deployment is
pending. Restart it after code changes because `python app.py` disables debug reload.

## 9. Authorized source files and tests

Privately obtain authorized source/reference files from the team lead's Google
Drive folder if your assigned development needs them. Keep original filenames:
CBMS household/member source files go in `data/raw/`; approved codebooks,
crosswalks, and reference material go in `data/reference/`. Both directories'
contents are Git-ignored. The backup already contains imported records, so these
files are for authorized reference/future work, not a request to reimport.

The controlled CSV import expects these exact filenames:

| Folder | CSV filename |
| --- | --- |
| `data/raw/` | `household.csv` |
| `data/raw/` | `section_a_e_indiv.csv` |
| `data/raw/` | `section_l02_to_l41.csv` |
| `data/raw/` | `section_l11_to_l14.csv` |
| `data/reference/` | `Tanglaw_Buhay_Codebook_Values_FINAL.csv` |
| `data/reference/` | `Tanglaw_Buhay_MPI_Mapping_FinalProbably.csv` |
| `data/reference/` | `Tanglaw_Buhay_Variable_Definitions_FINAL.csv` |

Keep the mapping filename exactly as shown, including `FinalProbably`. The older
Excel loader uses matching `.xlsx` source names; it is not the routine restored
database setup path. Do not rename formats or run either import path during setup.

For an approved task, run proportionate focused tests. For backend changes or
final integration, the existing full regression command is:

```powershell
$env:OPENBLAS_NUM_THREADS = "1"
python -B -m unittest discover -s tests
```

Confirm `TANGLAW_TEST_DATABASE_URL` points only to your empty, local
`tanglaw_buhay_test`. Database tests must execute, not silently skip. Keep actual
CBMS data out of tests. The last application regression before this documentation
handoff passed **381 tests**; the separate reset safety check passed **7 tests**.

## 10. Troubleshooting

| Symptom | What to check |
| --- | --- |
| `py`, Git, or Python not found | Restart the terminal; check installation/PATH and use `py -3.13`. |
| Missing Python module | Use the project `.venv`, install `requirements.txt`, and check the selected VS Code interpreter. |
| PostgreSQL connection refused | Check the PostgreSQL 18 Windows service, local host/port, and `.env`. |
| PostgreSQL password fails | Check the role/password, URL encoding, and database ownership; the app login is a different account. |
| Restore says an object already exists | Stop. Check that this is a new empty database; do not add `--clean` or delete existing work. |
| Restore says role is missing | Use `--no-owner --no-acl` with the local database owner as shown above. |
| Sign-in unavailable or does not persist | Set a random `SECRET_KEY` of at least 32 characters; use `SESSION_COOKIE_SECURE=false` only on local HTTP; restart the app. |
| Staff reviews/assessments unavailable | Check restore success, migrations 0001–0008 structures, selected completed Run 2, and `.env`; do not blindly reapply migrations or import scripts. |
| Initial admin already exists | Bootstrap is intentionally blocked; coordinate account access instead of clearing accounts. |
| Port 5000 already in use | Stop the older local server and restart; avoid running duplicate project instances. |

Share sanitized error descriptions, never credentials or household records.

## 11. Safe team development

Begin each assigned module from updated `main` with a clean working tree:

```powershell
git status
git switch main
git pull --ff-only origin main
git switch -c dev/your-module-name
```

Replace `your-module-name` with your assigned scope. Coordinate ownership before
editing shared files, especially routes, layout, configuration, and migrations.
Use separate branches and separate ChatGPT/Codex conversations for each module;
conversations do not synchronize teammates' laptop files or database state.

Review `git diff` and `git status`; stage explicit intended paths only. After
human review, commit and push your module branch, then open a GitHub pull request
into `main`. Have a teammate review it before the approved merge. Never force-push
shared branches, commit private data/backups, or reset/discard another person's work.
Git transfers code, not local databases; coordinate new migrations explicitly.

## 12. ChatGPT Chat + Codex workflow

Use **ChatGPT Chat → planning/brainstorming → approved Codex prompt → Codex
implementation → ChatGPT review → next task**. Keep one approved deliverable at
a time. Give ChatGPT the relevant current documents and sanitized Codex result;
it should not assume it has inspected your local repository or another conversation.
Use focused Codex tasks for implementation and verification rather than repeated
repository-wide audits. This is our team workflow, not a guarantee about account
usage limits. Follow the account's access rules and never include private records.

### Copy-paste onboarding prompt for ChatGPT Chat

Paste this into a **new ChatGPT Chat conversation**, then supply the referenced
documents and name your assigned module:

```text
We are continuing Tanglaw-Buhay (kaorizz26/Tanglaw_Buhay_FINAL), a Flask/Jinja2/
PostgreSQL decision support system for LGU/CSWDO staff in Pozorrubio.
My assigned module is: [MODULE]. My local branch is: [BRANCH].

Act as my planning and review partner. Proceed with caution. First understand the
existing implementation from the current documents, relevant code I provide, and
sanitized Codex reports. Do not claim to have inspected files you cannot access.
Ask for missing relevant context; do not assume all planned modules are complete.

Workflow: ChatGPT Chat -> planning and brainstorming with me -> I approve the
scope -> you write one focused, copy-paste-ready Codex task -> Codex implements
and reports -> we review together -> we choose the next task.
Do not generate an implementation prompt before we agree on the task.

Every Codex task must specify its objective, boundaries, acceptance criteria,
proportionate tests, and relevant visual checks. Tell Codex to read AGENTS.md;
for frontend work, read .codex/skills/tanglaw-frontend/SKILL.md; inspect the actual
affected code and relevant sections of docs/UI_SPEC.md, docs/PROJECT_CONTEXT.md,
and docs/IMPLEMENTATION_PLAN.md. Use targeted DEVELOPER_GUIDE.md sections only
when needed; never require reading the entire guide by default.

Protect completed MPI calculations, mappings, weights, unknown handling,
subsidy rules, source records, stored assessments/rankings, authentication,
permissions, and Staff Review behavior. Keep staff decisions separate from
screening findings. Never describe findings as official eligibility or approval.
Avoid overengineering, new dependencies, unrelated edits, repeated broad audits,
and unnecessary test reruns. Preserve teammate work and coordinate shared files.
Never authorize commits, pushes, merges, migrations, resets, or destructive
database operations automatically; require my explicit task-specific approval.
Never request passwords, private household records, or backups in the chat.

When I say "proceed with caution", apply these standards throughout:
Primary users are first-time, nontechnical LGU personnel. Every staff screen
must be professional, understandable, practical, accessible, and responsive,
using the existing navy/gold design. Make the page purpose, household/program,
status, available action, consequence, and return navigation clear. Use readable
typography, spacing, labels, colors, tables, and forms. Explain important scores,
historical findings, unknowns, and workflow statuses briefly in everyday language.
Avoid plain unfinished, article-like, overcrowded, repetitive, or developer-facing
operational screens. Omit technical methodology, provenance stories, and internal
implementation notes from staff screens; keep meaningful uncertainty visible.

Start by helping me understand my module's current state and plan its smallest
useful next deliverable. Do not start coding or authorize Git/database changes.
```

OpenAI's [Codex best practices](https://developers.openai.com/codex/learn/best-practices/)
provide further guidance on context, task scope, and verification.

## Project references

- [AGENTS.md](AGENTS.md): permanent development and data safeguards.
- [UI specification](docs/UI_SPEC.md): current staff/citizen UI standards.
- [Project context](docs/PROJECT_CONTEXT.md): purpose, boundaries, and completed functionality.
- [Implementation plan](docs/IMPLEMENTATION_PLAN.md): milestone status and remaining work.
- [Frontend skill](.codex/skills/tanglaw-frontend/SKILL.md): frontend task workflow.
- [Developer Guide](DEVELOPER_GUIDE.md): targeted technical reference; historical commands are not routine setup instructions.
