import unittest
from unittest.mock import MagicMock, patch

from repositories import dataset_batches
from services import assessment_queries as service, read_cache


class ReadCacheTests(unittest.TestCase):
    def setUp(self):
        read_cache.clear()
        self.addCleanup(read_cache.clear)

    def test_value_is_reused_until_expiry_and_callers_get_copies(self):
        loads = []

        def load():
            loads.append(1)
            return {"items": [1]}

        with patch("services.read_cache.time.monotonic", side_effect=[0, 1, 301]):
            first = read_cache.cached("k", 300, load)
            first["items"].append(2)
            self.assertEqual(read_cache.cached("k", 300, load), {"items": [1]})
            self.assertEqual(len(loads), 1)
            read_cache.cached("k", 300, load)
        self.assertEqual(len(loads), 2)

    def test_failed_load_is_not_cached(self):
        with self.assertRaises(ValueError):
            read_cache.cached("k", 300, MagicMock(side_effect=ValueError))
        self.assertEqual(read_cache.cached("k", 300, lambda: "ok"), "ok")

    def test_cache_is_bounded(self):
        for number in range(read_cache.MAX_ENTRIES + 5):
            read_cache.cached(number, 300, lambda: number)
        self.assertLessEqual(len(read_cache._entries), read_cache.MAX_ENTRIES)

    def test_barangays_are_cached_per_database_and_batch(self):
        def connection(dbname):
            mock = MagicMock()
            mock.info.dbname = dbname
            mock.execute.return_value.fetchall.return_value = [("Alpha",)]
            return mock
        one = connection("a")
        self.assertEqual(dataset_batches.list_barangays(one, 1), ["Alpha"])
        dataset_batches.list_barangays(one, 1)
        self.assertEqual(one.execute.call_count, 1)
        dataset_batches.list_barangays(one, 2)
        dataset_batches.list_barangays(connection("b"), 1)
        self.assertEqual(one.execute.call_count, 2)

    def test_dashboard_cache_key_changes_with_run_and_date(self):
        context = {"batch_id": 1, "run_id": 3, "completed_at": "t"}
        connection = MagicMock()
        connection.info.dbname = "d"
        calls = []

        def run(context, date):
            with patch.object(service, "_read_context") as read, patch.object(
                service, "_read_dashboard_summary",
                side_effect=lambda *a: calls.append(a) or {"n": len(calls)},
            ):
                read.return_value.__enter__.return_value = (connection, context)
                return service.get_active_dashboard_summary(assessment_date=date)

        self.assertEqual(run(context, "2026-10-25"), run(context, "2026-10-25"))
        self.assertEqual(len(calls), 1)
        run(context, "2026-10-26")
        run({**context, "run_id": 4}, "2026-10-26")
        run({**context, "batch_id": 2}, "2026-10-26")
        self.assertEqual(len(calls), 4)


if __name__ == "__main__":
    unittest.main()
