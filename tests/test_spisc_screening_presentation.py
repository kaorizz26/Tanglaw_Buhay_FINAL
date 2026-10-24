"""Synthetic SPISC guidance and protected UI regressions; no real database."""

from copy import deepcopy
from datetime import date, datetime
from html import unescape
import re
import unittest
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

from assessment_detail_fixtures import screening_source
from services.assessment_details import prepare_assessment_detail
from services.mpi_evidence import build_evidence_index
from services.spisc_screening_presentation import (
    philippine_assessment_date, prepare_spisc_guidance,
)
from services.subsidy_screening_details import prepare_subsidy_screening
from repositories import assessment_evidence
from services.spisc_disability_presentation import (
    DISABILITY_TYPES, HOUSEHOLD_LABELS, MEMBER_LABELS, prepare_disability_information,
)
import test_subsidy_screening_details as screening_tests


ASSESSMENT_DATE = date(2026, 10, 24)


def disability_fixture(source):
    """Explicit synthetic mappings matching the reviewed imported field contract."""
    codebook = source['references']['codebook']
    existing = {row['variable_name'] for row in codebook}
    for field in (*HOUSEHOLD_LABELS, *MEMBER_LABELS):
        if field not in existing:
            labels = ('Visual disability', 'Deaf or hearing disability',
                      'Intellectual disability', 'Learning disability',
                      'Mental disability', 'Physical disability (orthopedic)',
                      'Psychosocial disability', 'Speech and language impairment',
                      'Cancer (RA 11215)', 'Rare disease (RA 10747)') if field.startswith('l32') else ('Yes', 'No')
            codebook.extend({'variable_name': field, 'code': str(code), 'label': label}
                            for code, label in enumerate(labels, 1))
    source['household']['l29_with_member_who_has_pwd_id'] = '2'
    for member in source['members']:
        member['health_responses'].update(l30_with_pwd_id='2', l31_shown_pwd_id='NA',
                                          l32_type_of_disability_in_pwd_id='NA')
        for field, diagnosis, _ in DISABILITY_TYPES:
            member['health_responses'][field] = '2'
            member['health_responses'][diagnosis] = 'NA'


