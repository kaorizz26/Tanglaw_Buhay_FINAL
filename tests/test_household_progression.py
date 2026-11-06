"""Synthetic progression checks without private CBMS data."""

from copy import deepcopy
import os
from pathlib import Path
from secrets import token_urlsafe
from time import time
import unittest
from unittest.mock import MagicMock, patch
from uuid import UUID, uuid4

from dotenv import load_dotenv
import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict
from psycopg.types.json import Jsonb

from app import create_app
from services import household_progression as progression
from repositories import household_progression as repository
from services.vulnerability.scoring import INDICATOR_WEIGHTS


HOUSEHOLD_UUID = UUID("11111111-1111-4111-8111-111111111111")
ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")
TEST_URL = os.getenv("TANGLAW_TEST_DATABASE_URL")


def period(year, batch_id, score, flags, *, unknown=0):
    mpi = {name: flags.get(name, 0) for name in INDICATOR_WEIGHTS}
    mpi.update(vulnerability_score=score, unknown_indicator_count=unknown)
    findings = {"survey_year": year, "mpi": mpi, "programs": {
        code: {"screening_status": "No Historical CBMS Trigger"}
        for code in progression.PROGRAMS
    }}
    return {"survey_year": year, "batch_id": batch_id, "run_id": batch_id + 10,
            "score": score, "findings": findings}


class ProgressionComparisonTests(unittest.TestCase):
    def setUp(self):
        self.first = period(2022, 1, 12.5, {"School Attendance": 1})
        self.second = period(2024, 2, 0, {})

    def _compare(self, first=None, second=None, *, matching=True, raw_change=False):
        first = first or self.first
        second = second or self.second
        def indicators(_, __, findings, ___):
            if findings is None:
                return {}
            return {name: {"flag": findings["mpi"][name],
                           "status": "Deprived" if findings["mpi"][name] else "Not Deprived",
                           "explanation": "Synthetic recorded condition.",
                           "raw_condition": (("Earlier source" if findings is first["findings"]
                                              else "Later source") if raw_change and name == "Water"
                                             else None)}
                    for name in INDICATOR_WEIGHTS}
        with patch.object(progression, "_mpi_signature",
                          side_effect=[("same",) if matching else ("old",),
                                       ("same",) if matching else ("new",)]), \
             patch.object(progression, "_present_indicators", side_effect=indicators):
            return progression._compare(MagicMock(), first, second, str(HOUSEHOLD_UUID))

    def test_complete_comparable_scores_and_all_indicators(self):
        result = self._compare()
        self.assertEqual(result["overall"], "Improved")
        self.assertEqual(result["score_change"], -12.5)
        self.assertEqual(sum(result["counts"].values()), 16)
        self.assertEqual(result["counts"]["Improved"], 1)
        self.assertIn("School Attendance", result["summary"])
        self.assertEqual(result["dimensions"][0]["indicators"][0]["change"], "Improved")

    def test_unknown_indicator_prevents_overall_label_but_known_change_remains(self):
        second = deepcopy(self.second)
        second["findings"]["mpi"]["Internet"] = None
        second["findings"]["mpi"]["unknown_indicator_count"] = 1
        result = self._compare(second=second)
        self.assertEqual(result["overall"], "Insufficient Data")
        self.assertIsNone(result["score_change"])
        self.assertEqual(result["counts"]["Improved"], 1)
        self.assertEqual(result["counts"]["Insufficient Data"], 1)

    def test_method_difference_blocks_all_change_claims(self):
        result = self._compare(matching=False)
        self.assertEqual(result["overall"], "Insufficient Data")
        self.assertEqual(result["counts"]["Insufficient Data"], 16)

    def test_raw_condition_change_can_keep_same_deprivation_status(self):
        result = self._compare(raw_change=True)
        housing = next(item for item in result["dimensions"]
                       if item["name"] == "Housing, Water and Sanitation")
        water = next(item for item in housing["indicators"] if item["name"] == "Water")
        self.assertEqual(water["change"], "Unchanged")
        self.assertTrue(water["raw_change"])

    def test_missing_later_assessment_has_no_progression_claim(self):
        later = {**self.second, "findings": None, "score": None}
        result = self._compare(second=later)
        self.assertEqual(result["overall"], "Insufficient Data")
        self.assertIsNone(result["score_change"])
        self.assertEqual(result["counts"]["Insufficient Data"], 16)

    def test_batch_choice_is_explicit_when_a_year_has_multiple_batches(self):
        batches = [{"survey_year": 2022, "batch_id": 1},
                   {"survey_year": 2024, "batch_id": 2},
                   {"survey_year": 2024, "batch_id": 3}]
        self.assertIsNone(progression._choose_batches(batches, None, None))
        self.assertEqual(progression._choose_batches(batches, "1", "3"), (1, 3))
        with self.assertRaises(progression.InvalidProgressionSelection):
            progression._choose_batches(batches, "3", "1")

    def test_copied_mpi_uses_its_original_run_provenance(self):
        batch = {"batch_id": 1, "calculation_provenance": {"mpi_source_run_id": 2}}
        hashes = {path.relative_to(progression.ROOT).as_posix(): "synthetic-hash"
                  for path in (progression.ROOT / "services/vulnerability").glob("*.py")}
        with patch.object(progression.household_progression, "find_run_provenance",
                          return_value={"engine_sha256": hashes}) as source, \
             patch.object(progression.household_progression, "find_mapping_fingerprint",
                          return_value="mapping-hash"):
            signature = progression._mpi_signature(MagicMock(), batch)
        self.assertIsNotNone(signature)
        source.assert_called_once()


class ProgressionRouteTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app({"TESTING": True, "SECRET_KEY": token_urlsafe(48),
                               "DATABASE_URL": None})
        self.client = self.app.test_client()
        self.user = {"id": 1, "username": "synthetic_staff", "role": "lgu_staff",
                     "is_active": True, "session_version": 1}
        for patcher in (
            patch("services.auth.get_connection", return_value=MagicMock()),
            patch("repositories.staff_users.find_by_id", side_effect=lambda *args: dict(self.user)),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def _sign_in(self):
        with self.client.session_transaction() as session:
            session.update(user_id=1, session_version=1, issued_at=int(time()),
                           csrf_token=token_urlsafe(32))

    def test_staff_guard_and_historical_search(self):
        self.assertEqual(self.client.get("/assessments/progression").location, "/login")
        self._sign_in()
        result = {"batches": [{"survey_year": 2022}], "query": "1111",
                  "matches": [{"household_uuid": str(HOUSEHOLD_UUID), "years": [2022]}]}
        with patch("routes.assessments.search_households", return_value=result) as query:
            response = self.client.get("/assessments/progression?q=1111")
        self.assertEqual(response.status_code, 200)
        query.assert_called_once_with("1111")
        self.assertIn("Household Progression", response.text)
        self.assertIn(str(HOUSEHOLD_UUID), response.text)
        self.assertIn("View history", response.text)

    def test_invalid_comparison_query_is_rejected(self):
        self._sign_in()
        response = self.client.get(
            f"/assessments/progression/{HOUSEHOLD_UUID}?earlier=1&earlier=2",
        )
        self.assertEqual(response.status_code, 400)

    def test_comparison_page_renders_scores_indicators_and_screening(self):
        self._sign_in()
        older = period(2022, 1, 12.5, {"School Attendance": 1})
        newer = period(2024, 2, 0, {})
        first = progression._period(older, older["findings"])
        second = progression._period(newer, newer["findings"])
        indicators = [{"name": name, "change": "Improved" if index == 0 else "Unchanged",
                       "before": {"status": "Deprived", "explanation": "Earlier condition."},
                       "after": {"status": "Not Deprived", "explanation": "Later condition."}}
                      for index, name in enumerate(INDICATOR_WEIGHTS)]
        result = {
            "household_uuid": str(HOUSEHOLD_UUID), "periods": [first, second],
            "available": [first, second], "selected": (1, 2),
            "earlier": first, "later": second,
            "comparison": {"overall": "Improved", "score_change": -12.5,
                           "summary": "The recorded score decreased.",
                           "counts": {"Improved": 1, "Worsened": 0,
                                      "Unchanged": 15, "Insufficient Data": 0},
                           "dimensions": [{"name": "Education", "indicators": indicators}]},
        }
        with patch("routes.assessments.get_household_progression", return_value=result):
            response = self.client.get(f"/assessments/progression/{HOUSEHOLD_UUID}")
        self.assertEqual(response.status_code, 200)
        self.assertIn("12.50", response.text)
        self.assertIn("Indicator changes", response.text)
        self.assertIn("Historical subsidy screening", response.text)
        self.assertIn("The recorded score decreased.", response.text)

    def test_single_year_page_keeps_its_historical_score_visible(self):
        self._sign_in()
        older = period(2022, 1, 12.5, {"School Attendance": 1})
        first = progression._period(older, older["findings"])
        result = {"household_uuid": str(HOUSEHOLD_UUID), "periods": [first],
                  "available": [first], "selected": None, "earlier": None,
                  "later": None, "comparison": None}
        with patch("routes.assessments.get_household_progression", return_value=result):
            response = self.client.get(f"/assessments/progression/{HOUSEHOLD_UUID}")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Available household assessments", response.text)
        self.assertIn("12.50", response.text)
        self.assertNotIn("Overall progression", response.text)


@unittest.skipUnless(TEST_URL, "Set the local isolated test database URL.")
class ProgressionPostgresTests(unittest.TestCase):
    def setUp(self):
        settings = conninfo_to_dict(TEST_URL)
        self.assertEqual(settings.get("dbname"), "tanglaw_buhay_test")
        self.assertIn(settings.get("host"), ("localhost", "127.0.0.1", "::1"))
        self.assertNotIn("service", settings)
        self.assertNotIn("options", settings)
        self.assertIn(settings.get("hostaddr", "127.0.0.1"), ("127.0.0.1", "::1"))
        self.assertFalse(any(os.getenv(name) for name in
                             ("PGSERVICE", "PGSERVICEFILE", "PGOPTIONS")))
        address = "::1" if settings["host"] == "::1" else "127.0.0.1"
        self.connection = psycopg.connect(
            TEST_URL, hostaddr=address, connect_timeout=5, autocommit=True,
        )
        self.addCleanup(self.connection.close)
        self.assertEqual(
            self.connection.execute("SELECT current_database(), host(inet_server_addr())").fetchone(),
            ("tanglaw_buhay_test", address),
        )
        self.schema = f"progression_check_{uuid4().hex}"
        with self.connection.transaction():
            self.connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(self.schema)))
            self.connection.execute(sql.SQL("SET LOCAL search_path TO {}").format(
                sql.Identifier(self.schema),
            ))
            for name in ("0001_database_foundation.sql", "0002_staff_authentication.sql",
                         "0003_assessment_persistence.sql", "0004_assessment_run_selection.sql",
                         "0013_import_validation_audit.sql", "0015_multi_year_import.sql"):
                self.connection.execute((ROOT / "migrations" / name).read_text(encoding="utf-8"))
        self.addCleanup(self._drop_schema)
        self.connection.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(self.schema)))

    def _drop_schema(self):
        self.connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(
            sql.Identifier(self.schema),
        ))

    def _store_batch(self, year, uuids, *, active=False):
        batch_id = self.connection.execute(
            """INSERT INTO dataset_batches
               (survey_year, municipality, source_columns, validation_status, is_active)
               VALUES (%s, 'Pozorrubio', %s, 'validated', %s) RETURNING id""",
            (year, Jsonb({"household": ["uuid"]}), active),
        ).fetchone()[0]
        for position, household_uuid in enumerate(uuids, 1):
            self.connection.execute(
                """INSERT INTO households (batch_id, uuid, source_row_number, responses)
                   VALUES (%s, %s, %s, %s)""",
                (batch_id, household_uuid, position, Jsonb({"uuid": household_uuid})),
            )
        self.connection.execute(
            """INSERT INTO mpi_code_mappings (batch_id, source_row_number, responses)
               VALUES (%s, 1, %s)""",
            (batch_id, Jsonb({"variable_name": "synthetic", "code": "1"})),
        )
        run_id = self.connection.execute(
            """INSERT INTO assessment_runs
               (batch_id, calculation_version, calculation_provenance,
                expected_household_count, status, completed_at)
               VALUES (%s, 'synthetic-v1', %s, %s, 'completed', now()) RETURNING id""",
            (batch_id, Jsonb({"synthetic": True}), len(uuids)),
        ).fetchone()[0]
        for household_uuid in uuids:
            findings = {
                "household_uuid": household_uuid, "survey_year": year,
                "mpi": {"uuid": household_uuid, "vulnerability_rank": 1,
                        "vulnerability_score": 0},
                "programs": {code: {"screening_status": "No Historical CBMS Trigger"}
                             for code in progression.PROGRAMS},
            }
            self.connection.execute(
                """INSERT INTO household_assessments
                   (run_id, batch_id, household_uuid, findings) VALUES (%s, %s, %s, %s)""",
                (run_id, batch_id, household_uuid, Jsonb(findings)),
            )
        self.connection.execute(
            "INSERT INTO assessment_run_selections (batch_id, run_id) VALUES (%s, %s)",
            (batch_id, run_id),
        )
        return batch_id, run_id

    def test_same_uuid_links_years_and_retains_older_only_household(self):
        shared = str(HOUSEHOLD_UUID)
        older_only = "22222222-2222-4222-8222-222222222222"
        first, _ = self._store_batch(2022, [shared, older_only])
        second, _ = self._store_batch(2024, [shared], active=True)
        self.assertEqual(
            [(row["batch_id"], row["survey_year"]) for row in
             repository.list_ready_batches(self.connection)],
            [(first, 2022), (second, 2024)],
        )
        self.assertEqual(
            [row["batch_id"] for row in repository.find_history(self.connection, shared)],
            [first, second],
        )
        self.assertEqual(
            [row["batch_id"] for row in repository.find_history(self.connection, older_only)],
            [first],
        )
        matches = repository.search_households(self.connection, "2222")
        self.assertEqual(matches[0]["years"], [2022])
        self.assertEqual(matches[0]["household_uuid"], older_only)
        self.assertEqual(repository.find_mapping_fingerprint(self.connection, first),
                         repository.find_mapping_fingerprint(self.connection, second))


if __name__ == "__main__":
    unittest.main()
