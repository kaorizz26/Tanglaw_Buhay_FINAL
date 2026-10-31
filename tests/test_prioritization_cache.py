"""Prioritization cache checks use mocked repositories and synthetic findings only."""

from copy import deepcopy
from datetime import date
from secrets import token_urlsafe
from time import time
import unittest
from unittest.mock import MagicMock, patch

import psycopg
from psycopg.pq import TransactionStatus

from app import create_app
from repositories import assessment_evidence
from repositories import assessment_runs as repository
from services import assessment_queries as service, read_cache
from services.assessment_filters import AssessmentFilters
from services.assessment_queries import AssessmentQueryError
from services.spisc_age_progression import current_spisc_status


TODAY = date(2026, 10, 31)
ROW = {
    "household_uuid": "SYNTHETIC-0001", "vulnerability_rank": 1,
    "vulnerability_score": 30.0, "coverage_pct": 100.0,
    "data_quality_status": "Complete Data", "unknown_indicator_count": 0,
    "program_statuses": {"4Ps": "For Review", "SLP": "No Historical CBMS Trigger",
                         "SPISC": "No Historical CBMS Trigger", "TUPAD": "For Review"},
}
COUNTS = {"all": 10, "primary": 2, "secondary": 3, "both": 5, "other": 5}


class PrioritizationCacheTests(unittest.TestCase):
    def setUp(self):
        read_cache.clear()
        self.addCleanup(read_cache.clear)
        self.connection = MagicMock()
        self.connection.__enter__.return_value = self.connection
        self.connection.info.transaction_status = TransactionStatus.IDLE
        self.connection.info.dbname = "synthetic_database"
        self.factory = MagicMock(return_value=self.connection)
        self.batch = {"id": 9, "survey_year": 2022, "municipality": "Pozorrubio",
                      "validation_status": "validated", "is_active": True,
                      "source_provenance": {"kind": "synthetic"}}
        self.run = {"run_id": 17, "batch_id": 9, "calculation_version": "synthetic-v1",
                    "calculation_provenance": {"kind": "synthetic"},
                    "completed_at": "synthetic timestamp", "household_count": 10}
        patches = (
            patch.object(service.dataset_batches, "find_active_batches",
                         side_effect=lambda connection: [self.batch]),
            patch.object(repository, "find_designated_run",
                         side_effect=lambda connection, batch_id: self.run),
            patch.object(assessment_evidence, "list_recorded_ages", return_value=["70", "59"]),
            patch.object(repository, "list_assessment_summaries", return_value=[deepcopy(ROW)]),
            patch.object(repository, "count_assessment_summaries", return_value=4),
            patch.object(repository, "count_priority_groups", return_value=dict(COUNTS)),
            patch.object(service.dataset_batches, "list_barangays",
                         return_value=["Synthetic East"]),
            patch.object(service.dataset_batches, "find_household_identities",
                         return_value=[{"household_uuid": "SYNTHETIC-0001",
                                        "source_row_number": 1, "head_count": 1}]),
            patch("psycopg.connect", side_effect=AssertionError("No real connections")),
        )
        mocks = []
        for patcher in patches:
            mocks.append(patcher.start())
            self.addCleanup(patcher.stop)
        (_, _, self.ages, self.page, self.count, self.groups, _, _, _) = mocks

    def read(self, *, page=1, filters=None, assessment_date=TODAY, page_size=25, **flags):
        """Call the service the way the Prioritization page does, without identities."""
        return service.list_active_assessment_summaries(
            page=page, page_size=page_size, filters=filters, assessment_date=assessment_date,
            include_barangays=True, include_priority_counts=True,
            connection_factory=self.factory, **flags,
        )

    def test_identical_requests_reuse_the_page_but_still_check_the_context(self):
        first = self.read(filters=AssessmentFilters(priority="primary"))
        second = self.read(filters=AssessmentFilters(priority="primary"))
        self.assertEqual(first, second)
        self.assertEqual(self.page.call_count, 1)
        self.assertEqual(self.groups.call_count, 1)
        self.assertEqual(self.ages.call_count, 1)
        # Each request still opens a read-only snapshot and validates the active run.
        self.assertEqual(self.factory.call_count, 2)

    def test_different_filters_pages_and_sizes_do_not_share_entries(self):
        self.read()
        variations = (
            {"page": 2}, {"page_size": 10}, {"filters": AssessmentFilters(search="x")},
            {"filters": AssessmentFilters(barangay="Synthetic East")},
            {"filters": AssessmentFilters(programs=("4Ps",))},
            {"filters": AssessmentFilters(programs=("4Ps",), subsidy_mode="exact")},
            {"filters": AssessmentFilters(completeness="unknown")},
            {"filters": AssessmentFilters(priority="other")},
            {"include_household_identity": True},
        )
        for variation in variations:
            with self.subTest(variation=variation):
                before = self.page.call_count
                self.read(**variation)
                self.assertEqual(self.page.call_count, before + 1)
                self.read(**variation)
                self.assertEqual(self.page.call_count, before + 1)

    def test_new_batch_run_completion_or_date_never_reuses_old_results(self):
        self.read()
        self.assertEqual(self.page.call_count, 1)
        self.read(assessment_date=date(2026, 11, 1))
        self.assertEqual(self.page.call_count, 2)
        self.run = {**self.run, "completed_at": "later synthetic timestamp"}
        self.read()
        self.assertEqual(self.page.call_count, 3)
        self.run = {**self.run, "run_id": 18}
        self.read()
        self.assertEqual(self.page.call_count, 4)
        self.batch = {**self.batch, "id": 10}
        self.run = {**self.run, "batch_id": 10}
        self.read()
        self.assertEqual(self.page.call_count, 5)
        self.connection.info.dbname = "another_database"
        self.read()
        self.assertEqual(self.page.call_count, 6)

    def test_priority_counts_ignore_only_the_selected_priority(self):
        counts = {}
        for priority in ("all", "both", "primary", "secondary", "other"):
            counts[priority] = self.read(
                filters=AssessmentFilters(barangay="Synthetic East", priority=priority),
            )["priority_counts"]
        self.assertEqual(self.groups.call_count, 1)
        self.assertTrue(all(value == COUNTS for value in counts.values()))
        # Every page differs by priority, so each card still reads its own rows.
        self.assertEqual(self.page.call_count, 5)
        # Any other filter changes the counts and must not reuse them.
        for other in (AssessmentFilters(barangay="Elsewhere"), AssessmentFilters(search="x"),
                      AssessmentFilters(programs=("SLP",)),
                      AssessmentFilters(programs=("SLP",), subsidy_mode="exact"),
                      AssessmentFilters(completeness="determined")):
            with self.subTest(filters=other):
                before = self.groups.call_count
                self.read(filters=other)
                self.assertEqual(self.groups.call_count, before + 1)

    def test_age_statuses_match_current_spisc_logic_and_follow_the_date(self):
        context = {"survey_year": 2022, "source_provenance": {"kind": "synthetic"}}
        for assessed in (TODAY, date(2027, 6, 1), TODAY):
            self.read(assessment_date=assessed, filters=AssessmentFilters(search="x"))
            expected = {age: current_spisc_status([age], context, assessed)
                        for age in ("70", "59")}
            self.assertEqual(self.page.call_args.kwargs["spisc_age_statuses"], expected)
        # Two dates were classified; the repeated first date came from memory.
        self.assertEqual(self.ages.call_count, 2)

    def test_cached_and_uncached_results_are_identical(self):
        filters = AssessmentFilters(priority="primary", programs=("4Ps",))
        uncached = self.read(filters=filters)
        cached = self.read(filters=filters)
        read_cache.clear()
        reread = self.read(filters=filters)
        self.assertEqual(uncached, cached)
        self.assertEqual(uncached, reread)

    def test_failures_and_incomplete_reads_are_not_cached(self):
        self.page.side_effect = [psycopg.OperationalError("SYNTHETIC_PRIVATE"), [deepcopy(ROW)]]
        with self.assertRaises(AssessmentQueryError) as failure:
            self.read()
        self.assertEqual(str(failure.exception), "assessment_query_unavailable")
        self.assertEqual(len(self.read()["items"]), 1)
        self.assertEqual(len(self.read()["items"]), 1)
        self.assertEqual(self.page.call_count, 2)

    def test_changing_a_returned_page_cannot_change_the_stored_entry(self):
        first = self.read()
        first["items"].append({"household_uuid": "TAMPERED"})
        first["items"][0]["program_statuses"]["4Ps"] = "TAMPERED"
        first["priority_counts"]["all"] = -1
        first["context"]["batch_id"] = -1
        first["barangays"].append("TAMPERED")
        second = self.read()
        self.assertEqual(len(second["items"]), 1)
        self.assertEqual(second["items"][0]["program_statuses"]["4Ps"], "For Review")
        self.assertEqual(second["priority_counts"], COUNTS)
        self.assertEqual(second["context"]["batch_id"], 9)
        self.assertEqual(second["barangays"], ["Synthetic East"])


