"""Stored-query tests use synthetic findings and isolated local PostgreSQL schemas."""

from copy import deepcopy
from itertools import combinations
import os
from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch
from uuid import uuid4

from dotenv import load_dotenv
import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict
from psycopg.pq import TransactionStatus
from psycopg.types.json import Jsonb

from repositories import assessment_runs as repository
from repositories import assessment_evidence
from assessment_detail_fixtures import HOUSEHOLD_UUID, detail_source, screening_source
from services import assessment_queries as service, read_cache
from services import household_records as record_service
from services.assessment_filters import (
    AssessmentFilters, InvalidAssessmentFilters, PROGRAM_ORDER, normalize_filters,
)
from services.assessments import build_unified_assessments
from services.development_data import development_sources
from services.assessment_evidence_view import DISPLAY_NAMES
from services.vulnerability.scoring import INDICATOR_WEIGHTS
from services.subsidy_priority import (
    NO_SUBSIDY_PRIORITY, PRIMARY_PRIORITY, SECONDARY_PRIORITY,
)


ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")
TEST_URL = os.getenv("TANGLAW_TEST_DATABASE_URL")


class AssessmentQueryServiceTests(unittest.TestCase):
    def setUp(self):
        read_cache.clear()
        self.addCleanup(read_cache.clear)
        self.connection = MagicMock()
        self.connection.__enter__.return_value = self.connection
        self.connection.info.transaction_status = TransactionStatus.IDLE
        self.factory = MagicMock(return_value=self.connection)
        ages = patch.object(assessment_evidence, 'list_recorded_ages', return_value=[])
        ages.start()
        self.addCleanup(ages.stop)
        self.batch = {"id": 9, "survey_year": 2022, "municipality": "Pozorrubio",
                      "validation_status": "validated", "is_active": True,
                      "source_provenance": {"kind": "synthetic"}}
        self.run = {"run_id": 17, "batch_id": 9, "calculation_version": "synthetic-v1",
                    "calculation_provenance": {"kind": "synthetic"},
                    "completed_at": "synthetic timestamp", "household_count": 32}
        mock = patch.object(service.dataset_batches, "find_active_batches", return_value=[self.batch])
        mock.start()
        self.addCleanup(mock.stop)
        mock = patch.object(repository, "find_designated_run", return_value=self.run)
        self.runs = mock.start()
        self.addCleanup(mock.stop)
        mock = patch("psycopg.connect", side_effect=AssertionError("No real connections in unit tests"))
        mock.start()
        self.addCleanup(mock.stop)

    def test_context_and_page_use_one_read_only_snapshot(self):
        with patch.object(repository, "list_assessment_summaries", return_value=[]) as query:
            result = service.list_active_assessment_summaries(
                page=3, page_size=7, connection_factory=self.factory,
            )
        self.assertEqual(result["context"]["batch_id"], 9)
        self.assertEqual(result["context"]["survey_year"], 2022)
        self.assertEqual(result["total"], 32)
        query.assert_called_once_with(self.connection, 17, limit=7, offset=14,
                                      spisc_age_statuses={}, batch_id=9)
        self.connection.execute.assert_called_once_with(
            "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY",
        )
        self.factory.assert_called_once_with()

    def test_dashboard_reads_aggregates_only_and_rejects_count_mismatch(self):
        priority = {"all": 32, "primary": 5, "secondary": 7, "both": 12, "other": 20}
        findings = {"assessed_households": 32, "needs_attention": 3,
                    "four_ps": 8, "slp": 6, "spisc": 2, "tupad": 1}
        household_profile = {
            "household_count": 32, "member_count": 112,
            "households_with_children": 18, "households_with_older_adults": 9,
            "households_with_unclear_age": 0, "without_electricity": 2,
            "electricity_unclear": 0, "shared_toilet": 4,
            "toilet_sharing_unclear": 0,
            "water_sources": [{"code": "1", "households": 24},
                              {"code": "4", "households": 8}],
            "water_labels": [{"code": "1", "label": "Piped into dwelling"},
                             {"code": "4", "label": "Protected well"}],
        }
        indicators = {"assessed_households": 32, "indicators": {
            name: {"deprived": 5, "not_deprived": 20, "unknown": 7}
            for name in INDICATOR_WEIGHTS}}
        overview = {"priority": priority, "findings": findings}
        with patch.object(repository, "count_dashboard_overview", return_value=overview) as counts, \
                patch.object(repository, "count_indicator_findings", return_value=indicators) as patterns, \
                patch.object(service.dataset_batches, "count_household_profile",
                             return_value=household_profile) as profile, \
                patch.object(service.dataset_batches, "list_barangays", return_value=[]),                 patch.object(repository, "list_assessment_summaries") as rows:
            result = service.get_active_dashboard_summary(connection_factory=self.factory)
        self.assertEqual(result["priority"], priority)
        self.assertEqual(result["findings"], findings)
        self.assertEqual(result["household_profile"]["average_members"], 3.5)
        self.assertEqual(result["household_profile"]["water_sources"][0]["percent"], 75.0)
        patterns.assert_called_once_with(self.connection, 17, filters=AssessmentFilters())
        self.assertEqual([area["key"] for area in result["indicator_patterns"]],
                         ["education", "health", "housing", "employment"])
        patterns.assert_called_once_with(self.connection, 17, filters=AssessmentFilters())
        self.assertEqual([area["key"] for area in result["indicator_patterns"]],
                         ["education", "health", "housing", "employment"])
        counts.assert_called_once_with(
            self.connection, 17, filters=AssessmentFilters(),
            spisc_age_statuses={}, batch_id=9)
        profile.assert_called_once_with(self.connection, 9, None)
        rows.assert_not_called()
        self.connection.execute.assert_called_once_with(
            "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY",
        )
        read_cache.clear()
        with patch.object(repository, "count_dashboard_overview", return_value={
                    "priority": priority,
                    "findings": {**findings, "assessed_households": 31}}), \
                patch.object(repository, "count_indicator_findings", return_value=indicators), \
                patch.object(service.dataset_batches, "count_household_profile",
                             return_value=household_profile),                 patch.object(service.dataset_batches, "list_barangays", return_value=[]):
            with self.assertRaisesRegex(service.AssessmentQueryError, "assessment_count_mismatch"):
                service.get_active_dashboard_summary(connection_factory=self.factory)

    def test_household_profile_keeps_unclear_water_responses_separate(self):
        raw = {
            "household_count": 10, "member_count": 34,
            "households_with_children": 6, "households_with_older_adults": 3,
            "households_with_unclear_age": 1, "without_electricity": 2,
            "electricity_unclear": 1, "shared_toilet": 4,
            "toilet_sharing_unclear": 1,
            "water_sources": [
                {"code": "1", "households": 4},
                {"code": "1.0", "households": 1},
                {"code": "4", "households": 2},
                {"code": "2", "households": 1}, {"code": "10", "households": 1},
                {"code": "NA", "households": 1},
            ],
            "water_labels": [
                {"code": "1", "label": "Piped dwelling"},
                {"code": "4", "label": "Protected well"},
                {"code": "2", "label": "Piped yard"},
                {"code": "10", "label": "Other source"},
            ],
        }
        profile = service._present_household_profile(raw)
        self.assertEqual(profile["average_members"], 3.4)
        self.assertEqual([item["count"] for item in profile["water_sources"]],
                         [5, 2, 1, 1, 1])
        self.assertEqual(profile["water_sources"][-1]["label"],
                         "Not recorded or unclear")

    def test_detail_sources_and_references_share_one_designated_read_snapshot(self):
        source = detail_source()
        with patch.object(repository, "find_household_assessment", return_value=source["findings"]) as finding, \
                patch.object(service.dataset_batches, "find_household_identities", return_value=[source["identity"]]) as identity, \
                patch.object(assessment_evidence, "find_household_sources", return_value=source["household"]) as household, \
                patch.object(assessment_evidence, "list_member_sources", return_value=source["members"]) as members, \
                patch.object(assessment_evidence, "list_evidence_references", return_value=source["references"]) as references:
            result = service.get_active_assessment_detail(HOUSEHOLD_UUID, connection_factory=self.factory)
            self.assertEqual(result["findings"], source["findings"])
            finding.assert_called_once_with(self.connection, 17, HOUSEHOLD_UUID)
            identity.assert_called_once_with(self.connection, 9, [HOUSEHOLD_UUID])
            household.assert_called_once_with(self.connection, 9, HOUSEHOLD_UUID)
            members.assert_called_once_with(self.connection, 9, HOUSEHOLD_UUID)
            references.assert_called_once_with(self.connection, 9)
            self.factory.assert_called_once()
            self.connection.execute.assert_called_once_with(
                "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY",
            )

    def test_missing_detail_never_loads_source_evidence(self):
        with patch.object(repository, "find_household_assessment", return_value=None), \
                patch.object(assessment_evidence, "list_member_sources") as members:
            with self.assertRaisesRegex(service.AssessmentQueryError, "household_assessment_missing"):
                service.get_active_assessment_detail(HOUSEHOLD_UUID, connection_factory=self.factory)
            members.assert_not_called()

    def test_staff_reviews_share_the_designated_detail_read_only_snapshot(self):
        source = detail_source()
        with patch.object(repository, "find_household_assessment", return_value=source["findings"]), \
                patch.object(service.dataset_batches, "find_household_identities", return_value=[source["identity"]]), \
                patch.object(assessment_evidence, "find_household_sources", return_value=source["household"]), \
                patch.object(assessment_evidence, "list_member_sources", return_value=source["members"]), \
                patch.object(assessment_evidence, "list_evidence_references", return_value=source["references"]), \
                patch.object(service.staff_reviews, "list_history", return_value=[]) as reviews:
            result = service.get_active_assessment_detail(
                HOUSEHOLD_UUID, include_staff_reviews=True, connection_factory=self.factory,
            )
        reviews.assert_called_once_with(self.connection, 9, HOUSEHOLD_UUID)
        self.assertEqual(result["staff_reviews"], [])
        self.factory.assert_called_once()
        self.connection.execute.assert_called_once_with(
            "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY",
        )

    def test_filtered_count_page_and_barangays_share_designated_snapshot(self):
        filters = AssessmentFilters(
            programs=("4Ps", "SLP"), completeness="unknown", priority="both",
        )
        with patch.object(repository, "count_assessment_summaries", return_value=3) as count, \
                patch.object(repository, "list_assessment_summaries", return_value=[]) as page, \
                patch.object(service.dataset_batches, "list_barangays", return_value=["Synthetic East"]) as options, \
                patch.object(repository, "count_priority_groups", return_value={"all": 8}) as groups:
            result = service.list_active_assessment_summaries(
                page=2, filters=filters, include_barangays=True, include_priority_counts=True,
                connection_factory=self.factory,
            )
        self.assertEqual(result["total"], 3)
        self.assertEqual(result["barangays"], ["Synthetic East"])
        self.assertEqual(result["priority_counts"], {"all": 8})
        groups.assert_called_once_with(self.connection, 17, filters=filters,
                                       spisc_age_statuses={}, batch_id=9)
        count.assert_called_once_with(self.connection, 17, filters=filters,
                                      spisc_age_statuses={}, batch_id=9)
        page.assert_called_once_with(self.connection, 17, limit=25, offset=25, filters=filters,
                                     spisc_age_statuses={}, batch_id=9)
        options.assert_called_once_with(self.connection, 9)
        self.factory.assert_called_once()

    def test_invalid_filters_fail_before_connecting_and_valid_values_normalize(self):
        for filters in (
            {}, AssessmentFilters(search=2), AssessmentFilters(barangay=None),
            AssessmentFilters(programs=("INVALID",)), AssessmentFilters(completeness=[]),
            AssessmentFilters(subsidy_mode="invalid"), AssessmentFilters(subsidy_mode=[]),
            AssessmentFilters(priority="invalid"), AssessmentFilters(priority=[]),
        ):
            with self.subTest(filters=filters), self.assertRaises(InvalidAssessmentFilters):
                service.list_active_assessment_summaries(filters=filters, connection_factory=self.factory)
        self.factory.assert_not_called()
        self.assertEqual(
            normalize_filters(AssessmentFilters(
                search="  Synthetic   Name ", barangay=" East ", programs=("TUPAD", "4Ps", "4Ps"),
            )),
            AssessmentFilters(search="Synthetic Name", barangay="East", programs=("4Ps", "TUPAD")),
        )

    def test_priority_summaries_preserve_stored_values_and_future_context(self):
        self.batch["survey_year"] = 2030
        items = [
            {"household_uuid": "SYNTHETIC_PRIMARY", "vulnerability_score": 30,
             "vulnerability_rank": 8, "unknown_indicator_count": 2,
             "program_statuses": {"SLP": "For Review"}},
            {"household_uuid": "SYNTHETIC_SECONDARY", "vulnerability_score": 25,
             "vulnerability_rank": 19, "unknown_indicator_count": 1,
             "program_statuses": {"4Ps": "For Review"}},
            {"household_uuid": "SYNTHETIC_OTHER", "vulnerability_score": 40,
             "vulnerability_rank": 3, "unknown_indicator_count": 0,
             "program_statuses": {"SLP": "Needs Data Review"}},
        ]
        before = deepcopy(items)
        with patch.object(repository, "list_assessment_summaries", return_value=items):
            result = service.list_active_assessment_summaries(connection_factory=self.factory)
        self.assertEqual(result["context"]["survey_year"], 2030)
        self.assertEqual(result["context"]["batch_id"], 9)
        self.assertEqual(result["context"]["run_id"], 17)
        self.assertEqual([row["subsidy_priority"] for row in result["items"]],
                         [PRIMARY_PRIORITY, SECONDARY_PRIORITY, NO_SUBSIDY_PRIORITY])
        self.assertEqual([{key: value for key, value in row.items() if key != "subsidy_priority"}
                          for row in result["items"]], before)
        self.assertEqual(items, before)

    def test_exact_sql_binds_all_programs_and_no_selection_adds_no_predicate(self):
        filters = AssessmentFilters(programs=("4Ps", "TUPAD"), subsidy_mode="exact")
        selection, parameters = repository._summary_selection(17, filters)
        self.assertEqual(selection.count("= 'For Review'"), 2)
        self.assertEqual(selection.count("IS DISTINCT FROM 'For Review'"), 2)
        self.assertEqual(parameters, (
            17, *(["programs", code, "screening_status"] for code in PROGRAM_ORDER),
        ))
        self.assertEqual(
            repository._summary_selection(17, AssessmentFilters(subsidy_mode="exact")),
            repository._summary_selection(17, AssessmentFilters()),
        )
        mode_only = normalize_filters(AssessmentFilters(subsidy_mode="exact"))
        self.assertFalse(mode_only.active)
        self.assertEqual(mode_only.parameters, {"subsidy_mode": "exact"})

    def test_missing_ambiguous_or_invalid_active_batch_fails_safely(self):
        invalid = {**self.batch, "validation_status": "pending"}
        for batches, code in (([], "active_batch_missing"), ([self.batch, self.batch], "active_batch_ambiguous"),
                              ([invalid], "active_batch_invalid")):
            with self.subTest(code=code), \
                    patch.object(service.dataset_batches, "find_active_batches", return_value=batches):
                with self.assertRaisesRegex(service.AssessmentQueryError, code):
                    service.get_active_assessment_context(connection_factory=self.factory)

    def test_identity_lookup_is_bounded_to_page_and_uses_same_snapshot(self):
        items = [
            {"household_uuid": uuid, "vulnerability_score": 25,
             "program_statuses": {"4Ps": "For Review"}}
            for uuid in ("SYNTHETIC_B", "SYNTHETIC_A")
        ]
        identities = [{"household_uuid": "SYNTHETIC_A"}, {"household_uuid": "SYNTHETIC_B"}]
        with patch.object(repository, "list_assessment_summaries", return_value=items), \
                patch.object(service.dataset_batches, "find_household_identities", return_value=identities) as query:
            result = service.list_active_assessment_summaries(
                page=2, include_household_identity=True, connection_factory=self.factory,
            )
            self.assertEqual([item["household_uuid"] for item in result["items"]],
                             ["SYNTHETIC_B", "SYNTHETIC_A"])
            query.assert_called_once_with(self.connection, 9, ["SYNTHETIC_B", "SYNTHETIC_A"])
            self.factory.assert_called_once()
            query.return_value = []
            with self.assertRaisesRegex(service.AssessmentQueryError, "household_identity_missing"):
                service.list_active_assessment_summaries(
                    include_household_identity=True, connection_factory=self.factory,
                )

    def test_missing_incomplete_or_wrong_batch_designation_is_rejected(self):
        for run, code in ((None, "approved_assessment_run_missing"),
                          ({**self.run, "completed_at": None}, "completed_assessment_run_invalid"),
                          ({**self.run, "batch_id": 8}, "completed_assessment_run_invalid")):
            with self.subTest(code=code):
                self.runs.return_value = run
                with self.assertRaisesRegex(service.AssessmentQueryError, code):
                    service.get_active_assessment_context(connection_factory=self.factory)

    def test_invalid_inputs_are_rejected_before_connecting(self):
        for page, size in ((0, 25), (True, 25), (1, 0), (1, 101), (1, 2.5)):
            with self.subTest(page=page, size=size), self.assertRaises(service.AssessmentQueryError):
                service.list_active_assessment_summaries(
                    page=page, page_size=size, connection_factory=self.factory,
                )
        for uuid in (None, "", "  ", 12):
            with self.subTest(uuid=uuid), self.assertRaises(service.AssessmentQueryError):
                service.get_active_household_assessment(uuid, connection_factory=self.factory)
        self.factory.assert_not_called()

    def test_non_idle_connection_and_database_errors_do_not_leak_values(self):
        self.connection.info.transaction_status = TransactionStatus.INTRANS
        with self.assertRaisesRegex(service.AssessmentQueryError, "assessment_connection_not_idle"):
            service.get_active_assessment_context(connection_factory=self.factory)
        self.connection.execute.assert_not_called()
        self.factory.side_effect = psycopg.OperationalError("SYNTHETIC_SECRET")
        with self.assertRaises(service.AssessmentQueryError) as result:
            service.get_active_assessment_context(connection_factory=self.factory)
        self.assertEqual(str(result.exception), "assessment_query_unavailable")

    def test_list_sql_projects_summary_only_and_binds_pagination(self):
        cursor = self.connection.cursor.return_value.__enter__.return_value
        repository.list_assessment_summaries(self.connection, 17, limit=7, offset=14)
        statement, parameters = cursor.execute.call_args.args
        self.assertIn("ORDER BY h.vulnerability_rank ASC, h.household_uuid ASC", statement)
        self.assertIn("r.status = 'completed'", statement)
        self.assertIn("LIMIT %s OFFSET %s", statement)
        self.assertNotIn("SELECT h.findings", statement)
        self.assertNotIn("supporting_evidence", statement)
        self.assertEqual(parameters, (17, 7, 14))

    def test_filter_sql_binds_values_and_escapes_literal_search_wildcards(self):
        cursor = self.connection.cursor.return_value.__enter__.return_value
        filters = AssessmentFilters(
            search="Synthetic %_! ' OR 1=1 --", barangay="Synthetic East",
            programs=("4Ps", "SLP"), completeness="unknown",
        )
        repository.list_assessment_summaries(self.connection, 17, limit=25, offset=25, filters=filters)
        statement, parameters = cursor.execute.call_args.args
        self.assertNotIn(filters.search, statement)
        self.assertNotIn(filters.barangay, statement)
        self.assertIn("m.batch_id = h.batch_id AND m.uuid = h.household_uuid", statement)
        self.assertIn("HAVING count(*) = 1", statement)
        self.assertIn("ILIKE %s ESCAPE '!'", statement)
        self.assertIn(" OR ", statement)
        self.assertLess(statement.index("unknown_indicator_count"), statement.index("LIMIT %s"))
        self.assertEqual(parameters, (
            17, "%Synthetic !%!_!! ' OR 1=1 --%", "Synthetic East",
            ["programs", "4Ps", "screening_status"], ["programs", "SLP", "screening_status"], 25, 25,
        ))
        repository.list_assessment_summaries(
            self.connection, 17, limit=25, offset=0,
            filters=AssessmentFilters(search="hh-19304"),
        )
        statement, parameters = cursor.execute.call_args.args
        self.assertIn("source.source_row_number = %s", statement)
        self.assertNotIn("household_members", statement)
        self.assertEqual(parameters, (17, 19304, 25, 0))


