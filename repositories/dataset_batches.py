"""SQL storage for validated CSV snapshots; the caller owns the transaction."""

from psycopg import sql
from psycopg.types.json import Jsonb


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
