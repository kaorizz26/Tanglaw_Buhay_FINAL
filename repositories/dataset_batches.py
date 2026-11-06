"""Store and read validated CSV snapshots; the caller owns the transaction."""

from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from services import read_cache

BARANGAY_CACHE_SECONDS = 3600

SOURCE_TABLES = {
    "household": ("households", ("uuid",)),
    "section_a_e_indiv": ("household_members", ("uuid", "line_number")),
    "section_l02_to_l41": ("member_health", ("uuid", "section_l_line_number")),
    "section_l11_to_l14": ("child_death_records", ("uuid",)),
}
REFERENCE_TABLES = {
    "codebook": "codebook_values",
    "mpi_mapping": "mpi_code_mappings",
    "definitions": "variable_definitions",
}


def find_active_batches(connection):
    """Read at most two validated active batches so ambiguous state is rejected."""
    with connection.cursor(row_factory=dict_row) as cursor:
        return cursor.execute(
            """SELECT id, survey_year, municipality, source_columns,
                      source_provenance, validation_status, is_active
               FROM dataset_batches
               WHERE is_active = true AND validation_status = 'validated'
               LIMIT 2""",
        ).fetchall()


def find_validated_batch_metadata(connection, batch_id):
    """Read a specific validated batch without changing the active selection."""
    with connection.cursor(row_factory=dict_row) as cursor:
        return cursor.execute(
            """SELECT id, survey_year, municipality, source_columns, source_provenance,
                      validation_status, is_active
               FROM dataset_batches
               WHERE id = %s AND validation_status = 'validated'""",
            (batch_id,),
        ).fetchone()


def iter_batch_records(connection, batch_id, dataset_name):
    """Yield original row positions and JSON responses from a trusted table."""
    tables = {name: table for name, (table, _) in SOURCE_TABLES.items()}
    tables.update(REFERENCE_TABLES)
    if dataset_name not in tables:
        raise ValueError("Unknown batch dataset.")
    statement = sql.SQL(
        """SELECT source_row_number, responses FROM {}
           WHERE batch_id = %s ORDER BY source_row_number ASC""",
    ).format(sql.Identifier(tables[dataset_name]))
    with connection.cursor() as cursor:
        cursor.execute(statement, (batch_id,))
        yield from cursor


def find_household_identities(connection, batch_id, household_uuids):
    """Project page-scoped identities without choosing between ambiguous heads.

    The imported codebook identifies A02=1 as Head. Both joins retain the source
    batch and UUID; roster position alone does not establish household headship.
    """
    if not household_uuids:
        return []
    with connection.cursor(row_factory=dict_row) as cursor:
        return cursor.execute(
            """SELECT h.uuid AS household_uuid, h.source_row_number,
                      h.responses ->> 'barangay' AS barangay,
                      head.head_count, head.first_name, head.middle_name,
                      head.last_name
               FROM households h
               LEFT JOIN LATERAL (
                   SELECT count(*) AS head_count,
                          CASE WHEN count(*) = 1 THEN
                              max(m.responses ->> 'a01_first_name')
                          END AS first_name,
                          CASE WHEN count(*) = 1 THEN
                              max(m.responses ->> 'a01_middle_name')
                          END AS middle_name,
                          CASE WHEN count(*) = 1 THEN
                              max(m.responses ->> 'a01_last_name')
                          END AS last_name
                   FROM household_members m
                   WHERE m.batch_id = h.batch_id AND m.uuid = h.uuid
                     AND m.responses ->> 'a02_relation_to_hh_head' = '1'
               ) head ON true
               WHERE h.batch_id = %s AND h.uuid = ANY(%s)""",
            (batch_id, household_uuids),
        ).fetchall()


def list_barangays(connection, batch_id):
    """Return distinct recorded barangays from the active source batch only.

    The list is reference data of an imported batch, so it is reused per
    database and batch for an hour.
    """
    def load():
        rows = connection.execute(
            """SELECT DISTINCT btrim(responses ->> 'barangay') AS barangay
               FROM households
               WHERE batch_id = %s
                 AND btrim(responses ->> 'barangay') NOT IN ('', 'NA')
               ORDER BY barangay ASC""",
            (batch_id,),
        ).fetchall()
        return [row[0] for row in rows]

    return read_cache.cached(
        ("barangays", connection.info.dbname, batch_id), BARANGAY_CACHE_SECONDS, load,
    )