@unittest.skipUnless(TEST_URL, "Set the local isolated test database URL.")
class AssessmentQueryPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        datasets, references = development_sources()
        cls.findings = build_unified_assessments(datasets, references, survey_year=2022)

    def _connect(self):
        """Reject non-test targets and verify actual loopback identity before any DDL."""
        settings = conninfo_to_dict(TEST_URL)
        self.assertEqual(settings.get("dbname"), "tanglaw_buhay_test")
        self.assertIn(settings.get("host"), ("localhost", "127.0.0.1", "::1"))
        self.assertNotIn("service", settings)
        self.assertNotIn("options", settings)
        self.assertIn(settings.get("hostaddr", "127.0.0.1"), ("127.0.0.1", "::1"))
        self.assertFalse(any(os.getenv(name) for name in ("PGSERVICE", "PGSERVICEFILE", "PGOPTIONS")))
        address = "::1" if settings["host"] == "::1" else "127.0.0.1"
        connection = psycopg.connect(TEST_URL, hostaddr=address, connect_timeout=5, autocommit=True)
        try:
            identity = connection.execute("SELECT current_database(), host(inet_server_addr())").fetchone()
            self.assertEqual(identity, ("tanglaw_buhay_test", address))
            return connection
        except Exception:
            connection.close()
            raise

    def setUp(self):
        read_cache.clear()
        self.schema = f"assessment_query_check_{uuid4().hex}"
        self.connection = self._connect()
        self.addCleanup(self.connection.close)
        with self.connection.transaction():
            self.connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(self.schema)))
            self.connection.execute(sql.SQL("SET LOCAL search_path TO {}").format(sql.Identifier(self.schema)))
            for name in ("0001_database_foundation.sql", "0003_assessment_persistence.sql",
                         "0004_assessment_run_selection.sql"):
                self.connection.execute((ROOT / "migrations" / name).read_text(encoding="utf-8"))
        self.addCleanup(self._drop_schema)
        self.connection.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(self.schema)))
        self.batch_id = self._batch(active=True)

    def _drop_schema(self):
        self.connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(self.schema)))

    def _factory(self):
        connection = self._connect()
        connection.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(self.schema)))
        return connection

    def _batch(self, *, active=False, uuids=None):
        uuids = list(self.findings) if uuids is None else uuids
        with self.connection.transaction():
            batch_id = self.connection.execute(
                """INSERT INTO dataset_batches
                   (survey_year, municipality, source_columns, source_provenance, validation_status, is_active)
                   VALUES (2022, 'Pozorrubio', %s, %s, 'validated', %s) RETURNING id""",
                (Jsonb({"household": ["uuid"]}), Jsonb({"kind": "generated synthetic query tests"}), active),
            ).fetchone()[0]
            with self.connection.cursor() as cursor:
                cursor.executemany(
                    "INSERT INTO households (batch_id, uuid, source_row_number, responses) VALUES (%s, %s, %s, %s)",
                    [(batch_id, uuid, number, Jsonb({"uuid": uuid})) for number, uuid in enumerate(uuids, 1)],
                )
        return batch_id

    def _run(self, batch_id=None, *, complete=True, findings=None, designate=True):
        batch_id = self.batch_id if batch_id is None else batch_id
        findings = self.findings if findings is None else findings
        with self.connection.transaction():
            run_id = repository.create_run(self.connection, batch_id, calculation_version="synthetic-v1",
                                           calculation_provenance={"kind": "generated synthetic query tests"})
            repository.store_household_assessments(self.connection, run_id, findings)
            if complete:
                repository.complete_run(self.connection, run_id)
                if designate:
                    repository.designate_run(self.connection, batch_id, run_id)
        return run_id

    def _detail_fixture(self):
        """Store explicit synthetic findings and evidence in this isolated test schema."""
        source = detail_source()
        batch_id = self._batch(uuids=[HOUSEHOLD_UUID])
        self.connection.execute("UPDATE dataset_batches SET is_active=false WHERE id=%s", (self.batch_id,))
        self.connection.execute("UPDATE dataset_batches SET is_active=true WHERE id=%s", (batch_id,))
        self.batch_id = batch_id
        household = {**source["household"], "barangay": "Synthetic Barangay", "unrelated": "EXCLUDED_SOURCE_VALUE"}
        self.connection.execute("UPDATE households SET responses=%s WHERE batch_id=%s", (Jsonb(household), batch_id))
        for member in source["members"]:
            responses = {**member["responses"], "a02_relation_to_hh_head": "1" if member["line_number"] == 1 else "3",
                         "unrelated": "EXCLUDED_SOURCE_VALUE"}
            self.connection.execute(
                "INSERT INTO household_members (batch_id, uuid, line_number, source_row_number, responses) "
                "VALUES (%s, %s, %s, %s, %s)",
                (batch_id, HOUSEHOLD_UUID, member["line_number"], member["line_number"], Jsonb(responses)),
            )
            if member["line_number"] != 2:
                self.connection.execute(
                    "INSERT INTO member_health (batch_id, uuid, section_l_line_number, source_row_number, responses) "
                    "VALUES (%s, %s, %s, %s, %s)",
                    (batch_id, HOUSEHOLD_UUID, member["line_number"], member["line_number"], Jsonb(member["health_responses"])),
                )
        for name, table in service.dataset_batches.REFERENCE_TABLES.items():
            with self.connection.cursor() as cursor:
                cursor.executemany(
                    sql.SQL("INSERT INTO {} (batch_id, source_row_number, responses) VALUES (%s, %s, %s)")
                    .format(sql.Identifier(table)),
                    [(batch_id, number, Jsonb(row)) for number, row in enumerate(source["references"][name], 1)],
                )
        source["findings"]["mpi"]["vulnerability_rank"] = 1
        run_id = self._run(findings={HOUSEHOLD_UUID: source["findings"]})
        return source, run_id

    def test_detail_postgres_projection_member_join_batch_and_run_isolation(self):
        source, selected = self._detail_fixture()
        alternate = deepcopy(source["findings"])
        alternate["mpi"]["assumptions_used"] += ("OTHER_UNSELECTED_RUN",)
        self._run(findings={HOUSEHOLD_UUID: alternate}, designate=False)
        other_batch = self._batch(uuids=[HOUSEHOLD_UUID])
        self.connection.execute("UPDATE households SET responses=%s WHERE batch_id=%s",
                                (Jsonb({"q01_main_water": "OTHER_BATCH_VALUE"}), other_batch))
        self.connection.execute(
            "INSERT INTO household_members (batch_id, uuid, line_number, source_row_number, responses) "
            "VALUES (%s, %s, 1, 1, %s)",
            (other_batch, HOUSEHOLD_UUID, Jsonb({"a01_first_name": "OTHER_BATCH_MEMBER"})),
        )
        result = service.get_active_assessment_detail(HOUSEHOLD_UUID, connection_factory=self._factory)
        self.assertEqual(result["context"]["run_id"], selected)
        self.assertEqual(result["findings"], source["findings"])
        self.assertEqual(result["household"]["q01_main_water"], "3")
        self.assertEqual(result["identity"]["first_name"], "Synthetic")
        self.assertEqual([row["line_number"] for row in result["members"]], [1, 2, 3])
        self.assertFalse(result["members"][1]["health_present"])
        self.assertIsNone(result["members"][1]["health_responses"]["l34_has_been_sick"])
        self.assertNotIn("EXCLUDED_SOURCE_VALUE", str(result))
        self.assertNotIn("OTHER_BATCH_VALUE", str(result))
        self.assertNotIn("OTHER_BATCH_MEMBER", str(result))
        self.assertTrue(result["references"]["codebook"])
        self.assertTrue(result["references"]["mpi_mapping"])
        self.assertTrue(result["references"]["definitions"])

    def test_detail_queries_preserve_sources_findings_and_read_only_boundary(self):
        self._detail_fixture()
        tables = ("households", "household_members", "member_health", "household_assessments")
        def fingerprints():
            return [self.connection.execute(sql.SQL(
                "SELECT md5(string_agg(to_jsonb(t)::text, '' ORDER BY to_jsonb(t)::text)) FROM {} t",
            ).format(sql.Identifier(table))).fetchone() for table in tables]
        before = fingerprints()
        service.get_active_assessment_detail(HOUSEHOLD_UUID, connection_factory=self._factory)
        self.assertEqual(before, fingerprints())
        with service._read_context(self._factory) as (connection, _):
            self.assertEqual(connection.execute("SHOW transaction_read_only").fetchone(), ("on",))

    def test_screening_source_projection_decodes_joined_fields_without_widening_scope(self):
        self._detail_fixture()
        source = screening_source()
        self.connection.execute(
            "UPDATE households SET responses = responses || %s WHERE batch_id = %s",
            (Jsonb(source["household"]), self.batch_id),
        )
        for member in source["members"]:
            line = member["line_number"]
            self.connection.execute(
                "UPDATE household_members SET responses = responses || %s "
                "WHERE batch_id = %s AND uuid = %s AND line_number = %s",
                (Jsonb(member["responses"]), self.batch_id, HOUSEHOLD_UUID, line),
            )
            self.connection.execute(
                "UPDATE member_health SET responses = responses || %s "
                "WHERE batch_id = %s AND uuid = %s AND section_l_line_number = %s",
                (Jsonb(member["health_responses"]), self.batch_id, HOUSEHOLD_UUID, line),
            )
        result = service.get_active_assessment_detail(HOUSEHOLD_UUID, connection_factory=self._factory)
        self.assertEqual(result["household"]["l15_with_pwd_member"], "1")
        self.assertEqual(result["members"][2]["responses"]["e18_class_of_worker"], "3")
        self.assertEqual(result["members"][2]["health_responses"]["l16_pwd"], "2")
        self.assertIsNone(result["members"][1]["health_responses"]["l07_currently_pregnant"])
        fields = {row["variable_name"] for row in result["references"]["codebook"]}
        self.assertTrue({"l07_currently_pregnant", "l16_pwd", "e18_class_of_worker"} <= fields)
        self.assertNotIn("EXCLUDED_SOURCE_VALUE", str(result))

    def test_resolution_uses_designation_among_multiple_completed_and_pending_runs(self):
        self._run(complete=False)
        with self.assertRaisesRegex(service.AssessmentQueryError, "approved_assessment_run_missing"):
            service.get_active_assessment_context(connection_factory=self._factory)
        completed = self._run()
        result = service.get_active_assessment_context(connection_factory=self._factory)
        self.assertEqual((result["batch_id"], result["run_id"], result["survey_year"]),
                         (self.batch_id, completed, 2022))
        other = self._run(designate=False)
        result = service.get_active_assessment_context(connection_factory=self._factory)
        self.assertEqual(result["run_id"], completed)
        repository.designate_run(self.connection, self.batch_id, other)
        result = service.get_active_assessment_context(connection_factory=self._factory)
        self.assertEqual(result["run_id"], other)

    def test_missing_or_unvalidated_active_batch_does_not_fall_back(self):
        self._run()
        self.connection.execute("UPDATE dataset_batches SET is_active=false WHERE id=%s", (self.batch_id,))
        with self.assertRaisesRegex(service.AssessmentQueryError, "active_batch_missing"):
            service.get_active_assessment_context(connection_factory=self._factory)
        # A pending inactive source is not a valid alternative.
        other = self._batch()
        self.connection.execute("UPDATE dataset_batches SET validation_status='pending' WHERE id=%s", (other,))
        with self.assertRaisesRegex(service.AssessmentQueryError, "active_batch_missing"):
            service.get_active_assessment_context(connection_factory=self._factory)

    def test_pagination_preserves_ties_and_original_summary_values(self):
        run_id = self._run()
        expected = sorted(self.findings.values(), key=lambda row: (
            row["mpi"]["vulnerability_rank"], row["household_uuid"],
        ))
        actual = []
        for page in range(1, 6):
            result = service.list_active_assessment_summaries(
                page=page, page_size=7, connection_factory=self._factory,
            )
            self.assertEqual(result["context"]["run_id"], run_id)
            self.assertEqual(result["total"], 32)
            self.assertLessEqual(len(result["items"]), 7)
            actual.extend(result["items"])
        self.assertEqual([row["household_uuid"] for row in actual], [row["household_uuid"] for row in expected])
        for row, original in zip(actual, expected):
            for field in ("vulnerability_rank", "vulnerability_score", "coverage_pct",
                          "data_quality_status", "unknown_indicator_count"):
                self.assertEqual(row[field], original["mpi"][field])
            expected_statuses = {
                code: finding["screening_status"] for code, finding in original["programs"].items()
            }
            # This fixture intentionally has no source roster.
            expected_statuses['SPISC'] = 'Needs Data Review'
            self.assertEqual(row["program_statuses"], expected_statuses)
            self.assertNotIn("findings", row)
        self.assertLess(len({row["vulnerability_rank"] for row in actual}), len(actual))
        empty = service.list_active_assessment_summaries(page=6, page_size=7, connection_factory=self._factory)
        self.assertEqual(empty["items"], [])
        self.assertEqual(empty["total"], 32)

    def test_complete_detail_matches_every_synthetic_finding(self):
        self._run()
        for uuid, original in self.findings.items():
            result = service.get_active_household_assessment(uuid, connection_factory=self._factory)
            self.assertEqual(result["findings"], original)
            self.assertIsInstance(result["findings"]["mpi"]["assumptions_used"], tuple)
        with self.assertRaisesRegex(service.AssessmentQueryError, "household_assessment_missing"):
            service.get_active_household_assessment("SYNTHETIC_MISSING", connection_factory=self._factory)

    def test_identities_use_relationship_code_and_batch_scoped_joins(self):
        self._run()
        uuid = min(self.findings, key=lambda key: (
            self.findings[key]["mpi"]["vulnerability_rank"], key,
        ))
        other_batch = self._batch(uuids=[uuid])
        for batch, label in ((self.batch_id, "Active"), (other_batch, "Other")):
            self.connection.execute(
                "UPDATE households SET responses=%s WHERE batch_id=%s AND uuid=%s",
                (Jsonb({"uuid": uuid, "barangay": f"Synthetic {label}"}), batch, uuid),
            )
            with self.connection.cursor() as cursor:
                cursor.executemany(
                    """INSERT INTO household_members
                       (batch_id, uuid, line_number, source_row_number, responses)
                       VALUES (%s, %s, %s, %s, %s)""",
                    [(batch, uuid, line, line, Jsonb({
                        "a02_relation_to_hh_head": code,
                        "a01_first_name": f"Synthetic {label} {line}",
                        "a01_middle_name": "", "a01_last_name": "Head",
                    })) for line, code in ((1, "2"), (2, "1"))],
                )
        result = service.list_active_assessment_summaries(
            include_household_identity=True, connection_factory=self._factory,
        )
        selected = next(item for item in result["items"] if item["household_uuid"] == uuid)
        self.assertEqual(selected["identity"]["first_name"], "Synthetic Active 2")
        self.assertEqual(selected["identity"]["barangay"], "Synthetic Active")
        self.assertEqual(selected["identity"]["head_count"], 1)
        source_row = self.connection.execute(
            "SELECT source_row_number FROM households WHERE batch_id=%s AND uuid=%s",
            (self.batch_id, uuid),
        ).fetchone()[0]
        self.assertEqual(selected["identity"]["source_row_number"], source_row)
        other_identity = service.dataset_batches.find_household_identities(
            self.connection, other_batch, [uuid],
        )[0]
        self.assertEqual(other_identity["source_row_number"], 1)
        self.assertEqual(len(result["items"]), 25)
        self.assertTrue(any(item["identity"]["head_count"] == 0 for item in result["items"]))
        self.connection.execute(
            """INSERT INTO household_members
               (batch_id, uuid, line_number, source_row_number, responses)
               VALUES (%s, %s, 3, 3, %s)""",
            (self.batch_id, uuid, Jsonb({"a02_relation_to_hh_head": "1", "a01_first_name": "Ambiguous"})),
        )
        identity = service.dataset_batches.find_household_identities(
            self.connection, self.batch_id, [uuid],
        )[0]
        self.assertEqual(identity["head_count"], 2)
        self.assertIsNone(identity["first_name"])
        with service._read_context(self._factory) as (connection, _):
            self.assertEqual(connection.execute("SHOW transaction_read_only").fetchone(), ("on",))
            service.dataset_batches.find_household_identities(connection, self.batch_id, [uuid])

    def test_batch_isolation_uses_active_run_for_shared_and_other_households(self):
        self._run()
        shared_uuid = next(iter(self.findings))
        other_findings = deepcopy(self.findings)
        other_findings[shared_uuid]["mpi"]["assumptions_used"] += ("SYNTHETIC_OTHER_BATCH",)
        other = self._batch()
        other_run = self._run(other, findings=other_findings)
        result = service.get_active_household_assessment(shared_uuid, connection_factory=self._factory)
        self.assertEqual(result["findings"], self.findings[shared_uuid])
        self.connection.execute("UPDATE dataset_batches SET is_active=false WHERE id=%s", (self.batch_id,))
        self.connection.execute("UPDATE dataset_batches SET is_active=true WHERE id=%s", (other,))
        result = service.get_active_household_assessment(shared_uuid, connection_factory=self._factory)
        self.assertEqual(result["context"]["run_id"], other_run)
        self.assertEqual(result["findings"], other_findings[shared_uuid])

    def test_service_calls_leave_rows_unchanged_and_reject_writes(self):
        self._run()
        before = self.connection.execute(
            "SELECT md5(string_agg(to_jsonb(h)::text, '' ORDER BY run_id, household_uuid)) FROM household_assessments h",
        ).fetchone()
        service.list_active_assessment_summaries(connection_factory=self._factory)
        uuid = next(iter(self.findings))
        service.get_active_household_assessment(uuid, connection_factory=self._factory)
        with service._read_context(self._factory) as (connection, _):
            self.assertEqual(connection.execute("SHOW transaction_read_only").fetchone(), ("on",))
            self.assertEqual(connection.execute("SHOW transaction_isolation").fetchone(), ("repeatable read",))
            with self.assertRaises(psycopg.errors.ReadOnlySqlTransaction):
                with connection.transaction():
                    connection.execute("DELETE FROM household_assessments")
        after = self.connection.execute(
            "SELECT md5(string_agg(to_jsonb(h)::text, '' ORDER BY run_id, household_uuid)) FROM household_assessments h",
        ).fetchone()
        self.assertEqual(before, after)

    def _filter_fixture(self, *, scores=None):
        """Prepare explicit synthetic search identities and stored filter findings."""
        findings = deepcopy(self.findings)
        for number, (uuid, finding) in enumerate(findings.items(), 1):
            if scores is not None:
                finding["mpi"]["vulnerability_score"] = scores[number - 1]
            unknown = number % 4 == 0
            finding["mpi"]["unknown_indicator_count"] = int(unknown)
            finding["mpi"]["data_quality_status"] = "Needs Data Review" if unknown else "Complete Data"
            for code, divisor in (("4Ps", 2), ("SLP", 3), ("SPISC", 5), ("TUPAD", 7)):
                finding["programs"][code]["screening_status"] = (
                    "For Review" if number % divisor == 0 else "No Historical CBMS Trigger"
                )
            if number in (1, 5):
                finding["programs"]["SLP"]["screening_status"] = "Needs Data Review"
            barangay = "Synthetic East" if number % 2 else "Synthetic West"
            if number > 30:
                barangay = "" if number == 31 else "NA"
            self.connection.execute(
                "UPDATE households SET responses=%s WHERE batch_id=%s AND uuid=%s",
                (Jsonb({"uuid": uuid, "barangay": barangay}), self.batch_id, uuid),
            )
            self.connection.execute(
                """INSERT INTO household_members
                   (batch_id, uuid, line_number, source_row_number, responses)
                   VALUES (%s, %s, 1, %s, %s)""",
                (self.batch_id, uuid, number, Jsonb({
                    "a02_relation_to_hh_head": "1",
                    "a07_age": "60" if number % 5 == 0 else "30",
                    "a01_first_name": "Synthetic", "a01_middle_name": "NA",
                    "a01_last_name": "Common" if number <= 30 else "Other",
                })),
            )
        self._run(findings=findings)
        return findings

    def test_household_directory_search_barangay_and_source_order(self):
        findings = self._filter_fixture()
        first_uuid = next(iter(findings))
        all_records = record_service.get_record_page(
            1, record_service.RecordFilters(), connection_factory=self._factory,
        )
        self.assertEqual(all_records["total"], 32)
        self.assertEqual(all_records["page_count"], 2)
        self.assertEqual((all_records["range_start"], all_records["range_end"]), (1, 25))
        self.assertEqual(all_records["items"][0]["household_uuid"], first_uuid)
        self.assertEqual(all_records["items"][0]["reference_id"], "HH-00001")
        self.assertEqual(all_records["items"][0]["head_name"], "Synthetic Common")
        self.assertEqual(all_records["items"][0]["member_count"], 1)
        self.assertEqual(all_records["barangays"], ["Synthetic East", "Synthetic West"])
        second = record_service.get_record_page(
            2, record_service.RecordFilters(), connection_factory=self._factory,
        )
        self.assertEqual(second["items"][0]["reference_id"], "HH-00026")
        self.assertEqual((second["range_start"], second["range_end"]), (26, 32))
        east = record_service.get_record_page(
            1, record_service.RecordFilters(barangay="Synthetic East"),
            connection_factory=self._factory,
        )
        self.assertEqual(east["total"], 15)
        self.assertTrue(all(row["barangay"] == "Synthetic East" for row in east["items"]))
        by_id = record_service.get_record_page(
            1, record_service.RecordFilters(search="HH-00001"),
            connection_factory=self._factory,
        )
        self.assertEqual([row["household_uuid"] for row in by_id["items"]], [first_uuid])
        by_head = record_service.get_record_page(
            1, record_service.RecordFilters(search="Synthetic Common", barangay="Synthetic West"),
            connection_factory=self._factory,
        )
        self.assertEqual(by_head["total"], 15)
        self.assertEqual(by_head["items"][0]["reference_id"], "HH-00002")
        with self.assertRaises(record_service.InvalidRecordFilters):
            record_service.get_record_page(
                1, record_service.RecordFilters(barangay="Unknown"),
                connection_factory=self._factory,
            )

    def test_household_detail_reads_focused_selected_batch_answers_only(self):
        source, selected = self._detail_fixture()
        other_batch = self._batch(uuids=[HOUSEHOLD_UUID])
        self.connection.execute(
            "UPDATE households SET responses=%s WHERE batch_id=%s",
            (Jsonb({"q01_main_water": "OTHER_BATCH_VALUE"}), other_batch),
        )
        self.connection.execute(
            "INSERT INTO household_members (batch_id, uuid, line_number, source_row_number, responses) "
            "VALUES (%s, %s, 1, 1, %s)",
            (other_batch, HOUSEHOLD_UUID, Jsonb({"a01_first_name": "OTHER_BATCH_MEMBER"})),
        )
        record = record_service.get_record_detail(HOUSEHOLD_UUID, connection_factory=self._factory)
        self.assertEqual(record["context"]["run_id"], selected)
        self.assertEqual(record["reference_id"], "HH-00001")
        self.assertEqual(record["head_name"], "Synthetic Member 1")
        self.assertEqual(record["barangay"], "Synthetic Barangay")
        self.assertEqual(record["composition"]["total"], 3)
        self.assertEqual([member["line_number"] for member in record["members"]], [1, 2, 3])
        self.assertEqual(record["groups"][1]["cells"][0]["label"], "Main water source")
        self.assertEqual(record["groups"][0]["cells"][5]["value"], "50 m²")
        self.assertNotIn("OTHER_BATCH_VALUE", str(record))
        self.assertNotIn("OTHER_BATCH_MEMBER", str(record))
        self.assertNotIn("EXCLUDED_SOURCE_VALUE", str(record))
        self.assertEqual(source["household"]["q01_main_water"], "3")

    def _assert_filtered_rows(self, filters, expected_numbers, findings, *, page_size=25):
        """Check full matching sets/counts across pages and original stored values."""
        expected = [value for number, value in enumerate(findings.values(), 1) if number in expected_numbers]
        expected.sort(key=lambda row: (row["mpi"]["vulnerability_rank"], row["household_uuid"]))
        actual = []
        for page in range(1, max(1, (len(expected) + page_size - 1) // page_size) + 1):
            result = service.list_active_assessment_summaries(
                page=page, page_size=page_size, filters=filters, include_household_identity=True,
                include_barangays=True, connection_factory=self._factory,
            )
            self.assertEqual(result["total"], len(expected))
            self.assertEqual(result["barangays"], ["Synthetic East", "Synthetic West"])
            self.assertLessEqual(len(result["items"]), page_size)
            actual.extend(result["items"])
        self.assertEqual([row["household_uuid"] for row in actual], [row["household_uuid"] for row in expected])
        for row, original in zip(actual, expected):
            self.assertEqual(row["vulnerability_rank"], original["mpi"]["vulnerability_rank"])
            self.assertEqual(row["vulnerability_score"], original["mpi"]["vulnerability_score"])
            self.assertEqual(row["program_statuses"], {
                code: value["screening_status"] for code, value in original["programs"].items()
            })
        return actual

    def test_dashboard_aggregates_use_designated_run_and_stored_statuses(self):
        findings = self._filter_fixture()
        result = service.get_active_dashboard_summary(connection_factory=self._factory)
        self.assertEqual(result["context"]["household_count"], len(findings))
        self.assertEqual(result["priority"]["all"], len(findings))
        self.assertEqual(result["findings"]["assessed_households"], len(findings))
        self.assertEqual(result["findings"]["needs_attention"], sum(
            item["mpi"]["unknown_indicator_count"] > 0 for item in findings.values()
        ))
        for code, field in (("4Ps", "four_ps"), ("SLP", "slp"),
                            ("SPISC", "spisc"), ("TUPAD", "tupad")):
            self.assertEqual(result["findings"][field], sum(
                item["programs"][code]["screening_status"] == "For Review"
                for item in findings.values()
            ))
        self.assertNotIn("items", result)
        self.assertNotIn("household_uuid", str(result))

    def test_dashboard_counts_match_current_age_program_filters(self):
        self._filter_fixture()
        dashboard = service.get_active_dashboard_summary(connection_factory=self._factory)
        for program, field in (("4Ps", "four_ps"), ("SLP", "slp"),
                               ("SPISC", "spisc"), ("TUPAD", "tupad")):
            filtered = service.list_active_assessment_summaries(
                filters=AssessmentFilters(programs=(program,)), connection_factory=self._factory,
            )
            self.assertEqual(dashboard["findings"][field], filtered["total"], program)
        for priority in ("primary", "secondary"):
            filtered = service.list_active_assessment_summaries(
                filters=AssessmentFilters(priority=priority), connection_factory=self._factory,
            )
            self.assertEqual(dashboard["priority"][priority], filtered["total"], priority)

    def test_dashboard_barangay_statistics_reconcile_with_municipal_totals(self):
        findings = self._filter_fixture()
        municipal = service.get_active_dashboard_summary(connection_factory=self._factory)
        self.assertEqual(municipal["barangay"], "")
        self.assertEqual(municipal["barangays"], ["Synthetic East", "Synthetic West"])
        by_barangay = {
            name: service.get_active_dashboard_summary(
                barangay=name, connection_factory=self._factory)
            for name in municipal["barangays"]
        }
        # Households 31 and 32 record a blank or "NA" barangay and belong to no option.
        unrecorded = 2
        counts = {name: result["priority"]["all"] for name, result in by_barangay.items()}
        self.assertEqual(counts, {"Synthetic East": 15, "Synthetic West": 15})
        self.assertEqual(sum(counts.values()) + unrecorded, municipal["priority"]["all"])
        for name, result in by_barangay.items():
            self.assertEqual(result["barangay"], name)
            self.assertEqual(result["findings"]["assessed_households"], counts[name])
            for program, field in (("4Ps", "four_ps"), ("SLP", "slp"),
                                   ("SPISC", "spisc"), ("TUPAD", "tupad")):
                filtered = service.list_active_assessment_summaries(
                    filters=AssessmentFilters(barangay=name, programs=(program,)),
                    connection_factory=self._factory,
                )
                self.assertEqual(result["findings"][field], filtered["total"], (name, program))
            for priority in ("primary", "secondary"):
                filtered = service.list_active_assessment_summaries(
                    filters=AssessmentFilters(barangay=name, priority=priority),
                    connection_factory=self._factory,
                )
                self.assertEqual(result["priority"][priority], filtered["total"], (name, priority))
        # Households with a missing or NA barangay are the whole difference in every aggregate.
        profile_keys = ("with_children", "with_older_adults", "without_electricity", "shared_toilet")
        older = {name: sum(
            number % 5 == 0 for number in range(1, 31) if (number % 2 == 1) == (name == "Synthetic East")
        ) for name in counts}
        for name, result in by_barangay.items():
            self.assertEqual(result["household_profile"]["with_older_adults"], older[name], name)
        remainder_older = sum(number % 5 == 0 for number in (31, 32))
        self.assertEqual(
            sum(older.values()) + remainder_older,
            municipal["household_profile"]["with_older_adults"],
        )
        for key in profile_keys:
            self.assertLessEqual(
                sum(result["household_profile"][key] for result in by_barangay.values()),
                municipal["household_profile"][key], key,
            )
        self.assertEqual(len(findings), 32)

    def test_indicator_counts_match_stored_flags_and_barangay_scope(self):
        findings = self._filter_fixture()
        before = self.connection.execute(
            "SELECT md5(string_agg(findings::text, '' ORDER BY household_uuid)) FROM household_assessments"
        ).fetchone()
        municipal = service.get_active_dashboard_summary(connection_factory=self._factory)
        rows = {indicator["name"]: indicator for area in municipal["indicator_patterns"]
                for indicator in area["indicators"]}
        self.assertEqual(len(rows), 16)
        for name in INDICATOR_WEIGHTS:
            expected = [item["mpi"][name] for item in findings.values()]
            shown = {o["key"]: o["count"] for o in rows[DISPLAY_NAMES.get(name, name)]["outcomes"]}
            self.assertEqual(shown, {"deprived": expected.count(1),
                                     "not_deprived": expected.count(0),
                                     "unknown": expected.count(None)}, name)
        east = service.get_active_dashboard_summary(
            barangay="Synthetic East", connection_factory=self._factory)
        east_uuids = {uuid for number, uuid in enumerate(findings, 1) if number <= 30 and number % 2 == 1}
        for area in east["indicator_patterns"]:
            for indicator in area["indicators"]:
                self.assertEqual(sum(o["count"] for o in indicator["outcomes"]),
                                 east["priority"]["all"])
        self.assertEqual(east["priority"]["all"], len(east_uuids))
        after = self.connection.execute(
            "SELECT md5(string_agg(findings::text, '' ORDER BY household_uuid)) FROM household_assessments"
        ).fetchone()
        self.assertEqual(before, after)

    def test_unexpected_or_missing_stored_indicator_values_are_not_counted(self):
        self._filter_fixture()
        for label, change in (
            ("text flag", "jsonb_set(findings, '{mpi,Water}', '\"1\"')"),
            ("out of range", "jsonb_set(findings, '{mpi,Water}', '2')"),
            ("boolean", "jsonb_set(findings, '{mpi,Water}', 'true')"),
            ("missing key", "findings #- '{mpi,Water}'"),
        ):
            with self.subTest(label):
                read_cache.clear()
                self.connection.execute(
                    "UPDATE household_assessments SET findings = " + change
                    + " WHERE household_uuid = (SELECT min(household_uuid) FROM household_assessments)"
                )
                with self.assertRaisesRegex(service.AssessmentQueryError, "assessment_count_mismatch"):
                    service.get_active_dashboard_summary(connection_factory=self._factory)

    def test_dashboard_rejects_unknown_barangay_and_excludes_unrecorded_options(self):
        self._filter_fixture()
        for value in ("Synthetic Nowhere", "NA"):
            with self.subTest(value=value), self.assertRaises(InvalidAssessmentFilters):
                service.get_active_dashboard_summary(
                    barangay=value, connection_factory=self._factory)

    def test_priority_boundaries_combinations_pagination_and_unchanged_findings(self):
        # Number 1 has only Needs Data Review, even with its high stored score.
        findings = self._filter_fixture(
            scores=[40] + [19.999] * 7 + [20] * 8 + [25] * 8 + [25.001] * 8,
        )
        run_id = repository.find_designated_run(self.connection, self.batch_id)["run_id"]
        before = self.connection.execute(
            "SELECT md5(string_agg(to_jsonb(h)::text, '' ORDER BY household_uuid)) "
            "FROM household_assessments h",
        ).fetchone()
        primary = {25, 26, 27, 28, 30, 32}
        secondary = {9, 10, 12, 14, 15, 16, 18, 20, 21, 22, 24}
        groups = {
            "all": set(range(1, 33)), "primary": primary, "secondary": secondary,
            "both": primary | secondary, "other": set(range(1, 33)) - primary - secondary,
        }
        labels = {
            number: PRIMARY_PRIORITY if number in primary else (
                SECONDARY_PRIORITY if number in secondary else NO_SUBSIDY_PRIORITY
            ) for number in range(1, 33)
        }
        by_uuid = {finding["household_uuid"]: labels[number]
                   for number, finding in enumerate(findings.values(), 1)}
        for priority, numbers in groups.items():
            with self.subTest(priority=priority):
                actual = self._assert_filtered_rows(
                    AssessmentFilters(priority=priority), numbers, findings, page_size=7,
                )
                for row in actual:
                    self.assertEqual(row["subsidy_priority"], by_uuid[row["household_uuid"]])
                for mode in ("any", "exact"):
                    for completeness in ("all", "unknown", "determined"):
                        # Combine name, barangay, program matching and MPI completeness.
                        matches = {2, 4, 6, 8, 10, 12, 14, 16, 18, 20, 22, 24, 26, 28, 30}
                        if mode == "exact":
                            matches = {2, 4, 8, 16, 22, 26}
                        if completeness == "unknown":
                            matches &= {4, 8, 12, 16, 20, 24, 28, 32}
                        elif completeness == "determined":
                            matches -= {4, 8, 12, 16, 20, 24, 28, 32}
                        self._assert_filtered_rows(AssessmentFilters(
                            priority=priority, search="Synthetic Common", barangay="Synthetic West",
                            programs=("4Ps",), subsidy_mode=mode, completeness=completeness,
                        ), numbers & matches, findings, page_size=7)
                empty = service.list_active_assessment_summaries(
                    page=20, page_size=7, filters=AssessmentFilters(priority=priority),
                    connection_factory=self._factory,
                )
                self.assertEqual(empty["items"], [])
                self.assertEqual(empty["total"], len(numbers))
                counted = repository.count_priority_groups(
                    self.connection, run_id,
                    filters=AssessmentFilters(priority=priority),
                )
                self.assertEqual(counted, {key: len(values) for key, values in groups.items()})
        counted = repository.count_priority_groups(self.connection, run_id, filters=AssessmentFilters(
            priority="primary", search="Synthetic Common", barangay="Synthetic West",
            programs=("4Ps",), subsidy_mode="exact", completeness="unknown",
        ))
        self.assertEqual(counted, {"all": 3, "primary": 0, "secondary": 1, "both": 1, "other": 2})
        self._assert_filtered_rows(AssessmentFilters(priority="secondary", search="HH-00010"), [10], findings)
        self._assert_filtered_rows(AssessmentFilters(priority="primary", search="HH-00010"), [], findings)
        after = self.connection.execute(
            "SELECT md5(string_agg(to_jsonb(h)::text, '' ORDER BY household_uuid)) "
            "FROM household_assessments h",
        ).fetchone()
        self.assertEqual(before, after)

    def test_all_subsidy_sets_in_broad_and_exact_modes_with_other_filters(self):
        findings = self._filter_fixture()
        before = self.connection.execute(
            "SELECT md5(string_agg(to_jsonb(h)::text, '' ORDER BY household_uuid)) "
            "FROM household_assessments h",
        ).fetchone()
        for size in range(5):
            for selected in combinations(PROGRAM_ORDER, size):
                for mode in ("any", "exact"):
                    expected = []
                    selected_set = set(selected)
                    for number, finding in enumerate(findings.values(), 1):
                        flagged = {
                            code for code, program in finding["programs"].items()
                            if program["screening_status"] == "For Review"
                        }
                        if mode == "exact":
                            matches = flagged == selected_set
                        else:
                            matches = bool(flagged.intersection(selected_set))
                        if not selected or matches:
                            expected.append(number)
                    with self.subTest(selected=selected, mode=mode):
                        self._assert_filtered_rows(
                            AssessmentFilters(programs=selected, subsidy_mode=mode), expected, findings,
                        )
        for completeness, numbers in (("unknown", [4, 8, 16]), ("determined", [2, 22, 26])):
            self._assert_filtered_rows(
                AssessmentFilters(
                    search="Synthetic", barangay="Synthetic West", programs=("4Ps",),
                    subsidy_mode="exact", completeness=completeness,
                ), numbers, findings,
            )
        # SPISC-only number 5 has an SLP Needs Data Review status, not an extra flag.
        rows = self._assert_filtered_rows(
            AssessmentFilters(programs=("SPISC",), subsidy_mode="exact"), [5, 25], findings,
        )
        self.assertTrue(any(row["program_statuses"]["SLP"] == "Needs Data Review" for row in rows))
        after = self.connection.execute(
            "SELECT md5(string_agg(to_jsonb(h)::text, '' ORDER BY household_uuid)) "
            "FROM household_assessments h",
        ).fetchone()
        self.assertEqual(before, after)

    def test_exact_filtered_pagination_counts_and_unchanged_findings(self):
        findings = self._filter_fixture()
        self.connection.execute(
            "UPDATE household_members SET responses = responses || %s WHERE batch_id=%s",
            (Jsonb({'a07_age': '60'}), self.batch_id),
        )
        for number, finding in enumerate(findings.values(), 1):
            for code, program in finding["programs"].items():
                program["screening_status"] = "No Historical CBMS Trigger"
                if code == "SPISC" or (code == "4Ps" and number > 30):
                    program["screening_status"] = "For Review"
                elif code == "SLP":
                    program["screening_status"] = "Needs Data Review"
        self._run(findings=findings)
        rows = self._assert_filtered_rows(
            AssessmentFilters(programs=("SPISC",), subsidy_mode="exact"), range(1, 31), findings,
        )
        self.assertEqual(len(rows), 30)

    def test_postgres_search_barangay_program_completeness_and_combinations(self):
        findings = self._filter_fixture()
        selected_run = service.get_active_assessment_context(connection_factory=self._factory)["run_id"]
        alternative = deepcopy(findings)
        for finding in alternative.values():
            finding["mpi"]["unknown_indicator_count"] = 0
            for program in finding["programs"].values():
                program["screening_status"] = "For Review"
        self._run(findings=alternative, designate=False)
        self.assertEqual(
            service.get_active_assessment_context(connection_factory=self._factory)["run_id"], selected_run,
        )
        cases = (
            (AssessmentFilters(), range(1, 33)),
            (AssessmentFilters(search="sYnThEtIc cOmMoN"), range(1, 31)),
            (AssessmentFilters(search="hh-00032"), [32]),
            (AssessmentFilters(barangay="Synthetic East"), range(1, 31, 2)),
            (AssessmentFilters(programs=("4Ps",)), range(2, 33, 2)),
            (AssessmentFilters(programs=("SLP",)), range(3, 33, 3)),
            (AssessmentFilters(programs=("SPISC",)), range(5, 33, 5)),
            (AssessmentFilters(programs=("TUPAD",)), range(7, 33, 7)),
            (AssessmentFilters(programs=("4Ps", "SLP")), [n for n in range(1, 33) if n % 2 == 0 or n % 3 == 0]),
            (AssessmentFilters(completeness="unknown"), range(4, 33, 4)),
            (AssessmentFilters(completeness="determined"), [n for n in range(1, 33) if n % 4]),
            (AssessmentFilters(
                search="synthetic common", barangay="Synthetic West",
                programs=("4Ps", "SLP"), completeness="unknown",
            ), range(4, 31, 4)),
            (AssessmentFilters(barangay="Synthetic East", completeness="unknown"), []),
            (AssessmentFilters(search="Does not exist"), []),
            (AssessmentFilters(search="HH-99999"), []),
        )
        before = self.connection.execute(
            "SELECT md5(string_agg(to_jsonb(h)::text, '' ORDER BY household_uuid)) FROM household_assessments h",
        ).fetchone()
        for filters, numbers in cases:
            with self.subTest(filters=filters):
                self._assert_filtered_rows(filters, numbers, findings)
        after = self.connection.execute(
            "SELECT md5(string_agg(to_jsonb(h)::text, '' ORDER BY household_uuid)) FROM household_assessments h",
        ).fetchone()
        self.assertEqual(before, after)

    def test_search_literals_unique_heads_and_source_batch_isolation(self):
        findings = self._filter_fixture()
        uuid = next(iter(findings))
        other_batch = self._batch(uuids=[uuid])
        self.connection.execute(
            "UPDATE households SET responses=%s WHERE batch_id=%s",
            (Jsonb({"barangay": "Unrelated Synthetic Barangay"}), other_batch),
        )
        self.connection.execute(
            """INSERT INTO household_members
               (batch_id, uuid, line_number, source_row_number, responses)
               VALUES (%s, %s, 1, 1, %s)""",
            (other_batch, uuid, Jsonb({
                "a02_relation_to_hh_head": "1", "a01_first_name": "Wrong Batch Name",
            })),
        )
        self.connection.execute(
            """UPDATE household_members SET responses=%s
               WHERE batch_id=%s AND uuid=%s AND line_number=1""",
            (Jsonb({"a02_relation_to_hh_head": "1", "a01_first_name": "Literal %_! Name",
                    "a07_age": "30"}), self.batch_id, uuid),
        )
        self._assert_filtered_rows(AssessmentFilters(search="%_!"), [1], findings)
        self._assert_filtered_rows(AssessmentFilters(search="Wrong Batch Name"), [], findings)
        self._assert_filtered_rows(AssessmentFilters(search="' OR 1=1 --"), [], findings)
        self.connection.execute(
            """INSERT INTO household_members
               (batch_id, uuid, line_number, source_row_number, responses)
               VALUES (%s, %s, 2, 33, %s)""",
            (self.batch_id, uuid, Jsonb({
                "a02_relation_to_hh_head": "2", "a01_first_name": "Non Head Name", "a07_age": "30",
            })),
        )
        self._assert_filtered_rows(AssessmentFilters(search="Non Head Name"), [], findings)
        self.connection.execute(
            """UPDATE household_members SET responses=%s
               WHERE batch_id=%s AND uuid=%s AND line_number=2""",
            (Jsonb({"a02_relation_to_hh_head": "1", "a01_first_name": "Ambiguous Head",
                    "a07_age": "30"}), self.batch_id, uuid),
        )
        self._assert_filtered_rows(AssessmentFilters(search="%_!"), [], findings)
        # Source-row IDs still work when no unique head name can be shown.
        self._assert_filtered_rows(AssessmentFilters(search="HH-00001"), [1], findings)
        with service._read_context(self._factory) as (connection, _):
            self.assertEqual(connection.execute("SHOW transaction_read_only").fetchone(), ("on",))
            repository.count_assessment_summaries(connection, 1, filters=AssessmentFilters(search="Literal"))


if __name__ == "__main__":
    unittest.main()
