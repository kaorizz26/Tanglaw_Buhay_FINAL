"""Synthetic SLP presentation checks; real database connections are blocked."""

from copy import deepcopy
from html import unescape
import re
import subprocess
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from jinja2 import ChoiceLoader, DictLoader
from services.assessment_reference_labels import evidence_cell
from services.mpi_evidence import build_evidence_index
from services.subsidy_screening_details import prepare_subsidy_screening

from test_subsidy_screening_details import ScreeningRouteTests


class SlpPresentationTests(unittest.TestCase):
    setUp = ScreeningRouteTests.setUp
    session = ScreeningRouteTests.session

    def evidence_rows(self):
        return prepare_subsidy_screening(
            self.source, build_evidence_index(self.source),
        )[1]['members']

    def test_missing_work_wording_counts_members_and_uses_batch_year(self):
        original = deepcopy(self.source['members'][0])
        self.set_finding('Needs Data Review')
        for count, prefix in (
            (1, "One household member's work status was not recorded"),
            (2, "Two household members' work statuses were not recorded"),
            (3, "3 household members' work statuses were not recorded"),
            (5, "5 household members' work statuses were not recorded"),
        ):
            for year in (2022, 2024):
                with self.subTest(count=count, year=year):
                    self.source['context']['survey_year'] = year
                    self.source['members'] = []
                    for line in range(1, count + 1):
                        member = deepcopy(original)
                        member['line_number'] = line
                        self.source['members'].append(member)
                    before = deepcopy(self.source)
                    program = prepare_subsidy_screening(
                        self.source, build_evidence_index(self.source),
                    )[1]
                    self.assertEqual(program['guidance']['why'], [
                        f'{prefix} in the {year} CBMS. Without this information, '
                        'the system could not establish an SLP screening condition.',
                    ])
                    for member in program['members']:
                        self.assertEqual(member['slp_uncertainty'],
                                         f'Work status was not recorded for this member in the {year} CBMS.')
                    self.assertEqual(self.source, before)

    def test_missing_work_count_ignores_repeated_evidence_and_member_identity(self):
        self.source['members'] = [self.source['members'][0]]
        evidence = {'member_line_number': 1, 'evidence_role': 'supporting_context',
                    'observed_values': {'e01_employed': 'NA'}}
        self.set_finding('Needs Data Review', (evidence, deepcopy(evidence)))
        self.source['members'].append(deepcopy(self.source['members'][0]))
        program = prepare_subsidy_screening(
            self.source, build_evidence_index(self.source),
        )[1]
        self.assertTrue(program['guidance']['why'][0].startswith("One household member's"))

    def test_other_uncertainty_categories_do_not_claim_missing_work_status(self):
        self.set_finding('Needs Data Review')
        self.source['members'] = [self.source['members'][0]]
        answers = self.source['members'][0]['responses']
        for field in ('b06_ofi', 'a07_age', 'e18_class_of_worker', 'e01_employed'):
            with self.subTest(field=field):
                answers.update(a07_age='45', b06_ofi='7', e01_employed='1',
                               e18_class_of_worker='3')
                answers[field] = 'invalid'
                program = prepare_subsidy_screening(
                    self.source, build_evidence_index(self.source),
                )[1]
                self.assertNotIn('work status was not recorded', program['guidance']['why'][0])
                self.assertNotIn('Work status was not recorded', program['members'][0]['slp_uncertainty'])

    def test_other_slp_status_wording_is_preserved(self):
        for pathway, expected in (
            ('EF', "SLP provides support for employment opportunities. The member's recorded work situation supports further review for employment assistance but does not establish eligibility."),
            ('MD', "SLP supports people who want to start, maintain, or develop livelihood activities. The member's recorded work situation supports further review for livelihood assistance but does not establish eligibility."),
        ):
            self.set_finding('For Review', (self.evidence(pathway),))
            program = prepare_subsidy_screening(
                self.source, build_evidence_index(self.source),
            )[1]
            self.assertEqual(program['guidance']['why'], [expected])
        self.set_finding('No Historical CBMS Trigger')
        program = prepare_subsidy_screening(
            self.source, build_evidence_index(self.source),
        )[1]
        self.assertEqual(program['guidance']['why'], [
            'The available 2022 CBMS records did not identify a household member meeting the conditions for an SLP review. This does not mean the household is ineligible or does not need assistance today.',
        ])

    def test_relevant_missing_fields_are_emphasized_with_specific_context(self):
        self.set_finding('Needs Data Review')
        self.source['members'] = [self.source['members'][0]]
        answers = self.source['members'][0]['responses']
        for field, explanation in (
            ('e01_employed', 'Work status was not recorded for this member in the 2022 CBMS.'),
            ('b06_ofi', 'residency information was unclear'),
            ('e18_class_of_worker', 'employment arrangement was not recorded'),
            ('a07_age', 'age at the survey was unclear'),
        ):
            with self.subTest(field=field):
                answers.update(a07_age='45', b06_ofi='7', e01_employed='1',
                               e18_class_of_worker='3')
                answers[field] = 'NA'
                before = deepcopy(self.source)
                rows = self.evidence_rows()
                self.assertIn(explanation, rows[0]['slp_uncertainty'])
                self.assertEqual(sum(c.get('slp_unavailable', False)
                                     for c in rows[0]['cells']), 1)
                article = self.render()
                self.assertIn('member-evidence slp-evidence-unresolved', article)
                self.assertIn('class="slp-answer-unavailable"', article)
                self.assertNotIn('Needs clarification', article)
                self.assertEqual(self.source, before)

    def test_skips_exclusions_and_other_members_remain_neutral(self):
        self.set_finding('Needs Data Review')
        answers = self.source['members'][0]['responses']
        for age, residency, work, worker in (
            ('45', '7', '2', 'NA'), ('45', '1', 'NA', 'NA'),
            ('17', '7', '2', 'NA'), ('17', '7', 'NA', '1'),
        ):
            answers.update(a07_age=age, b06_ofi=residency,
                           e01_employed=work, e18_class_of_worker=worker)
            for row in self.evidence_rows():
                self.assertNotIn('slp_uncertainty', row)
                self.assertFalse(any(c.get('slp_unavailable') for c in row['cells']))

    def test_multiple_relevant_members_and_invalid_answers_have_accurate_context(self):
        self.set_finding('Needs Data Review')
        self.source['members'][0]['responses']['a01_first_name'] = '<b>Example</b>'
        self.source['members'][2]['responses']['e01_employed'] = 'invalid'
        rows = self.evidence_rows()
        self.assertEqual(sum('slp_uncertainty' in row for row in rows), 2)
        self.assertEqual(rows[0]['slp_uncertainty'],
                         'Work status was not recorded for this member in the 2022 CBMS.')
        self.assertIn('work status was unclear', rows[-1]['slp_uncertainty'])
        article = self.render()
        self.assertIn('&lt;b&gt;Example&lt;/b&gt;', article)
        self.assertNotIn('<b>Example</b>', article)
        self.assertEqual(article.count('class="slp-answer-unavailable"'), 2)

    def test_highlighting_is_limited_to_saved_slp_unresolved_status(self):
        for status in ('For Review', 'No Historical CBMS Trigger'):
            self.set_finding(status)
            self.assertNotIn('slp-evidence-unresolved', self.render())

    def render(self):
        self.session()
        page = self.client.get(self.path + '?tab=screening&page=2&priority=secondary').text
        return re.search(r'<article[^>]+id="screening-slp".*?</article>', page, re.S)[0]

    def set_finding(self, status, evidence=()):
        self.source['findings']['programs']['SLP'].update(
            screening_status=status, supporting_evidence=evidence,
            data_quality_warnings=(), methodology_notes=(),
        )

    def evidence(self, pathway, line=1, worker='3', age='45'):
        answers = self.source['members'][line - 1]['responses']
        answers.update(a07_age=age, b06_ofi='7',
                       e01_employed='2' if pathway == 'EF' else '1',
                       e18_class_of_worker=worker)
        return {'member_line_number': line, 'pathway': pathway,
                'observed_values': {k: answers[k] for k in (
                    'a07_age', 'b06_ofi', 'e01_employed', 'e18_class_of_worker')}}

    def test_ef_saved_evidence_names_age_escaping_and_no_recalculation(self):
        item = self.evidence('EF')
        self.set_finding('For Review', (item,))
        self.source['members'][0]['responses']['a01_first_name'] = '<script>Example</script>'
        before = deepcopy(self.source)
        with patch('services.subsidy_screening.screen_slp', side_effect=AssertionError('No rescreening')):
            article = self.render()
        for text in ('Employment Facilitation', 'Help with finding or preparing for work',
                     'Age 45 at the 2022 CBMS survey.', 'Reported not working during the week',
                     'does not establish eligibility', '&lt;script&gt;Example&lt;/script&gt;'):
            self.assertIn(text, article)
        self.assertNotIn('<script>Example</script>', article)
        self.assertEqual(self.source, before)

    def test_every_md_class_and_minor_has_distinct_decoded_work(self):
        descriptions = []
        for worker in ('3', '4', '5', '6'):
            with self.subTest(worker=worker):
                self.set_finding('For Review', (self.evidence('MD', worker=worker, age='16'),))
                article = self.render()
                self.assertIn('Microenterprise Development', article)
                self.assertIn('Age 16 at the 2022', article)
                cards = re.search(r'<ul class="slp-findings">(.*?)</ul>', article, re.S)[1]
                descriptions.append(cards)
                meaning = evidence_cell('e18_class_of_worker', worker, 'section_a_e_indiv',
                                        build_evidence_index(self.source))['meaning']
                self.assertIn(meaning, unescape(cards))
                self.assertNotIn('guardian', article.lower())
                self.assertNotIn('consent', article.lower())
        self.assertEqual(len(set(descriptions)), 4)

    def test_both_pathways_multiple_members_and_deduplication(self):
        ef, md = self.evidence('EF'), self.evidence('MD', line=3)
        self.set_finding('For Review', (ef, md, md))
        article = self.render()
        self.assertEqual(article.count('class="slp-pathway"'), 2)
        self.assertIn('Synthetic Member 1', article)
        self.assertIn('Synthetic Member 3', article)
        questions = re.search(r'<ol>(.*?)</ol>', article, re.S)[1]
        self.assertEqual(questions.count('<li>'), 7)
        self.assertEqual(questions.count('previously received SLP'), 1)
        self.assertEqual(article.count('Start SLP Review</a>'), 1)

    def test_missing_applicable_fields_multiple_members_and_expected_skips(self):
        self.set_finding('Needs Data Review')
        self.source['members'] = [self.source['members'][0]]
        answers = self.source['members'][0]['responses']
        for field, label in (('e01_employed', 'Work status: Not recorded'),
                             ('b06_ofi', 'Residency information'),
                             ('e18_class_of_worker', 'Employment arrangement'),
                             ('a07_age', 'Age at survey is not recorded')):
            with self.subTest(field=field):
                answers.update(a07_age='45', b06_ofi='7', e01_employed='1', e18_class_of_worker='3')
                answers[field] = 'NA'
                article = self.render()
                self.assertIn(label, article)
                self.assertNotIn('class="slp-pathway"', article)
        answers.update(a07_age='45', b06_ofi='7', e01_employed='2', e18_class_of_worker='NA')
        article = self.render()
        self.assertNotIn('Employment arrangement:', article)
        self.assertNotIn('Work status: Not recorded', article)
        self.assertIn('may have been skipped as expected', article)
        answers['e01_employed'] = 'NA'
        self.source['members'].append(deepcopy(self.source['members'][0]))
        self.source['members'][1]['line_number'] = 7
        self.assertEqual(self.render().count('Work status: Not recorded'), 2)

    def test_known_exclusions_do_not_invent_missing_work_information(self):
        self.set_finding('Needs Data Review')
        self.source['members'] = [self.source['members'][0]]
        answers = self.source['members'][0]['responses']
        for age, residency, work, worker in (
            ('15', '7', 'NA', 'NA'), ('45', '1', 'NA', 'NA'),
            ('17', '7', '2', 'NA'), ('45', '7', '1', '1'),
            ('17', '7', 'NA', '1'),
        ):
            answers.update(a07_age=age, b06_ofi=residency,
                           e01_employed=work, e18_class_of_worker=worker)
            self.assertIn('does not identify one reliable cause', self.render())

    def test_saved_nonwork_requires_recorded_answer_and_positive_wins_over_gaps(self):
        ef = self.evidence('EF')
        self.set_finding('For Review', (ef,))
        self.source['members'][2]['responses']['e01_employed'] = 'NA'
        article = self.render()
        self.assertIn('Employment Facilitation', article)
        self.assertIn('Work status: Not recorded', article)
        self.assertIn('For Review', article)
        ef['observed_values']['e01_employed'] = 'NA'
        article = self.render()
        self.assertNotIn('Reported not working during the week before the survey.', article)
        self.assertIn('recorded nonwork is not established', article)

    def test_status_defaults_independent_evidence_and_review_navigation(self):
        for status in ('For Review', 'Needs Data Review', 'No Historical CBMS Trigger'):
            self.set_finding(status)
            article = self.render()
            tag = re.search(r'<details class="slp-disclosure"[^>]*>', article)[0]
            self.assertEqual(' open' in tag, status != 'No Historical CBMS Trigger')
            self.assertIn('<details class="screening-evidence">', article)
            if status != 'For Review':
                self.assertIn('A brief reason is required when starting this review.', article)
        self.assertIn('does not mean the household is ineligible', article)
        for state, withdrawn, label in (('In Review', False, 'Open'), ('Closed', False, 'View'),
                                         ('Closed', True, 'Start'), ('Cancelled', False, 'Start')):
            self.source['staff_reviews'] = [{'id': 7, 'current_review_id': 7, 'program': 'SLP',
                                            'status': state, 'withdrawn': withdrawn}]
            article = self.render()
            link = re.search(r'href="([^"]+)"[^>]*>' + label + r' SLP Review</a>', article)[1]
            parsed = urlsplit(unescape(link))
            self.assertEqual(parse_qs(parsed.query)['priority'], ['secondary'])
            self.assertEqual(parse_qs(parsed.query)['page'], ['2'])
            self.assertIn('/reviews/SLP', parsed.path)
        self.source['staff_reviews'].append({'id': 8, 'current_review_id': 8,
                                            'program': 'SLP', 'status': 'In Review'})
        self.assertIn('Open SLP Review', self.render())

    def test_committed_other_programs_and_summary_render_identically(self):
        self.session()
        current = self.client.get(self.path + '?tab=screening').text
        old = subprocess.check_output(['git', 'show', 'HEAD:templates/assessments/screening.html']).decode('utf-8')
        self.app.jinja_env.loader = ChoiceLoader([DictLoader({'assessments/screening.html': old}), self.app.jinja_env.loader])
        self.app.jinja_env.cache.clear()
        baseline = self.client.get(self.path + '?tab=screening').text
        for code in ('4ps', 'spisc', 'tupad'):
            pattern = rf'<article[^>]+id="screening-{code}".*?</article>'
            self.assertEqual(re.search(pattern, current, re.S)[0], re.search(pattern, baseline, re.S)[0])
        pattern = r'<dl class="assessment-metrics".*?</dl>'
        self.assertEqual(re.search(pattern, current, re.S)[0], re.search(pattern, baseline, re.S)[0])