def count_household_profile(connection, batch_id, barangay=None):
    """Return batch-scoped source aggregates, never household or member rows.

    An optional recorded barangay narrows every aggregate to that barangay.
    """
    in_barangay = " AND btrim(answers.barangay) = %s" if barangay else ""
    scope = (batch_id, barangay) if barangay else (batch_id,)
    with connection.cursor(row_factory=dict_row) as cursor:
        # Each household's large answer set is read once for all four answers.
        answer_groups = cursor.execute(
            """SELECT coalesce(nullif(btrim(answers.q01_main_water), ''), '') AS water,
                      answers.r12_electricity AS electricity,
                      answers.q19_share_toilet AS toilet_sharing,
                      count(*) AS households
               FROM households h
               CROSS JOIN LATERAL jsonb_to_record(h.responses) AS answers(
                   barangay text, q01_main_water text, r12_electricity text,
                   q19_share_toilet text)
               WHERE h.batch_id = %s""" + in_barangay + """
               GROUP BY 1, 2, 3""",
            scope,
        ).fetchall()
        households = {
            "household_count": sum(row["households"] for row in answer_groups),
            "without_electricity": sum(
                row["households"] for row in answer_groups if row["electricity"] == "2"),
            "electricity_unclear": sum(
                row["households"] for row in answer_groups
                if row["electricity"] not in ("1", "2")),
            "shared_toilet": sum(
                row["households"] for row in answer_groups if row["toilet_sharing"] == "1"),
            "toilet_sharing_unclear": sum(
                row["households"] for row in answer_groups
                if row["toilet_sharing"] not in ("1", "2")),
        }
        members = cursor.execute(
            """WITH ages AS (
                   SELECT uuid,
                          CASE WHEN (responses ->> 'a07_age') ~ '^[0-9]{1,3}$'
                               THEN (responses ->> 'a07_age')::integer
                          END AS age
                   FROM household_members m WHERE m.batch_id = %s"""
            + (""" AND EXISTS (
                       SELECT 1 FROM households h
                       WHERE h.batch_id = m.batch_id AND h.uuid = m.uuid
                         AND btrim(h.responses ->> 'barangay') = %s)""" if barangay else "")
            + """
               )
               SELECT count(*) AS member_count,
                      count(DISTINCT uuid) FILTER (WHERE age < 18)
                          AS households_with_children,
                      count(DISTINCT uuid) FILTER (WHERE age >= 60)
                          AS households_with_older_adults,
                      count(DISTINCT uuid) FILTER (WHERE age IS NULL)
                          AS households_with_unclear_age
               FROM ages""",
            scope,
        ).fetchone()
        water_counts = {}
        for row in answer_groups:
            water_counts[row["water"]] = water_counts.get(row["water"], 0) + row["households"]
        water_sources = [
            {"code": code, "households": count}
            for code, count in sorted(water_counts.items(), key=lambda item: (-item[1], item[0]))
        ]
        water_labels = cursor.execute(
            """SELECT responses ->> 'code' AS code,
                      responses ->> 'label' AS label
               FROM codebook_values
               WHERE batch_id = %s
                 AND responses ->> 'variable_name' = 'q01_main_water'""",
            (batch_id,),
        ).fetchall()
    return {**households, **members, "water_sources": water_sources,
            "water_labels": water_labels}


def lock_dataset_imports(connection):
    """Serialize imports/activation while allowing ordinary readers to continue."""
    connection.execute("LOCK TABLE dataset_batches IN SHARE ROW EXCLUSIVE MODE")


def create_batch(connection, survey_year, municipality, columns, provenance):
    """Create an inactive pending batch and return its database-generated ID."""
    return connection.execute(
        """INSERT INTO dataset_batches
           (survey_year, municipality, source_columns, source_provenance)
           VALUES (%s, %s, %s, %s) RETURNING id""",
        (survey_year, municipality, Jsonb(columns), Jsonb(provenance)),
    ).fetchone()[0]


