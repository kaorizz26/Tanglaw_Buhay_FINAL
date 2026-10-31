"""Origin-aware navigation between Household Records and assessments (synthetic data only)."""

from datetime import datetime, timezone
from html import unescape
import re
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlencode, urlsplit

from werkzeug.datastructures import MultiDict

from assessment_detail_fixtures import HOUSEHOLD_UUID
from services.household_records import (
    InvalidRecordFilters, RecordFilters, parse_record_return, record_return_parameters,
)
from services.review_queue import QueueFilters, queue_return_parameters
import test_staff_reviews as reviews


RECORD_URL = f'/households/{HOUSEHOLD_UUID}'
LIST_STATE = {'page': '2', 'q': 'Synthetic', 'barangay': 'Synthetic East'}
RECORD_STATE = {'from_record': '1', 'record_page': '2', 'record_q': 'Synthetic',
                'record_barangay': 'Synthetic East'}


def links_in(page):
    """Return every link address on a rendered page, with HTML escaping removed."""
    return [unescape(link) for link in re.findall(r'href="([^"]+)"', page)]


class RecordReturnParserTests(unittest.TestCase):
    def test_round_trip_keeps_page_search_and_barangay(self):
        filters = RecordFilters('Synthetic Head', 'Synthetic East')
        parameters = record_return_parameters(3, filters)
        # A real request delivers the parameters as text, so encode them first.
        arguments = MultiDict(parse_qs(urlencode(parameters)))
        self.assertEqual(parse_record_return(arguments), (3, filters))

    def test_page_without_record_origin_returns_none(self):
        self.assertIsNone(parse_record_return(MultiDict([('tab', 'mpi'), ('page', '2')])))

    def test_malformed_origins_are_rejected(self):
        bad_queries = (
            'from_record=2', 'from_record=1&from_record=1', 'record_page=2',
            'from_record=1&record_url=https://evil.test', 'from_record=1&record_page=0',
            'from_record=1&record_page=1&record_page=2', 'from_record=1&record_q=a&record_q=b',
            'from_record=1&record_q=' + 'x' * 201, 'from_record=1&record_status=open',
        )
        for query in bad_queries:
            with self.subTest(query=query[:60]):
                with self.assertRaises(InvalidRecordFilters):
                    parse_record_return(MultiDict(parse_qs(query)))


class RecordNavigationRouteTests(unittest.TestCase):
    def setUp(self):
        self.fixture = reviews.StaffReviewRouteTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.client = self.fixture.client
        self.fixture.session()
        self.assessment = self.fixture.path
        self.query = urlencode(RECORD_STATE)
        self.fixture.source['staff_reviews'] = [{
            'id': 42, 'current_review_id': 42, 'program': '4Ps', 'status': 'In Review',
            'username': 'synthetic_staff', 'notes': [], 'starting_reason': '',
            'created_at': datetime(2026, 10, 17, tzinfo=timezone.utc),
            'closing_recommendation': None,
        }]

    def record_back_link(self, page):
        return next(link for link in links_in(page)
                    if link.startswith(RECORD_URL) and 'page=2' in link)

    def test_record_page_links_to_assessment_with_list_state(self):
        record = {
            'context': {'survey_year': 2022, 'municipality': 'Pozorrubio'},
            'household_uuid': str(HOUSEHOLD_UUID), 'reference_id': 'HH-00001',
            'head_name': 'Synthetic Head', 'barangay': 'Synthetic East',
            'member_count': 0, 'groups': [], 'members': [],
        }
        with patch('routes.households.get_record_detail', return_value=record):
            page = self.client.get(RECORD_URL + '?' + urlencode(LIST_STATE))
        link = next(link for link in links_in(page.text) if link.startswith(self.assessment))
        self.assertEqual({key: value[0] for key, value in parse_qs(urlsplit(link).query).items()},
                         RECORD_STATE)

    def test_assessment_returns_to_the_same_household_record(self):
        page = self.client.get(self.assessment + '?' + self.query)
        self.assertEqual(page.status_code, 200)
        self.assertIn('Back to Household Record', page.text)
        self.assertNotIn('Back to Prioritization', page.text)
        back = self.record_back_link(page.text)
        self.assertEqual(urlsplit(back).path, RECORD_URL)
        self.assertEqual({key: value[0] for key, value in parse_qs(urlsplit(back).query).items()},
                         LIST_STATE)

    def test_origin_survives_every_tab_and_review_link(self):
        for tab in ('overview', 'mpi', 'screening', 'reviews'):
            with self.subTest(tab=tab):
                page = self.client.get(f'{self.assessment}?tab={tab}&{self.query}')
                self.assertEqual(page.status_code, 200)
                self.assertIn('Back to Household Record', page.text)
                tab_links = [link for link in links_in(page.text)
                             if link.startswith(self.assessment) and 'tab=' in link]
                self.assertTrue(tab_links)
                for link in tab_links:
                    self.assertEqual(parse_qs(urlsplit(link).query)['record_page'], ['2'])
        review = self.assessment + '/reviews/4Ps/42'
        page = self.client.get(review + '?' + self.query)
        self.assertEqual(page.status_code, 200)
        action_links = [link for link in links_in(page.text) if 'action=finish' in link]
        self.assertEqual(parse_qs(urlsplit(action_links[0]).query)['record_q'], ['Synthetic'])
        self.assertIn('record_barangay=Synthetic+East',
                      next(link for link in links_in(page.text) if 'tab=reviews' in link))
        start = self.client.post(self.assessment + '/reviews/SLP?' + self.query, data={
            'csrf_token': 'test-token', 'batch_id': '1', 'run_id': '2',
            'starting_reason': 'Synthetic reason.',
        })
        self.assertEqual(start.status_code, 303)
        self.assertEqual(parse_qs(urlsplit(start.location).query)['record_page'], ['2'])

    def test_prioritization_and_queue_returns_are_unchanged(self):
        prioritization = self.client.get(self.assessment + '?page=3&q=Synthetic')
        self.assertIn('Back to Prioritization', prioritization.text)
        self.assertNotIn('Back to Household Record', prioritization.text)
        self.assertIn('/assessments?page=3&amp;q=Synthetic', prioritization.text)
        queue_query = urlencode(queue_return_parameters(2, QueueFilters()), doseq=True)
        queue_page = self.client.get(self.assessment + '?' + queue_query)
        self.assertIn('Back to Review Queue', queue_page.text)
        self.assertNotIn('Back to Household Record', queue_page.text)

    def test_invalid_and_conflicting_origins_are_rejected(self):
        queue_query = urlencode(queue_return_parameters(2, QueueFilters()), doseq=True)
        bad_queries = (
            'from_record=2', 'record_page=2', 'from_record=1&record_page=0',
            'from_record=1&record_q=a&record_q=b', 'from_record=1&record_url=https://evil.test',
            'from_record=1&record_q=' + 'x' * 201,
            # Two origins at once are ambiguous.
            self.query + '&' + queue_query,
        )
        for query in bad_queries:
            with self.subTest(query=query[:60]):
                self.assertEqual(self.client.get(self.assessment + '?' + query).status_code, 400)
        self.assertEqual(
            self.client.get(self.assessment + '/reviews/4Ps/42?from_record=2').status_code, 400)


if __name__ == '__main__':
    unittest.main()
