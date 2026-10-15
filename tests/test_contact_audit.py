import copy
import unittest
from phigros_chart_mcp.contact_audit import audit_contacts


def note(ident, kind, time, end=None, finger='L_index', gesture=None):
    annotation = {'finger_key': finger} if finger else {}
    if gesture:
        annotation['gesture'] = gesture
    return dict(id=ident, kind=kind, time=str(time), end=str(time if end is None else end),
                x=0, annotation=annotation)


def model(*notes):
    return {'offset_ms': 125, 'lines': [{'id': 'main', 'notes': list(notes)}]}


class ContactAudit(unittest.TestCase):
    def test_hold_ownership_conflict_and_exact_release(self):
        data = model(note('held', 'hold', 1, 3), note('conflict', 'tap', 2), note('released', 'tap', 3))
        before = copy.deepcopy(data)
        result = audit_contacts(data)
        self.assertEqual(result['finding_counts'], {'assigned_finger_collision': 1})
        self.assertEqual(result['findings'][0]['note_id'], 'conflict')
        self.assertEqual(result['findings'][0]['audio_seconds'], 2.125)
        self.assertEqual(data, before)

    def test_shared_raw_objects_are_not_independent_contacts(self):
        result = audit_contacts(model(note('a', 'tap', 1, gesture='shared'),
                                      note('b', 'drag', 1, gesture='shared')))
        self.assertEqual(result['peak_declared_contacts'], 1)
        self.assertEqual(result['findings'], [])
        self.assertTrue(any('assumed sharing' in s for s in result['limitations']))

    def test_grouped_drag_owns_contact_between_targets(self):
        result = audit_contacts(model(note('a', 'drag', 1, gesture='path'),
                                      note('b', 'drag', 3, gesture='path'), note('inside', 'tap', 2)))
        self.assertEqual(result['finding_counts']['assigned_finger_collision'], 1)
        self.assertEqual(result['findings'][0]['note_id'], 'inside')

    def test_four_contacts_and_unlabelled_objects(self):
        result = audit_contacts(model(note('a', 'hold', 1, 4, 'L_middle'),
                                      note('b', 'hold', 1, 4, 'R_middle'),
                                      note('c', 'tap', 2, finger='L_index'),
                                      note('d', 'tap', 2, finger='R_index'),
                                      note('unassigned', 'drag', 2, finger=None)))
        self.assertEqual(result['peak_declared_contacts'], 4)
        self.assertEqual(result['unassigned_heads'], 1)
        self.assertEqual(result['findings'], [])

    def test_rapid_repeat_bounded_report_and_travel(self):
        notes = [note(str(i), 'tap', f'{i}/20') for i in range(10)]
        notes[1]['x'] = .25
        result = audit_contacts(model(*notes), max_witnesses=2)
        self.assertEqual(result['finding_counts']['rapid_assigned_repeat'], 9)
        self.assertEqual(len(result['findings']), 2)
        self.assertTrue(result['findings_truncated'])
        self.assertEqual(result['fastest_same_finger_strikes'][0]['gap_ms'], 50)
        self.assertEqual(result['fastest_same_finger_strikes'][0]['local_x_delta'], .25)


if __name__ == '__main__':
    unittest.main()