def store_batch_records(connection, batch_id, datasets, references):
    """Insert source parents before children, then preserve the three references."""
    for name, (table, keys) in SOURCE_TABLES.items():
        _store_frame(connection, batch_id, table, datasets[name], keys)
    for name, table in REFERENCE_TABLES.items():
        _store_frame(connection, batch_id, table, references[name], ())


def _store_frame(connection, batch_id, table, frame, keys):
    """Keep raw text in JSONB; derive only the schema's integer join columns."""
    columns = ("batch_id", "source_row_number", *keys, "responses")
    statement = sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(
        sql.Identifier(table),
        sql.SQL(", ").join(map(sql.Identifier, columns)),
        sql.SQL(", ").join(sql.Placeholder() for _ in columns),
    )
    with connection.cursor() as cursor:
        cursor.executemany(statement, _frame_rows(batch_id, frame, keys))


def _frame_rows(batch_id, frame, keys):
    """Yield one-based source positions without trusting a Pandas index as an ID."""
    for position, values in enumerate(frame.itertuples(index=False, name=None), 1):
        responses = dict(zip(frame.columns, values))
        identities = tuple(
            responses[key] if key == "uuid" else int(responses[key]) for key in keys
        )
        yield (batch_id, position, *identities, Jsonb(responses))


def verify_batch_counts(connection, batch_id, datasets, references):
    """Require every stored table's row count to match the validated snapshot."""
    tables = {name: table for name, (table, _) in SOURCE_TABLES.items()}
    tables.update(REFERENCE_TABLES)
    for name, frame in {**datasets, **references}.items():
        stored = connection.execute(
            sql.SQL("SELECT count(*) FROM {} WHERE batch_id = %s").format(
                sql.Identifier(tables[name]),
            ),
            (batch_id,),
        ).fetchone()[0]
        if stored != len(frame):
            raise RuntimeError("Stored batch counts do not match the validated snapshot.")


def mark_batch_validated(connection, batch_id):
    """Finish an inactive batch only after its stored snapshot has been checked."""
    changed = connection.execute(
        """UPDATE dataset_batches SET validation_status = 'validated'
           WHERE id = %s AND validation_status = 'pending' AND NOT is_active""",
        (batch_id,),
    ).rowcount
    if changed != 1:
        raise RuntimeError("The pending batch could not be validated.")


def find_matching_bundle(connection, bundle_sha256):
    """Avoid storing the same administrator bundle twice under the import lock."""
    row = connection.execute(
        """SELECT id FROM dataset_batches
           WHERE source_provenance -> 'declared' ->> 'bundle_sha256' = %s
           ORDER BY id LIMIT 1""",
        (bundle_sha256,),
    ).fetchone()
    return row[0] if row else None


def activate_batch(connection, batch_id):
    """Replace the active pointer; previous survey rows and references stay intact."""
    connection.execute("UPDATE dataset_batches SET is_active = false WHERE is_active")
    changed = connection.execute(
        """UPDATE dataset_batches SET validation_status = 'validated', is_active = true
           WHERE id = %s AND validation_status = 'pending'""",
        (batch_id,),
    ).rowcount
    if changed != 1:
        raise RuntimeError("The pending batch could not be activated.")


def list_batches_with_completed_selections(connection):
    """List validated batches with a designated completed run for admin switching."""
    with connection.cursor(row_factory=dict_row) as cursor:
        return cursor.execute(
            """SELECT b.id, b.survey_year, b.is_active,
                      r.id AS run_id, r.expected_household_count AS households
               FROM dataset_batches b
               JOIN assessment_run_selections s ON s.batch_id = b.id
               JOIN assessment_runs r ON r.id = s.run_id AND r.batch_id = b.id
               WHERE b.validation_status = 'validated' AND r.status = 'completed'
                 AND r.completed_at IS NOT NULL
               ORDER BY b.is_active DESC, b.id DESC""",
        ).fetchall()