class PrioritizationCacheAuthorizationTests(unittest.TestCase):
    """Authentication runs before any cache lookup."""

    def setUp(self):
        self.app = create_app({"TESTING": True, "SECRET_KEY": token_urlsafe(48),
                               "DATABASE_URL": None})
        self.client = self.app.test_client()
        self.user = {"id": 1, "username": "synthetic_staff", "role": "lgu_staff",
                     "is_active": True, "session_version": 1}
        patches = (
            patch("services.auth.get_connection", return_value=MagicMock()),
            patch("repositories.staff_users.find_by_id",
                  side_effect=lambda *args: dict(self.user)),
            patch("routes.assessments.get_prioritization_page", return_value=None),
        )
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)

    def _sign_in(self):
        with self.client.session_transaction() as session:
            session.update(user_id=1, session_version=1, issued_at=int(time()),
                           csrf_token=token_urlsafe(32))

    def test_signed_out_inactive_and_unknown_roles_never_reach_the_cache(self):
        with patch.object(read_cache, "cached") as cache:
            self.assertEqual(self.client.get("/assessments?priority=primary").location, "/login")
            for field, value in (("is_active", False), ("role", "unapproved"),
                                 ("session_version", 2)):
                with self.subTest(field=field), patch.dict(self.user, {field: value}):
                    self._sign_in()
                    self.assertEqual(self.client.get("/assessments").location, "/login")
            cache.assert_not_called()

    def test_signed_in_staff_and_administrators_see_the_same_service_call(self):
        for role in ("lgu_staff", "system_administrator"):
            with self.subTest(role=role), patch.dict(self.user, role=role):
                self._sign_in()
                self.assertEqual(self.client.get("/assessments").status_code, 404)
