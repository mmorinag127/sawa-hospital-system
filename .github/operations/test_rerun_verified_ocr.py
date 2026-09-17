import unittest
from copy import deepcopy
from rerun_verified_ocr import validate_case


class SelectionTests(unittest.TestCase):
    def setUp(self):
        self.case = dict(identity_verified=True, order_id='ORDabc', facility_id='FAC1', document_id='DOC1', pdf_uri='gs://original', week_start='2026-09-27', week_end='2026-09-30', expected_job_updated_at='2026-09-17T00:00:00')
        self.order = dict(id='ORDabc', facility='FAC1', document_id='DOC1', document='gs://original', status='要確認')
        self.workflow = dict(facility_id='FAC1', week_start='2026-09-27', week_end='2026-09-30', state='ocr_completed', ocr_job={'updated_at':'2026-09-17T00:00:00'})

    def test_new_runner_cannot_replay_manifest_after_job_update(self):
        for state in ['ocr_completed', 'ocr_failed', 'ocr_running']:
            changed={**self.workflow, 'state':state, 'ocr_job':{'updated_at':'2026-09-17T01:00:00'}}
            with self.subTest(state=state), self.assertRaises(ValueError):
                validate_case(self.case, self.order, changed, True)

    def test_verified_case(self):
        validate_case(self.case, self.order, self.workflow, False)

    def test_unverified(self):
        self.case['identity_verified'] = False
        with self.assertRaises(ValueError):
            validate_case(self.case, self.order, self.workflow, False)

    def test_document_and_facility_changes(self):
        for key in ['id', 'facility', 'document_id', 'document']:
            order = deepcopy(self.order)
            order[key] = 'changed'
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_case(self.case, order, self.workflow, False)

    def test_period_mismatch(self):
        for key in ['week_start', 'week_end', 'facility_id']:
            workflow = deepcopy(self.workflow)
            workflow[key] = 'changed'
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_case(self.case, self.order, workflow, False)

    def test_saved_or_confirmed_requires_approval(self):
        for key in ['saved_sheet_id', 'confirmed_snapshot_id']:
            workflow = {**self.workflow, key: 'saved'}
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_case(self.case, self.order, workflow, False)
            validate_case(self.case, self.order, workflow, True)
        self.order['status'] = '確定'
        with self.assertRaises(ValueError):
            validate_case(self.case, self.order, self.workflow, False)

    def test_active_and_blocked(self):
        for patch in [{'state':'ocr_running'}, {'blockers':['menu_missing']}]:
            with self.subTest(patch=patch), self.assertRaises(ValueError):
                validate_case(self.case, self.order, {**self.workflow, **patch}, True)
        self.order['is_archived'] = True
        with self.assertRaises(ValueError):
            validate_case(self.case, self.order, self.workflow, True)


if __name__ == '__main__':
    unittest.main()
