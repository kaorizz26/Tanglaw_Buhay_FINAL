"""Synthetic TUPAD presentation checks; real database connections are blocked."""

from copy import deepcopy
from html import unescape
import re
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import test_subsidy_screening_details as screening_tests


class TupadPresentationTests(unittest.TestCase):
    setUp = screening_tests.ScreeningRouteTests.setUp
    session = screening_tests.ScreeningRouteTests.session

    def render(self):
        self.session()
        page = self.client.get(self.path + '?tab=screening&page=2&priority=secondary').text
        return re.search(r'<article[^>]+id="screening-tupad".*?</article>', page, re.S)[0]

    def set_finding(self, status, evidence=()):
        self.source['findings']['programs']['TUPAD'].update(
            screening_status=status, supporting_evidence=evidence,
            data_quality_warnings=(), methodology_notes=(),
        )

    def evidence(self, kind, line=1, age='45'):
        return {'member_line_number': line, 'trigger_code': kind, 'evidence_type': 'observed',
                'observed_values': {'a07_age': age, 'b06_ofi': '7'}}

    def test_for_review_names_each_member_age_and_recorded_reason_without_rescreening(self):
        self.set_finding('For Review', (
            self.evidence('observed_nonwork'), self.evidence('irregular_work', line=3, age='52'),
            self.evidence('irregular_work', line=3, age='52'),
        ))
        self.source['members'][0]['responses']['a01_first_name'] = '<script>x</script>'
        before = deepcopy(self.source)
        with patch('services.subsidy_screening.screen_tupad', side_effect=AssertionError('No rescreening')):
            article = self.render()
        for text in ('Age 45 at the 2022 CBMS survey.', 'Age 52 at the 2022 CBMS survey.',
                     'Recorded as not working during the week before the survey.',
                     'Short-term, seasonal, casual, or day-to-day work was recorded',
                     'saved findings about the members',
                     'not establish eligibility or current unemployment',
                     'Start a TUPAD Staff Review', 'Start TUPAD Review</a>',
                     '&lt;script&gt;x&lt;/script&gt;'):
            self.assertIn(text, article)
        self.assertEqual(article.count('Short-term, seasonal'), 1)
        self.assertNotIn('<script>x</script>', article)
        self.assertNotIn('A brief reason is required', article)
        for text in ('trigger_code', 'pilot', 'synthetic-support', 'provisional'):
            self.assertNotIn(text, article)
        self.assertEqual(self.source, before)

    def test_legacy_assumed_findings_stay_labelled_as_not_recorded_answers(self):
        legacy = self.evidence('assumed_nonwork')
        context = dict(self.evidence('assumed_overseas_work'), evidence_role='supporting_context')
        self.set_finding('For Review', (legacy, context))
        article = self.render()
        self.assertIn('not a recorded No answer', article)
        self.assertIn('Employment was not confirmed by a work response.', article)
        self.assertEqual(article.count('class="slp-findings"'), 1)
        self.assertEqual(len(re.findall(r'<li>\s*<strong>', re.search(
            r'<ul class="slp-findings">(.*?)</ul>', article, re.S)[1])), 1)

    def test_unresolved_members_are_described_and_expected_skips_are_not(self):
        self.set_finding('Needs Data Review')
        self.source['members'] = [self.source['members'][0]]
        answers = self.source['members'][0]['responses']
        for field, text in (('e01_employed', 'Work status was not recorded'),
                            ('b06_ofi', 'Residency information is not recorded'),
                            ('a07_age', 'Age at survey is not recorded')):
            with self.subTest(field=field):
                answers.update(a07_age='45', b06_ofi='7', e01_employed='2')
                answers[field] = 'NA'
                article = self.render()
                self.assertIn(text, article)
                self.assertIn('Why more information is needed', article)
                self.assertIn('Staff may start a TUPAD Review', article)
                self.assertIn('A brief reason is required when starting this review.', article)
        answers.update(a07_age='45', b06_ofi='7', e01_employed='1', e11_nature_of_employment='NA')
        self.assertIn('nature of the work was not recorded', self.render())
        # Regular work excludes a member only once age and residency are known.
        for field, text in (('a07_age', 'Age at survey is not recorded'),
                            ('b06_ofi', 'Residency information is not recorded')):
            answers.update(a07_age='45', b06_ofi='7', e01_employed='1', e11_nature_of_employment='1')
            answers[field] = 'NA'
            self.assertIn(text, self.render())
        for age, residency, work, nature in (('17', '7', 'NA', 'NA'), ('45', '1', 'NA', 'NA'),
                                             ('45', '7', '1', '1')):
            answers.update(a07_age=age, b06_ofi=residency, e01_employed=work,
                           e11_nature_of_employment=nature)
            self.assertIn('does not identify one reliable cause', self.render())

    def test_shared_panel_heading_ids_are_unique_per_program(self):
        self.session()
        page = self.client.get(self.path + '?tab=screening').text
        ids = re.findall(r'\sid="([^"]+)"', page)
        self.assertEqual(len(ids), len(set(ids)))
        for code in ('slp', 'tupad'):
            self.assertIn(f'aria-labelledby="{code}-found-heading"', page)
            self.assertIn(f'id="{code}-found-heading"', page)

    def test_no_review_signal_and_review_state_navigation(self):
        self.set_finding('No Historical CBMS Trigger')
        article = self.render()
        tag = re.search(r'<details class="slp-disclosure"[^>]*>', article)[0]
        self.assertNotIn(' open', tag)
        for text in ('Why there is no review signal', 'does not mean the household is ineligible',
                     'If staff have newer information:',
                     'changed since the 2022 survey', 'previously received TUPAD'):
            self.assertIn(text, article)
        for state, withdrawn, label in (('In Review', False, 'Open'), ('Closed', False, 'View'),
                                         ('Closed', True, 'Start')):
            self.source['staff_reviews'] = [{'id': 7, 'current_review_id': 7, 'program': 'TUPAD',
                                            'status': state, 'withdrawn': withdrawn}]
            link = re.search(r'href="([^"]+)"[^>]*>' + label + r' TUPAD Review</a>', self.render())[1]
            parsed = urlsplit(unescape(link))
            self.assertIn('/reviews/TUPAD', parsed.path)
            self.assertEqual(parse_qs(parsed.query)['page'], ['2'])


if __name__ == '__main__':
    unittest.main()