class SpiscGuidanceTests(unittest.TestCase):
    def setUp(self):
        self.source = screening_source()
        disability_fixture(self.source)
        self.template = deepcopy(self.source['members'][0])

    def roster(self, ages):
        template = self.template
        self.source['members'] = []
        for line, age in enumerate(ages, 1):
            member = deepcopy(template)
            member['line_number'] = line
            member['responses'].update(a07_age=age, a01_last_name=f'Member {line}')
            self.source['members'].append(member)

    def guidance(self, assessed=ASSESSMENT_DATE):
        return prepare_spisc_guidance(self.source, assessed)

    def test_requested_2022_age_boundaries_and_unknown_category(self):
        self.roster(['54', '55', '56', '57', '59', '60', None])
        before = deepcopy(self.source)
        guidance = self.guidance()
        self.assertEqual([m['category'] for m in guidance['age_summary']['members']], [
            'below_senior_age', 'possible_senior_age', 'reached_senior_age',
            'reached_senior_age', 'reached_senior_age', 'recorded_senior', 'unknown_age',
        ])
        self.assertEqual(guidance['found'][0]['minimum_age'], 59)
        self.assertEqual(guidance['found'][0]['maximum_age'], 60)
        self.assertEqual(guidance['age_summary']['relevant_member_line_numbers'], (2, 3, 4, 5, 6))
        self.assertTrue(guidance['has_unknown_age'])
        self.assertEqual(self.source, before)

    def test_future_batch_uses_selected_year_and_explicit_date(self):
        self.roster(['55', '56', '58', '60'])
        self.source['context']['survey_year'] = 2024
        guidance = self.guidance(date(2027, 10, 24))
        self.assertEqual([m['category'] for m in guidance['age_summary']['members']], [
            'below_senior_age', 'possible_senior_age', 'reached_senior_age', 'recorded_senior',
        ])
        self.assertEqual(guidance['recorded_members'][0]['cells'][0]['label'], 'Age in CBMS 2024')
        self.assertEqual(guidance['date_label'], 'Oct 24, 2027')
        self.source['context']['source_provenance'] = {'declared': {
            'age_reference_date': '2024-12-31', 'age_reference_date_verified': True,
        }}
        self.assertEqual(self.guidance(date(2027, 10, 24))['age_summary']['members'][1]['category'],
                         'below_senior_age')
        self.source['context']['source_provenance']['declared']['age_reference_date_verified'] = False
        self.assertEqual(self.guidance(date(2027, 10, 24))['age_summary']['members'][1]['category'],
                         'possible_senior_age')

    def test_new_year_boundaries_are_not_simple_year_difference(self):
        self.roster(['55'])
        for assessed, category in (
            (date(2026, 1, 1), 'below_senior_age'),
            (date(2026, 7, 1), 'possible_senior_age'),
            (date(2027, 12, 31), 'reached_senior_age'),
        ):
            with self.subTest(assessed=assessed):
                self.assertEqual(self.guidance(assessed)['age_summary']['members'][0]['category'], category)

    def test_household_reason_describes_only_present_age_categories(self):
        for ages, expected in (
            (['62'], 'One member was already 60 or older in CBMS 2022.'),
            (['56'], 'One member has reached age 60 since CBMS 2022.'),
            (['55'], 'One member may have reached age 60 and needs current-age confirmation.'),
            (['62', '65'], '2 members were already 60 or older in CBMS 2022.'),
            (['62', '56', '55'], 'One member was already 60 or older in CBMS 2022; '
             'one member has reached age 60 since CBMS 2022; '
             'one member may have reached age 60 and needs current-age confirmation.'),
        ):
            with self.subTest(ages=ages):
                self.roster(ages)
                self.assertEqual(self.guidance()['why'], expected)
        self.source['context']['survey_year'] = 2024
        self.roster(['62'])
        self.assertIn('CBMS 2024', self.guidance(date(2027, 10, 24))['why'])

    def test_verification_questions_fit_single_multiple_and_potential_members(self):
        self.roster(['62'])
        self.assertEqual(self.guidance()['questions'][0],
                         "What is the senior's current age and date of birth?")
        self.roster(['62', '56'])
        questions = self.guidance()['questions']
        self.assertEqual(questions[0],
                         'What are the current ages and dates of birth of the listed seniors?')
        self.assertIn('Are any of the listed seniors', questions[1])
        self.assertIn('Do any of the listed seniors', questions[2])
        self.assertIn('Do the listed seniors', questions[3])
        self.roster(['62', '55'])
        self.assertIn('listed members', self.guidance()['questions'][0])
        self.roster(['62', None])
        self.assertIn('listed members', self.guidance()['questions'][0])

    def test_roster_identity_and_nested_answer_adaptation(self):
        self.roster(['55', '60'])
        self.source['members'][0]['line_number'] = '9'
        self.source['members'][1]['line_number'] = '2'
        self.source['members'][0]['responses'].update(
            a01_first_name='', a01_middle_name='', a01_last_name='',
        )
        guidance = self.guidance()
        self.assertEqual(guidance['found'][0]['subject'], 'Member line 9')
        self.assertEqual(guidance['found'][1]['subject'], 'Synthetic Member 2')
        self.assertEqual(guidance['age_summary']['relevant_member_line_numbers'], ('9', '2'))
        for member in guidance['age_summary']['members']:
            self.assertEqual(member['uuid'], self.source['findings']['household_uuid'])

    def test_member_disability_requires_join_and_household_corroboration(self):
        for age in ('55', '57', '60'):
            for household, member, joined, supported in (
                ('1', '1', True, True), ('2', '1', True, False),
                ('NA', '1', True, False), ('1', '1', False, False),
                ('1', 'NA', True, False), ('1', '2', True, False),
            ):
                with self.subTest(age=age, household=household, member=member, joined=joined):
                    self.roster([age])
                    self.source['household']['l15_with_pwd_member'] = household
                    self.source['members'][0]['health_present'] = joined
                    self.source['members'][0]['health_responses']['l16_pwd'] = member
                    guidance = self.guidance()
                    detail = guidance['found'][0]['disability']
                    self.assertEqual(detail['disability_supported'], supported)
                    if not joined:
                        self.assertIn('unavailable', str(detail['notes']))
                    self.assertTrue(guidance['review_suggested'])

    def test_household_disability_does_not_identify_member_or_trigger_review(self):
        self.roster(['30', None])
        self.source['members'][0]['health_responses']['l16_pwd'] = '1'
        guidance = self.guidance()
        self.assertEqual(guidance['found'][0]['category'], 'unknown_age')
        self.assertFalse(guidance['review_suggested'])
        self.assertNotIn('possible disability circumstance', str(guidance['found']))

    def test_empty_invalid_ages_and_future_dated_batch_remain_unresolved(self):
        for ages in ([], ['NA', '-1', '59.5', 'invalid']):
            self.roster(ages)
            guidance = self.guidance()
            self.assertTrue(guidance['has_unknown_age'])
            self.assertFalse(guidance['review_suggested'])
            self.assertTrue(all(m['category'] == 'unknown_age' for m in guidance['found']))
        self.roster(['60'])
        self.source['context']['survey_year'] = 2030
        guidance = self.guidance()
        self.assertIsNone(guidance['age_summary'])
        self.assertIn('precedes the CBMS age reference', guidance['why'])
        self.assertFalse(guidance['review_suggested'])

    def test_date_is_captured_once_in_philippine_time(self):
        with patch('services.spisc_screening_presentation.datetime') as clock:
            clock.now.return_value = datetime(2026, 10, 24, 0, 5, tzinfo=ZoneInfo('Asia/Manila'))
            self.assertEqual(philippine_assessment_date(), ASSESSMENT_DATE)
            clock.now.assert_called_once_with(ZoneInfo('Asia/Manila'))
        with patch('services.subsidy_screening_details.philippine_assessment_date',
                   return_value=ASSESSMENT_DATE) as capture:
            programs = prepare_subsidy_screening(self.source, build_evidence_index(self.source))
            capture.assert_called_once_with()
            self.assertEqual(programs[2]['guidance']['assessment_date'], ASSESSMENT_DATE)

    def test_projection_keeps_batch_household_and_member_line_join(self):
        connection = MagicMock()
        cursor = connection.cursor.return_value.__enter__.return_value
        assessment_evidence.list_member_sources(connection, 9, 'synthetic-household')
        query, parameters = cursor.execute.call_args.args
        self.assertIn('l.batch_id = m.batch_id AND l.uuid = m.uuid', query)
        self.assertIn('l.section_l_line_number = m.line_number', query)
        self.assertIn('m.batch_id = %s AND m.uuid = %s', query)
        self.assertEqual(parameters[2:], (9, 'synthetic-household'))
        self.assertTrue(set(MEMBER_LABELS) <= set(parameters[1]))
        self.assertTrue(set(HOUSEHOLD_LABELS) <= set(assessment_evidence.HOUSEHOLD_FIELDS))

    def test_stored_findings_priority_and_other_programs_are_preserved(self):
        self.roster(['57'])
        for status in ('For Review', 'Needs Data Review', 'No Historical CBMS Trigger'):
            self.source['findings']['programs']['SPISC']['screening_status'] = status
            before = deepcopy(self.source)
            with patch('services.subsidy_screening_details.philippine_assessment_date',
                       return_value=ASSESSMENT_DATE), \
                    patch('services.subsidy_screening.screen_spisc', side_effect=AssertionError('No engine')), \
                    patch('psycopg.connect', side_effect=AssertionError('No database')):
                detail = prepare_assessment_detail(self.source)
            self.assertEqual(detail['screening_programs'][2]['status'], status)
            self.assertEqual(detail['programs'][2]['status'], status)
            self.assertEqual(self.source, before)
            self.assertEqual(detail['mpi'], before['findings']['mpi'])
            other_programs = [p for p in detail['screening_programs'] if p['code'] != 'SPISC']
            changed = deepcopy(self.source)
            changed['members'][0]['responses']['a07_age'] = '30'
            # Priority derives from stored statuses, never dynamic age guidance.
            with patch('services.subsidy_screening_details.philippine_assessment_date',
                       return_value=ASSESSMENT_DATE):
                negative = prepare_assessment_detail(changed)
            self.assertEqual(detail['priority_display'], negative['priority_display'])
            self.assertEqual([p['status'] for p in other_programs], [
                p['status'] for p in negative['screening_programs'] if p['code'] != 'SPISC'
            ])

    def test_recorded_information_includes_new_members_and_missing_join_is_not_no(self):
        self.roster(['57', '55', '30'])
        member = self.source['members'][0]
        member['health_present'] = False
        member['health_responses']['l16_pwd'] = '1'  # Unjoined data is not evidence.
        programs = prepare_subsidy_screening(
            self.source, build_evidence_index(self.source), assessment_date=ASSESSMENT_DATE,
        )
        rows = programs[2]['members']
        self.assertEqual([m['name'] for m in rows], [f'Synthetic Member {i}' for i in (1, 2, 3)])
        self.assertEqual(rows[0]['cells'][0]['value'], '57 years')
        self.assertEqual(rows[0]['cells'][1]['value'], 'Not recorded')
        self.assertTrue(any(c['label'] == 'PWD ID reported' for c in rows[1]['cells']))


