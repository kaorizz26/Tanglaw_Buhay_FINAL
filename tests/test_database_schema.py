"""Exercise the first migration only against a local synthetic test database."""

import os
from pathlib import Path
import unittest
from uuid import uuid4

import psycopg
from dotenv import load_dotenv
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict
from psycopg.types.json import Jsonb


MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "migrations"
    / "0001_database_foundation.sql"
)
load_dotenv(Path(__file__).resolve().parents[1] / ".env")
TEST_URL = os.getenv("TANGLAW_TEST_DATABASE_URL")


@unittest.skipUnless(TEST_URL, "Set TANGLAW_TEST_DATABASE_URL for a local synthetic test database.")
class DatabaseSchemaTests(unittest.TestCase):
    def test_keys_batches_and_source_values(self):
        """Check the approved joins, active-batch rule and lossless value shapes."""
        settings = conninfo_to_dict(TEST_URL)
        self.assertEqual(settings.get("dbname"), "tanglaw_buhay_test")
        self.assertIn(settings.get("host"), ("localhost", "127.0.0.1", "::1"))
        self.assertIn(settings.get("hostaddr", "127.0.0.1"), ("127.0.0.1", "::1"))
        self.assertNotIn("service", settings)

        schema_name = f"schema_check_{uuid4().hex}"
        with psycopg.connect(TEST_URL, hostaddr="127.0.0.1") as connection:
            try:
                connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema_name)))
                connection.execute(
                    sql.SQL("SET LOCAL search_path TO {}").format(sql.Identifier(schema_name))
                )
                connection.execute(MIGRATION.read_text(encoding="utf-8"))
                self._check_synthetic_rows(connection)
            finally:
                # DDL and data stay in this transaction, including on assertion failure.
                connection.rollback()

    def _check_synthetic_rows(self, connection):
        """Insert safe sample rows, then test constraints using savepoints."""
        columns = Jsonb({
            "household": ["uuid", "blank_answer", "literal_na"],
            "section_a_e_indiv": ["uuid", "line_number"],
            "section_l02_to_l41": ["uuid", "section_l_line_number"],
            "section_l11_to_l14": ["uuid"],
            "codebook": ["code", "label"],
            "mpi_mapping": ["code", "deprived"],
            "definitions": ["variable_name", "provenance"],
        })
        provenance = Jsonb({"example": "synthetic test only"})
        batch_id = connection.execute(
            """INSERT INTO dataset_batches
               (survey_year, municipality, source_columns, source_provenance,
                validation_status, is_active)
               VALUES (2022, 'Pozorrubio', %s, %s, 'validated', true)
               RETURNING id""",
            (columns, provenance),
        ).fetchone()[0]
        self._reject(connection, psycopg.errors.CheckViolation,
                     "UPDATE dataset_batches SET validation_status = 'failed' WHERE id = %s",
                     (batch_id,))
        self._reject(connection, psycopg.errors.UniqueViolation,
                     """INSERT INTO dataset_batches
                        (survey_year, municipality, source_columns, validation_status, is_active)
                        VALUES (2022, 'Pozorrubio', %s, 'validated', true)""",
                     (columns,))
        other_batch = connection.execute(
            """INSERT INTO dataset_batches (survey_year, municipality, source_columns)
               VALUES (2022, 'Pozorrubio', %s) RETURNING id""",
            (columns,),
        ).fetchone()[0]

        household = {"uuid": "synthetic-household", "blank_answer": "", "literal_na": "NA"}
        connection.execute(
            "INSERT INTO households VALUES (%s, %s, 1, %s)",
            (batch_id, household["uuid"], Jsonb(household)),
        )
        connection.execute(
            "INSERT INTO household_members VALUES (%s, %s, 7, 1, %s)",
            (batch_id, household["uuid"], Jsonb({"uuid": household["uuid"], "line_number": 7})),
        )
        connection.execute(
            "INSERT INTO member_health VALUES (%s, %s, 7, 1, %s)",
            (batch_id, household["uuid"],
             Jsonb({"uuid": household["uuid"], "section_l_line_number": 7})),
        )
        for row_number in (1, 2):
            connection.execute(
                "INSERT INTO child_death_records VALUES (%s, %s, %s, %s)",
                (batch_id, row_number, household["uuid"], Jsonb({"uuid": household["uuid"]})),
            )
        self.assertEqual(connection.execute(
            "SELECT count(*) FROM child_death_records WHERE batch_id = %s", (batch_id,)
        ).fetchone()[0], 2)
        self.assertEqual(connection.execute(
            "SELECT responses FROM households WHERE batch_id = %s", (batch_id,)
        ).fetchone()[0], household)
        self.assertEqual(connection.execute(
            "SELECT source_columns -> 'household' FROM dataset_batches WHERE id = %s",
            (batch_id,),
        ).fetchone()[0], ["uuid", "blank_answer", "literal_na"])

        self._reject(connection, psycopg.errors.UniqueViolation,
                     "INSERT INTO households VALUES (%s, %s, 1, %s)",
                     (batch_id, "another-synthetic-household", Jsonb({})))
        self._reject(connection, psycopg.errors.ForeignKeyViolation,
                     "INSERT INTO household_members VALUES (%s, %s, 1, 1, %s)",
                     (other_batch, household["uuid"], Jsonb({})))
        self._reject(connection, psycopg.errors.ForeignKeyViolation,
                     "INSERT INTO member_health VALUES (%s, %s, 8, 2, %s)",
                     (batch_id, household["uuid"], Jsonb({})))
        self._reject(connection, psycopg.errors.ForeignKeyViolation,
                     "INSERT INTO child_death_records VALUES (%s, 3, %s, %s)",
                     (batch_id, "missing-synthetic-household", Jsonb({})))

        for table, row in (
            ("codebook_values", {"code": "001", "label": "NA"}),
            ("mpi_code_mappings", {"code": "001", "deprived": ""}),
            ("variable_definitions", {"variable_name": "example", "provenance": "synthetic"}),
        ):
            connection.execute(
                sql.SQL("INSERT INTO {} VALUES (%s, 1, %s)").format(sql.Identifier(table)),
                (batch_id, Jsonb(row)),
            )
            stored = connection.execute(
                sql.SQL("SELECT responses FROM {} WHERE batch_id = %s").format(
                    sql.Identifier(table)
                ),
                (batch_id,),
            ).fetchone()[0]
            self.assertEqual(stored, row)

    def _reject(self, connection, error_type, statement, parameters):
        """Rollback one expected constraint failure without aborting the test."""
        with self.assertRaises(error_type):
            with connection.transaction():
                connection.execute(statement, parameters)


if __name__ == "__main__":
    unittest.main()
