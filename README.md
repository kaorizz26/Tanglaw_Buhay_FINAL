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
in Pozorrubio, Pangasinan. The assessment, subsidy screening, and staff review
workflows are implemented and verified; several administration and citizen
modules are still partial or planned. **A navigation item or placeholder does not
mean a feature is complete.**

Both roles, **System Administrator** and **LGU Staff**, sign in at `/login` and
may use the Dashboard, Household Records, Household Prioritization, Household
Assessment, Staff Reviews, and Review Queue. Data Import and Staff Accounts are
administrator-only. Imported CBMS records are read-only for everyone.

### Completed Features

| Feature | Available functionality |
| --- | --- |
| Authentication and Roles | Database-backed login/logout, sessions, CSRF protection, role guards, and a shared branded sign-in page. There is no self-registration. |
| Staff Account Creation | The administrator creates LGU Staff accounts under Staff Accounts. A generated initial password is shown once, and the staff member must set their own password at first sign-in. |
| Dashboard | Historical household statistics for the designated completed run, with a Barangay filter, per-program “For Review” counts, and household assessment patterns for the 16 indicators. LGU Staff also see **My Subsidy Reviews**. The administrator Dashboard shows statistics only. |
| Household Records | Read-only directory with household ID/head search, barangay filter, pagination, and focused pages with household answers, the member roster, and a link to the assessment. |
| Household Prioritization | Stored municipal ranks with search, barangay and other filters, and pagination (25 households per page). Filters never change a rank. |
| Household Assessment | Overview, all 16 MPI indicators with View Evidence, Subsidy Screening, and Staff Reviews tabs for one household. |
| Subsidy Screening | Four separate historical screening findings for 4Ps, SLP, SPISC, and TUPAD, shown as For Review, Not Enough Information, or No Review Signal. SPISC uses a current-age recommendation. |
| Staff Reviews | Start a review for one household and program, add permanent notes, reassign the responsible staff member, finish with a recommendation, or cancel. |
| Review Queue | Municipality-wide queue with three status cards (In Review, For LGU Follow-Up, No Further Action), Assigned to Me / All Staff Reviews scope, search, filters, date sorting, and direct links to each review. |
| Recommendation Management | Correct or withdraw a completed recommendation with a reason, confirmation, and retained audit history. |
| Public Information Pages | `/public` and one page for each of 4Ps, SLP, SPISC, and TUPAD. They need no account, read no CBMS records, and accept no submissions. |
| MPI and Screening Engines | Sixteen-indicator vulnerability score and four independent subsidy engines; the designated completed assessment is **Run 3**. |
| PostgreSQL and Import Foundation | CBMS tables, batches, assessment runs, review tables (migrations 0001–0010), and a validated backend importer used once for the 2022 batch. |
| Development Review Reset | Developer-only, safeguarded reset of disposable local staff-review records; **never use on real LGU history**. |

### Partial or Incomplete

| Module | Current state |
| --- | --- |
| Staff Accounts lifecycle | Only account creation exists. Account listing, editing, deactivation, and password reset are not implemented. |
| Data Import interface | The administrator Data Import page is a placeholder. Importing is available only through the backend importer and the one-time 2022 runner script. |
| Assessment-run management | New runs are created and designated only by controlled backend scripts; there is no administrator interface. |
| Public portal | Information pages only. Citizen identity, access codes, and submissions are not implemented. |

### Planned, Not Started

| Module | Notes |
| --- | --- |
| Submissions | Staff handling of citizen-submitted documents. The staff navigation item is disabled and no workflow exists. |
| Public submission integration | Depends on the citizen identity and security decisions above. |
| Pilot verification | End-to-end verification with real LGU usage has not been done. Verification so far used synthetic data. |
| Deployment and production hardening | The built-in Flask server is for local development only. |
| Multi-year support and household progression | The database currently restricts source records to survey year 2022 and Pozorrubio; comparing periods also needs reliable household identity matching. |

### Latest Verified Test Results

- **582** full regression tests passed at commit `a95575f`.
- After the wording correction at commit `3778780`, **83 focused tests** passed
  (staff reviews, Review Queue, review responsibility, SPISC integration, and
  recommendation changes). The full suite has **not** been rerun since.
- The integrated staff workflow was verified separately using isolated synthetic
  data, not real CBMS records.

### Future CBMS Data Support

Tanglaw-Buhay is **not a one-time-use system**. Its database structure supports
multiple CBMS dataset batches and multiple assessment runs, including preservation
of historical results. The current database schema restricts source records to
**survey year 2022 and Pozorrubio**. Supporting later survey years requires an approved schema change,
even when the questionnaire structure stays the same. Changes to questionnaires
may also require approved mapping and validation updates. The administrator-facing
future-import and assessment-run workflows are **not yet implemented**.