class SpiscDisabilityTests(unittest.TestCase):
    def setUp(self):
        self.source = screening_source()
        disability_fixture(self.source)
        self.member = self.source['members'][2]
        self.answers = self.member['health_responses']
        self.answers['l16_pwd'] = '1'

    def present(self):
        return prepare_disability_information(self.source, build_evidence_index(self.source))

    def test_multiple_types_keep_diagnosis_answers_paired(self):
        first, second = DISABILITY_TYPES[0], DISABILITY_TYPES[5]
        self.answers.update({first[0]: '1', first[1]: '1', second[0]: '1', second[1]: '2'})
        before = deepcopy(self.source)
        result = self.present()['members']['3']
        cells = {cell['label']: cell['value'] for cell in result['cells']}
        self.assertIn('Visual disability', cells['Reported disability'])
        self.assertIn('Physical disability (orthopedic)', cells['Reported disability'])
        self.assertEqual(cells["Doctor's diagnosis reported"],
                         'Visual disability: Yes; Physical disability (orthopedic): No')
        self.assertFalse(result['notes'])
        self.assertEqual(self.source, before)

    def test_member_and_household_id_corroboration_then_shown_id_type(self):
        self.source['household']['l29_with_member_who_has_pwd_id'] = '1'
        self.answers.update(l30_with_pwd_id='1', l31_shown_pwd_id='1',
                            l32_type_of_disability_in_pwd_id='6')
        cells = {cell['label']: cell['value'] for cell in self.present()['members']['3']['cells']}
        self.assertEqual(cells['PWD ID reported'], 'Yes')
        self.assertEqual(cells['PWD ID reportedly shown at interview'], 'Yes')
        self.assertEqual(cells['Disability type recorded from the PWD ID'], 'Physical disability (orthopedic)')
        self.answers['l31_shown_pwd_id'] = '2'
        result = self.present()['members']['3']
        self.assertNotIn('Disability type recorded from the PWD ID',
                         [cell['label'] for cell in result['cells']])
        self.assertTrue(any(cell['needs_verification'] for cell in result['recorded_cells']
                            if cell['label'] == 'Disability type recorded from the PWD ID'))

    def test_types_do_not_leak_to_another_member_or_an_absent_join(self):
        field, diagnosis, label = DISABILITY_TYPES[5]
        self.answers.update({field: '1', diagnosis: '1'})
        result = self.present()
        self.assertNotIn(label, str(result['members']['1']['cells']))
        self.member['health_present'] = False
        result = self.present()
        self.assertFalse(result['members']['3']['cells'])
        self.assertIn('not reliably identified', ' '.join(result['notes']))

    def test_no_answers_and_expected_skips_are_not_highlighted(self):
        self.source['household'].update(l15_with_pwd_member='2', l29_with_member_who_has_pwd_id='2')
        self.answers.update(l16_pwd='2', l30_with_pwd_id='2')
        result = self.present()['members']['3']
        self.assertFalse(any(cell['needs_verification'] for cell in result['recorded_cells']))
        self.answers.update(l16_pwd='NA', l30_with_pwd_id='NA')
        result = self.present()['members']['3']
        self.assertFalse(any(cell['needs_verification'] for cell in result['recorded_cells']))
        self.assertTrue(all('Not asked' in cell['value'] for cell in result['recorded_cells']))

    def test_explicit_member_no_is_distinct_from_an_absent_section_l_record(self):
        self.answers['l16_pwd'] = '2'
        self.assertIn('No disability reported for this member.',
                      self.present()['members']['3']['notes'])
        self.member['health_present'] = False
        notes = self.present()['members']['3']['notes']
        self.assertNotIn('No disability reported for this member.', notes)
        self.assertIn('unavailable', ' '.join(notes))

    def test_household_no_hides_only_unrecorded_member_pwd_id_answers(self):
        self.source['household']['l29_with_member_who_has_pwd_id'] = '2'
        for answer in (None, '', 'NA', '1', '2'):
            with self.subTest(answer=answer):
                self.answers['l30_with_pwd_id'] = answer
                before = deepcopy(self.source)
                result = self.present()
                rows = result['members']['3']['recorded_cells']
                pwd_rows = [cell for cell in rows if cell['label'] == 'PWD ID reported']
                self.assertEqual(len(pwd_rows), int(answer in ('1', '2')))
                self.assertEqual(result['household_cells'][1]['value'], 'No')
                self.assertEqual(self.source, before)

        self.source['household']['l29_with_member_who_has_pwd_id'] = '1'
        self.answers['l30_with_pwd_id'] = 'NA'
        rows = self.present()['members']['3']['recorded_cells']
        pwd_row = next(cell for cell in rows if cell['label'] == 'PWD ID reported')
        self.assertEqual(pwd_row['value'], 'Not recorded')
        self.assertTrue(pwd_row['needs_verification'])

    def test_missing_applicable_diagnosis_is_not_no_and_is_highlighted(self):
        field, _, label = DISABILITY_TYPES[0]
        self.answers[field] = '1'
        result = self.present()['members']['3']
        self.assertIn(f'{label}: Not recorded', str(result['cells']))
        self.assertTrue(any(cell['needs_verification'] for cell in result['recorded_cells']
                            if "Doctor's diagnosis reported" in cell['label']))

    def test_conflicting_parent_or_reference_never_attributes_a_positive(self):
        field, diagnosis, label = DISABILITY_TYPES[5]
        self.answers.update({field: '1', diagnosis: '1'})
        self.source['household']['l15_with_pwd_member'] = '2'
        result = self.present()['members']['3']
        self.assertFalse(result['disability_supported'])
        self.assertNotIn(label, str(result['cells']))
        self.assertTrue(any(cell['needs_verification'] for cell in result['recorded_cells']))
        self.source['household']['l15_with_pwd_member'] = '1'
        self.source['references']['codebook'].append(
            {'variable_name': 'l16_pwd', 'code': '1', 'label': 'No'},
        )
        self.assertFalse(self.present()['members']['3']['disability_supported'])