def lock_switch_batches(connection, target_batch_id):
    """Serialize switches and lock outgoing context before counting open reviews."""
    with connection.cursor(row_factory=dict_row) as cursor:
        current = cursor.execute(
            """SELECT id, validation_status, is_active FROM dataset_batches
               WHERE is_active FOR UPDATE""",
        ).fetchall()
        target = cursor.execute(
            """SELECT id, validation_status, is_active, source_provenance
               FROM dataset_batches WHERE id = %s FOR UPDATE""",
            (target_batch_id,),
        ).fetchone()
    return current, target


def lock_designated_completed_run(connection, batch_id):
    """Keep a selected completed run stable during a batch switch."""
    with connection.cursor(row_factory=dict_row) as cursor:
        return cursor.execute(
            """SELECT r.id AS run_id, r.expected_household_count AS households,
                      r.completed_at
               FROM assessment_run_selections s
               JOIN assessment_runs r ON r.id = s.run_id AND r.batch_id = s.batch_id
               WHERE s.batch_id = %s AND r.status = 'completed'
                 AND r.completed_at IS NOT NULL
               FOR SHARE OF s, r""",
            (batch_id,),
        ).fetchone()


def count_completed_run_coverage(connection, batch_id, run_id):
    """Recheck source and stored household coverage before switching."""
    return connection.execute(
        """SELECT (SELECT count(*) FROM households WHERE batch_id = %s),
                  (SELECT count(*) FROM household_assessments
                   WHERE batch_id = %s AND run_id = %s)""",
        (batch_id, batch_id, run_id),
    ).fetchone()


def count_in_progress_reviews(connection, batch_id):
    """Count unfinished staff work on the outgoing batch only."""
    return connection.execute(
        """SELECT count(*) FROM staff_reviews
           WHERE batch_id = %s AND status = 'In Review'""",
        (batch_id,),
    ).fetchone()[0]


def count_blocking_citizen_work(connection, batch_id):
    """Count the Citizen Accounts and open document requests that belong to one batch.

    Accounts that are waiting for activation or active, and requests that are not
    finished (even those of deactivated accounts), would be invisible to LGU Staff once
    another batch became active. Both counts come from one statement, so they share
    one snapshot. The citizen tables are optional: a missing document table never hides
    existing accounts, and a missing account table means there is nothing to count.
    """
    accounts_exist, requests_exist = connection.execute(
        """SELECT to_regclass('citizen_accounts') IS NOT NULL,
                  to_regclass('document_requests') IS NOT NULL""",
    ).fetchone()
    # Only fixed text is joined below; the batch id is always a bound parameter.
    count_accounts = (
        """(SELECT count(*) FROM citizen_accounts
            WHERE batch_id = %(batch_id)s
              AND status IN ('pending_activation', 'active'))"""
        if accounts_exist else "0"
    )
    count_requests = (
        """(SELECT count(*) FROM document_requests r
            JOIN citizen_accounts a ON a.id = r.account_id
            WHERE a.batch_id = %(batch_id)s
              AND r.status IN ('requested', 'submitted', 'needs_correction'))"""
        if accounts_exist and requests_exist else "0"
    )
    accounts, requests = connection.execute(
        f"SELECT {count_accounts}, {count_requests}", {"batch_id": batch_id},
    ).fetchone()
    return {"accounts": accounts, "requests": requests}


def switch_prepared_batch(connection, previous_batch_id, target_batch_id,
                          target_run_id):
    """Move only the active pointer after the service has checked both contexts."""
    previous = connection.execute(
        """UPDATE dataset_batches SET is_active = false
           WHERE id = %s AND is_active AND validation_status = 'validated'""",
        (previous_batch_id,),
    ).rowcount
    target = connection.execute(
        """UPDATE dataset_batches b SET is_active = true
           WHERE b.id = %s AND NOT b.is_active
             AND b.validation_status = 'validated'
             AND EXISTS (
               SELECT 1 FROM assessment_run_selections s
               JOIN assessment_runs r ON r.id = s.run_id AND r.batch_id = s.batch_id
               WHERE s.batch_id = b.id AND r.id = %s
                 AND r.status = 'completed' AND r.completed_at IS NOT NULL
             )""",
        (target_batch_id, target_run_id),
    ).rowcount
    if previous != 1 or target != 1:
        raise RuntimeError("batch_switch_rejected")