### Development Guidelines

Create a separate feature branch for each assigned module. Read `AGENTS.md` and
the relevant parts of `docs/UI_SPEC.md` and `docs/PROJECT_CONTEXT.md` before
planning. Design for first-time, nontechnical LGU staff; preserve finalized MPI
and subsidy rules, original CBMS records, and existing review behavior. Use
synthetic data for tests, protect authorized household information, and
coordinate migrations and pull requests. See
[Safe team development](#11-safe-team-development). Using an AI assistant is
optional; see [Optional AI-assisted development](#12-optional-ai-assisted-development).

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

The current application is on the `dev/integration-admin-public` branch. `main`
is behind it and does not yet contain the Dashboard, Household Records, staff
account creation, or the refined review workflow, so do not set up from `main`.
Clone the repository and check out the integration branch:

```powershell
git clone https://github.com/kaorizz26/Tanglaw_Buhay_FINAL.git
Set-Location Tanglaw_Buhay_FINAL
git fetch origin
git switch dev/integration-admin-public
git branch --show-current
git status
code .
```

Expected: `git branch --show-current` prints `dev/integration-admin-public`, and
`git status` reports the branch is up to date with `origin/dev/integration-admin-public`
with a clean working tree. Then continue with the environment and database steps below.
If `git switch` cannot find the branch, run `git fetch origin` again and check that
your GitHub account has access to the repository.

GitHub may ask you to sign in and must grant you access to the repository.
Do not commit feature work directly on this shared branch or on `main`; create your
own feature branch from it (see [Safe team development](#11-safe-team-development)).
How and when the integration branch is merged into `main` has not been decided.

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
metadata, completed Runs 1 and 2 (Run 2 selected when the backup was prepared),
stored MPI rankings and all four program findings, and schema migrations
**0001–0008**. It retains account/review
table structures but excludes all rows from `staff_users`, `staff_reviews`,
`staff_review_notes`, `staff_review_reassignments`, and
`staff_review_recommendation_changes`. Each teammate creates their own account.

**Choose one restore method. Restore once into the empty development database.**
Do not run migrations 0001–0008 after restoring this backup. Do not rerun the
historical CBMS import, assessment persistence, or run correction/selection
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

For the backup as originally prepared, expect **19,304 households**, **69,350
members**, two completed runs with **19,304 assessments each** (38,608 total),
and batch `1` selecting Run `2`. Before administrator bootstrap, all five
account/review tables should have zero rows. Confirm these values against the
supplied manifest, which is authoritative if the owner provides a newer backup.

The project's current development database designates completed **Run 3**, which
adopts the refined SLP and TUPAD screening rules. If your restored database still
selects Run 2, or the manifest lists migrations only up to 0008, it is an older
snapshot. **Warning:** the backup you were given may contain only Runs 1–2 and
migrations 0001–0008. No updated Run 3 backup is assumed to exist. Read the
backup's JSON manifest, then confirm with the project owner which database
snapshot to restore. Do not run the assessment persistence scripts yourself; they
need separate owner approval.

The prepared handoff backup was restored into a separate disposable database.
All retained rows and schema definitions matched the source, including stored MPI
and four-program findings. Administrator creation, sign-in, and protected pages
also passed. The adjacent private manifest records the verification results.

### Apply account schema updates after restoring the handoff backup

The supplied backup includes migrations 0001–0008. Migrations 0009 and 0010 add
two account columns that must be applied **once** to each teammate's local development
database, in order. Git does not update PostgreSQL, and application startup does
not run migrations. From the project directory, use your own local database role:

```powershell
& "C:\Program Files\PostgreSQL\18\bin\psql.exe" -X -h localhost -U tanglaw_local -W -d tanglaw_buhay_dev -1 -v ON_ERROR_STOP=1 -f "migrations/0009_staff_display_name.sql"
& "C:\Program Files\PostgreSQL\18\bin\psql.exe" -X -h localhost -U tanglaw_local -W -d tanglaw_buhay_dev -1 -v ON_ERROR_STOP=1 -f "migrations/0010_staff_password_change_required.sql"
```

If using an existing development database, check `staff_users` first and run only
the missing migration(s). Do not reapply a migration whose column already exists:

```sql
SELECT column_name FROM information_schema.columns
WHERE table_schema = 'public' AND table_name = 'staff_users'
  AND column_name IN ('display_name', 'must_change_password')
ORDER BY column_name;
```

After both are applied the database matches migrations 0001–0010. Existing
accounts keep their current passwords and have `must_change_password = false`. Accounts created through the administrator page
have the flag set until their first password change. The empty test database does
not need these migrations applied manually; the tests create disposable schemas.

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
keys private; never paste them into AI assistant prompts, screenshots, or Git.

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
account. The System Administrator opens `/administration` (the Dashboard) and can
create LGU Staff accounts under Staff Accounts. Give a new staff member their generated initial
password privately; they sign in at `/login` and must set a new password before
opening staff pages. Both roles can open Household Records, Household Prioritization,
assessments, and the Review Queue (it opens on Assigned to Me for LGU Staff and
All Staff Reviews for administrators). The queue starts empty because the handoff intentionally
excludes staff reviews. `/public` shows the public program information pages
without signing in. `/test` is only a health check; it does not verify the
database. Stop the server with **Ctrl+C**.

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
CBMS data out of tests. The latest full regression passed **582 tests** at commit `a95575f`; after the
wording correction at `3778780`, 83 focused tests passed and the full suite has
not been rerun (see [Latest Verified Test Results](#latest-verified-test-results)).

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
| Staff reviews/assessments unavailable | Check restore success, migrations 0001–0010 structures, the selected completed run, and `.env`; do not blindly reapply migrations or import scripts. |
| Initial admin already exists | Bootstrap is intentionally blocked; coordinate account access instead of clearing accounts. |
| Port 5000 already in use | Stop the older local server and restart; avoid running duplicate project instances. |

Share sanitized error descriptions, never credentials or household records.

## 11. Safe team development

Begin each assigned module from the updated integration branch with a clean working
tree (until the team decides how it is merged into `main`):

```powershell
git status
git switch dev/integration-admin-public
git pull --ff-only origin dev/integration-admin-public
git switch -c dev/your-module-name
```

Replace `your-module-name` with your assigned scope. Coordinate ownership before
editing shared files, especially routes, layout, configuration, and migrations.
Use a separate branch for each module. AI assistant conversations, if you use
them, do not synchronize teammates' laptop files or database state.

Review `git diff` and `git status`; stage explicit intended paths only. After
human review, commit and push your module branch, then open a GitHub pull request
into the branch your team lead names. Have a teammate review it before the approved merge. Never force-push
shared branches, commit private data/backups, or reset/discard another person's work.
Git transfers code, not local databases; coordinate new migrations explicitly.

## 12. Optional AI-assisted development

Using an AI assistant is optional; nothing in setup, testing, or running the
application depends on one. If you choose to use any assistant, keep one approved
deliverable at a time, give it the relevant current documents and your own
sanitized results, and remember that it cannot see your local repository or
database unless you provide the information. Follow your account's usage rules and
never include private household records, backups, passwords, or `.env` contents.

A short prompt you may adapt for any assistant:

```text
We are continuing Tanglaw-Buhay (kaorizz26/Tanglaw_Buhay_FINAL), a Flask/Jinja2/
PostgreSQL decision support system for LGU/CSWDO staff in Pozorrubio.
My assigned module is: [MODULE]. My local branch is: [BRANCH].

Help me plan and review one focused task at a time. Read AGENTS.md first, then
only the relevant parts of docs/UI_SPEC.md, docs/PROJECT_CONTEXT.md,
docs/IMPLEMENTATION_PLAN.md, and DEVELOPER_GUIDE.md. For frontend work, also read
.codex/skills/tanglaw-frontend/SKILL.md. Inspect the actual affected code and do
not assume planned modules are complete.

State the objective, boundaries, acceptance criteria, and proportionate tests
before any code is written. Protect completed MPI calculations, subsidy rules,
source records, stored assessments and rankings, authentication, permissions, and
Staff Review behavior. Keep staff decisions separate from screening findings and
never describe findings as official eligibility or approval. Avoid new
dependencies and unrelated edits. Do not commit, push, migrate, reset, or run
destructive database operations without my explicit approval for that task.
Never ask for passwords, private household records, or backups.
```

## Project references

- [AGENTS.md](AGENTS.md): permanent development and data safeguards.
- [UI specification](docs/UI_SPEC.md): current staff/citizen UI standards.
- [Project context](docs/PROJECT_CONTEXT.md): purpose, boundaries, and completed functionality.
- [Implementation plan](docs/IMPLEMENTATION_PLAN.md): milestone status and remaining work.
- [Frontend skill](.codex/skills/tanglaw-frontend/SKILL.md): frontend task workflow.
- [Developer Guide](DEVELOPER_GUIDE.md): targeted technical reference; historical commands are not routine setup instructions.