class SpiscPresentationTests(unittest.TestCase):
    session = screening_tests.ScreeningRouteTests.session

    def setUp(self):
        screening_tests.ScreeningRouteTests.setUp(self)
        disability_fixture(self.source)

    def render(self):
        self.session()
        with patch('services.subsidy_screening_details.philippine_assessment_date',
                   return_value=ASSESSMENT_DATE):
            response = self.client.get(self.path + '?tab=screening&page=2&priority=secondary')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        return re.search(r'<article[^>]+id="screening-spisc".*?</article>', response.text, re.S)[0]

    def set_age(self, age, status='No Historical CBMS Trigger'):
        self.source['members'] = [self.source['members'][0]]
        self.source['members'][0]['responses']['a07_age'] = age
        self.source['findings']['programs']['SPISC'].update(
            screening_status=status, supporting_evidence=(), data_quality_warnings=(),
        )

    def test_progressed_age_and_saved_negative_are_visibly_separate(self):
        self.set_age('57')
        article = self.render()
        for text in ('Has reached senior age',
                     'Screening based on CBMS 2022: No Review Signal.', '61–62',
                     'Oct 24, 2026',
                     'A brief reason is required on the review initiation screen.'):
            self.assertIn(text, article)
        self.assertIn('>For Review</span>', article)
        main = article.split('<details class="screening-evidence">')[0]
        self.assertNotIn('Screening based on CBMS', main)
        self.assertNotIn('Current-age guidance', article)
        self.assertNotIn('Estimated ages require confirmation.', article)
        self.assertEqual(main.count('screening-status--review'), 1)
        self.assertIn('<details class="spisc-disclosure" open>', article)
        self.assertIn('<details class="screening-evidence">', article)

    def test_all_categories_and_disclosure_defaults(self):
        for age, label, opened in (
            ('60', 'Recorded as a senior', True),
            ('57', 'Has reached senior age', True),
            ('55', 'May have reached senior age', True),
            ('30', 'No senior-age review signal', False),
            ('NA', 'Age is missing or unclear', True),
        ):
            with self.subTest(age=age):
                self.set_age(age)
                article = self.render()
                self.assertIn(label, article)
                self.assertEqual(' open' in re.search(r'<details class="spisc-disclosure"[^>]*>', article)[0], opened)

    def test_questions_hierarchy_and_jinja_escaping(self):
        self.set_age('60', 'For Review')
        self.source['members'][0]['responses']['a01_first_name'] = '<script>alert(1)</script>'
        article = self.render()
        self.assertIn('&lt;script&gt;alert(1)&lt;/script&gt;', article)
        self.assertNotIn('<script>alert(1)</script>', article)
        questions = re.search(r'<ol>(.*?)</ol>', article, re.S)[1]
        self.assertEqual(questions.count('<li>'), 5)
        for text in ('date of birth', 'another pension', 'financial support', 'mobility', 'waiting list'):
            self.assertIn(text, questions)
        headings = ('What the system found', 'Why this was flagged',
                    'Information to check', 'Suggested staff action', 'View Recorded Information')
        self.assertEqual(sorted(headings, key=article.index), list(headings))
        self.assertNotIn('A brief reason is required', article)

    def test_review_buttons_and_navigation_preserve_review_restrictions(self):
        self.set_age('57')
        for state, withdrawn, label in (
            ('In Review', False, 'Open'), ('Closed', False, 'View'),
            ('Closed', True, 'Start'), ('Cancelled', False, 'Start'),
        ):
            self.source['staff_reviews'] = [{
                'id': 7, 'current_review_id': 7, 'program': 'SPISC',
                'status': state, 'withdrawn': withdrawn,
            }]
            article = self.render()
            link = re.search(r'href="([^"]+)"[^>]*>' + label + r' SPISC Review</a>', article)[1]
            parsed = urlsplit(unescape(link))
            expected = self.path + '/reviews/SPISC' + ('/7' if label != 'Start' else '')
            self.assertEqual(parsed.path, expected)
            self.assertEqual(parse_qs(parsed.query)['priority'], ['secondary'])
            self.assertEqual(parse_qs(parsed.query)['page'], ['2'])
            self.assertEqual(article.count('SPISC Review</a>'), 1)
            if label != 'Start':
                self.assertNotIn('Start SPISC Review</a>', article)
        self.source['staff_reviews'].append({
            'id': 8, 'current_review_id': 8, 'program': 'SPISC', 'status': 'In Review',
        })
        self.assertIn('Open SPISC Review', self.render())

    def test_saved_needs_data_review_still_requires_reason(self):
        self.set_age('57', 'Needs Data Review')
        article = self.render()
        self.assertIn('Screening based on CBMS 2022: Not Enough Information.', article)
        self.assertIn('A brief reason is required', article)

    def test_multiple_members_disability_grid_and_decoded_labels_are_escaped(self):
        self.source['members'] = self.source['members'][:2]
        for member in self.source['members']:
            member['responses']['a07_age'] = '62'
            member['health_responses'].update(l16_pwd='1', l17_a_visual_disability='1',
                                              l18_a_visual_disability_diagnosed='1')
        self.source['household']['l29_with_member_who_has_pwd_id'] = '1'
        self.source['members'][0]['health_responses'].update(
            l30_with_pwd_id='1', l31_shown_pwd_id='1', l32_type_of_disability_in_pwd_id='6',
        )
        for row in self.source['references']['codebook']:
            if row['variable_name'] == 'l32_type_of_disability_in_pwd_id' and row['code'] == '6':
                row['label'] = '<b>Physical</b>'
        article = self.render()
        self.assertEqual(article.count('<dl class="spisc-ages">'), 2)
        self.assertIn('66–67', article)
        self.assertIn('&lt;b&gt;Physical&lt;/b&gt;', article)
        self.assertNotIn('<b>Physical</b>', article)
        self.assertIn('class="spisc-recorded-grid"', article)

    def test_skipped_member_pwd_id_rows_are_hidden_without_hiding_members(self):
        self.source['household']['l29_with_member_who_has_pwd_id'] = '2'
        for member in self.source['members']:
            member['health_responses']['l30_with_pwd_id'] = 'NA'
        before = deepcopy(self.source)
        evidence = self.render().split('<details class="screening-evidence">')[1]
        self.assertIn('Household reported a member with a PWD ID', evidence)
        self.assertIn('No', evidence)
        self.assertNotIn('PWD ID reported', evidence)
        for number in (1, 2, 3):
            self.assertIn(f'Synthetic Member {number}', evidence)
        self.assertEqual(self.source, before)

    def test_all_roster_members_and_younger_members_available_answers_are_retained(self):
        self.source['context']['survey_year'] = 2024
        self.source['household']['l29_with_member_who_has_pwd_id'] = '1'
        self.source['members'][1]['health_responses'].update(
            l30_with_pwd_id='1', l31_shown_pwd_id='2',
        )
        before = deepcopy(self.source)
        article = self.render()
        main, evidence = article.split('<details class="screening-evidence">')
        self.assertNotIn('Synthetic Member 2', main)
        for number in (1, 2, 3):
            self.assertIn(f'Synthetic Member {number}', evidence)
        self.assertIn('Household answers', evidence)
        self.assertIn('Screening based on CBMS 2024: For Review.', evidence)
        guidance = prepare_spisc_guidance(self.source, ASSESSMENT_DATE)
        younger = guidance['recorded_members'][1]
        values = {cell['label']: cell for cell in younger['cells']}
        self.assertEqual(values['Age in CBMS 2024']['value'], '12 years')
        self.assertEqual(values['PWD ID reported']['value'], 'Yes')
        self.assertEqual(values['PWD ID reportedly shown at interview']['value'], 'No')
        self.assertFalse(values['PWD ID reportedly shown at interview']['needs_verification'])
        self.assertEqual(self.source, before)


if __name__ == '__main__':
    unittest.main()
