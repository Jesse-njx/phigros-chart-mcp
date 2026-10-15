"""Independent public-backend witnesses, computed without backend helpers."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from fractions import Fraction

from phigros_chart_mcp.core import Engine, CompiledTracks


class IndependentReview(unittest.TestCase):
    def setUp(self):
        self.e = Engine()
        self.created = self.e.chart_create({'title': 'Independent witness'}, bpm=120)

    def plan(self, operations, chart=None):
        c = chart or self.created
        return self.e.authoring_plan(c['chart_ref'], c['digest'], operations)

    def apply(self, p, chart=None):
        return self.e.authoring_apply(p['plan_ref'], (chart or self.created)['digest'])

    def read(self, c, **kw):
        return self.e.chart_read(c['chart_ref'], c['digest'], **kw)

    def test_tempo_crossing_hold_and_offset(self):
        c = self.e.chart_create({'title': 'Tempo crossing'}, offset_ms=125,
                               tempo_map=[{'beat': 0, 'bpm': 120}, {'beat': 2, 'bpm': 240}])
        p = self.plan([{'type': 'insert_notes', 'line_id': 'main', 'notes': [
            {'id': 'held', 'kind': 'hold', 'beat': 1, 'duration': 2, 'x': .1}]}], c)
        n = self.read(self.apply(p, c))['notes'][0]
        self.assertEqual(Fraction(n['time']), Fraction(1, 2))
        self.assertEqual(Fraction(n['end']) - Fraction(n['time']), Fraction(3, 4))
        self.assertEqual(n['audio_seconds'], .625)
        self.assertEqual(Fraction(n['duration']), 2)

    def test_overlapping_hold_is_in_bounded_read(self):
        p = self.plan([{'type': 'insert_notes', 'line_id': 'main', 'notes': [
            {'id': 'held', 'kind': 'hold', 'beat': 1, 'duration': 4, 'x': -.2},
            {'id': 'far', 'kind': 'tap', 'beat': 100, 'x': .2}]}])
        c = self.apply(p)
        r = self.read(c, start='3', end='4')
        self.assertEqual([n['id'] for n in r['notes']], ['held'])
        self.assertNotIn('far', json.dumps(r), 'Bounded read leaked unrelated notes via lines')

    def test_context_cannot_mutate_stored_revision(self):
        before = self.e.chart_context(self.created['chart_ref'])
        self.e.chart_context(self.created['chart_ref'])['metadata']['title'] = 'tampered'
        self.e.chart_context(self.created['chart_ref'])['tempo_map'][0]['bpm'] = 999
        after = self.e.chart_context(self.created['chart_ref'])
        self.assertEqual(after['metadata']['title'], 'Independent witness')
        self.assertEqual(after['tempo_map'][0]['bpm'], 120)
        self.assertEqual(after['digest'], self.created['digest'])

    def test_read_cannot_mutate_tempo(self):
        r = self.read(self.created)
        r['tempo_map'][0]['bpm'] = 999
        self.assertEqual(self.e.chart_context(self.created['chart_ref'])['tempo_map'][0]['bpm'], 120)

    def test_plan_detached_and_atomic_stale_guard(self):
        ops = [{'type': 'insert_notes', 'line_id': 'main', 'notes': [
            {'id': 'a', 'kind': 'tap', 'beat': 2, 'x': .2}]}]
        p = self.plan(ops)
        stale = self.plan([{'type': 'insert_notes', 'line_id': 'main', 'notes': [
            {'id': 'b', 'kind': 'tap', 'beat': 3, 'x': -.2}]}])
        ops[0]['notes'][0]['x'] = 777
        c = self.apply(p)
        self.assertEqual(self.read(c)['notes'][0]['x'], .2)
        with self.assertRaises(ValueError): self.apply(stale)
        with self.assertRaises(ValueError): self.apply(p)
        self.assertEqual([n['id'] for n in self.read(c)['notes']], ['a'])

    def test_sequential_two_bar_stair_transforms(self):
        p = self.plan([
            {'type': 'insert_pattern', 'line_id': 'main', 'pattern': 'stairs', 'start': 32,
             'count': 32, 'step': [0, 1, 4], 'lane_x': [-.3, -.1, .1, .3], 'order': [0, 1, 2, 3]},
            {'type': 'transform_notes', 'line_id': 'main', 'start': 36, 'end': 40, 'mirror': True},
            {'type': 'transform_notes', 'line_id': 'main', 'start': [39, 3, 4], 'end': 40, 'kind': 'flick'}])
        ns = sorted(self.read(self.apply(p))['notes'], key=lambda n: Fraction(n['beat']))
        self.assertEqual(len(ns), 32)
        self.assertEqual(Fraction(ns[-1]['beat']), Fraction(159, 4))
        self.assertEqual(sum(n['kind'] == 'flick' for n in ns), 1)
        self.assertEqual([n['x'] for n in ns[:4]], [-.3, -.1, .1, .3])
        self.assertEqual([n['x'] for n in ns[16:20]], [.3, .1, -.1, -.3])

    def test_invalid_edit_does_not_mutate_chart(self):
        for note in [
            {'kind': 'hold', 'beat': 1, 'duration': 0, 'x': 0},
            {'kind': 'tap', 'beat': [0, 1, 0], 'x': 0},
            {'kind': 'tap', 'beat': 1, 'x': float('nan')},
            {'kind': 'tap', 'beat': 1, 'x': 0, 'unknown': True}]:
            with self.subTest(note=note), self.assertRaises(ValueError):
                self.plan([{'type': 'insert_notes', 'line_id': 'main', 'notes': [note]}])
        self.assertEqual(self.read(self.created)['notes'], [])
        self.assertEqual(self.e.chart_context(self.created['chart_ref'])['digest'], self.created['digest'])

    def test_decorative_line_cannot_score(self):
        with self.assertRaises(ValueError):
            self.plan([{'type': 'create_line', 'id': 'ornament', 'name': 'ornament', 'role': 'decorative'},
                       {'type': 'insert_notes', 'line_id': 'ornament', 'notes': [
                           {'kind': 'tap', 'beat': 1, 'x': 0}]}])
        self.assertEqual(self.e.chart_context(self.created['chart_ref'])['lines'][0]['note_count'], 0)

    def test_mixed_source_bpm_same_audio_target_and_no_write(self):
        def line(bpm, raw):
            return {'bpm': bpm, 'notesAbove': [{'type': 1, 'time': raw, 'holdTime': 0,
                    'positionX': 0, 'speed': 1, 'floorPosition': 2}], 'notesBelow': [],
                    'speedEvents': [{'startTime': 0, 'endTime': 10000, 'value': 2, 'floorPosition': 0}],
                    'judgeLineMoveEvents': [], 'judgeLineRotateEvents': [], 'judgeLineDisappearEvents': []}
        source = {'formatVersion': 3, 'offset': .125, 'judgeLineList': [line(120, 64), line(240, 128)]}
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'source.json'
            path.write_text(json.dumps(source))
            original = path.read_bytes()
            c = self.e.chart_import(str(path))
            ns = self.read(c)['notes']
            self.assertEqual([n['audio_seconds'] for n in ns], [1.125, 1.125])
            self.assertEqual([Fraction(n['beat']) for n in ns], [2, 2])
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(sorted(p.name for p in Path(td).iterdir()), ['source.json'])

    def test_speed_boundary_floor_and_hold_encoding(self):
        c = self.e.chart_create({'title': 'Speed boundary'}, bpm=120, main_line={'speed': 2})
        p = self.plan([
            {'type': 'set_effect', 'line_id': 'main', 'channel': 'speed', 'start': 2, 'end': 8, 'a': 4},
            {'type': 'insert_notes', 'line_id': 'main', 'notes': [
                {'id': 'h', 'kind': 'hold', 'beat': 2, 'duration': 1, 'x': -.1, 'speed': 1.5},
                {'id': 't', 'kind': 'tap', 'beat': 3, 'x': .1, 'speed': 1.5}]}], c)
        c = self.apply(p, c)
        official = self.e.chart_export(c['chart_ref'], c['digest'])['chart']
        ns = official['judgeLineList'][0]['notesAbove']
        self.assertEqual(ns[0]['time'], 64)
        self.assertEqual(ns[0]['floorPosition'], 2)
        self.assertEqual(ns[0]['holdTime'], 32)
        self.assertEqual(ns[0]['speed'], 6)
        self.assertEqual(ns[1]['floorPosition'], 4)
        self.assertEqual(ns[1]['speed'], 1.5)

    def test_effect_replacement_restores_original_suffix(self):
        p = self.plan([{'type': 'set_effect', 'line_id': 'main', 'channel': 'rotation',
                       'start': 0, 'end': 8, 'a': 0, 'b': 80, 'replace': True}])
        c = self.apply(p)
        replacement = self.plan([{'type': 'set_effect', 'line_id': 'main', 'channel': 'rotation',
                                 'start': 2, 'end': 4, 'a': 20, 'b': 60, 'replace': True}], c)
        c = self.apply(replacement, c)
        rows = self.e.chart_export(c['chart_ref'], c['digest'])['chart']['judgeLineList'][0]['judgeLineRotateEvents']
        def sample(raw):
            row = next(e for e in rows if e['startTime'] <= raw < e['endTime'])
            return row['start'] + (row['end'] - row['start']) * (raw - row['startTime']) / (row['endTime'] - row['startTime'])
        self.assertEqual(sample(32), 10)  # beat1, source ramp
        self.assertEqual(sample(96), 40)  # beat3, replacement
        self.assertEqual(sample(128), 40) # beat4, restored source
        self.assertEqual(sample(192), 60) # beat6, source suffix

    def test_native_units_four_kinds_and_temporal_export(self):
        c = self.e.chart_create({'title': 'Native units'}, offset_ms=125, main_line={'opacity': .5, 'x': .1, 'y': -.1})
        p = self.plan([{'type': 'insert_notes', 'line_id': 'main', 'notes': [
            {'id': k, 'kind': k, 'beat': i + 1, 'x': .2, 'side': 'below', **({'duration': [0, 1, 2]} if k == 'hold' else {})}
            for i, k in enumerate(['tap', 'drag', 'hold', 'flick'])]}], c)
        c = self.apply(p, c)
        native = self.e.chart_export(c['chart_ref'], c['digest'], format='phichain_native')['chart']
        self.assertEqual(native['format'], 6)
        self.assertEqual(native['offset'], 125)
        self.assertEqual([n['kind'] for n in native['lines'][0]['notes']], ['tap', 'drag', 'hold', 'flick'])
        self.assertTrue(all(n['x'] == 270 and not n['above'] for n in native['lines'][0]['notes']))
        self.assertEqual(native['lines'][0]['notes'][2]['hold_beat'], [0, 1, 2])
        opacity = next(e for e in native['lines'][0]['events'] if e['kind'] == 'opacity')
        self.assertEqual(opacity['value']['value'], 127.5)

    def test_export_collision_and_stale_digest_leave_files_unchanged(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'chart.json'
            path.write_text('existing')
            with self.assertRaises(ValueError):
                self.e.chart_export(self.created['chart_ref'], self.created['digest'], output_path=str(path))
            self.assertEqual(path.read_text(), 'existing')
            self.assertEqual(list(Path(td).iterdir()), [path])
            with self.assertRaises(ValueError):
                self.e.chart_export(self.created['chart_ref'], '0' * 64, output_path=str(Path(td) / 'new.json'))
            self.assertEqual(list(Path(td).iterdir()), [path])

    def test_hidden_carrier_on_visible_anchor_is_not_missing_anchor(self):
        p = self.plan([
            {'type': 'create_line', 'id': 'carrier', 'name': 'carrier', 'role': 'note_carrier',
             'anchor_id': 'main', 'opacity': 0, 'x': .1, 'rotation': 0},
            {'type': 'create_line', 'id': 'frame', 'name': 'frame', 'role': 'decorative', 'rotation': 90},
            {'type': 'insert_notes', 'line_id': 'carrier', 'notes': [{'kind': 'tap', 'beat': 2, 'x': .1}]}])
        c = self.apply(p)
        check = self.e.authoring_check(c['chart_ref'], c['digest'])
        codes = {f['code'] for f in check['findings']}
        self.assertNotIn('anchor_count', codes)
        self.assertNotIn('anchor_invisible', codes)
        self.assertNotIn('carrier_misses_anchor', codes)
        self.assertEqual(check['raw_simultaneous_objects_peak'], 1)

    def test_legitimate_degenerate_source_event_loss_aware_roundtrip(self):
        source = {'formatVersion': 3, 'offset': 0, 'customExtension': {'keep': True}, 'judgeLineList': [{
            'bpm': 120, 'notesAbove': [], 'notesBelow': [],
            'speedEvents': [{'startTime': 0, 'endTime': 10000, 'value': 2, 'floorPosition': 0},
                            {'startTime': 2202, 'endTime': 2202, 'value': 2}],
            'judgeLineMoveEvents': [], 'judgeLineRotateEvents': [], 'judgeLineDisappearEvents': []}]}
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'degenerate.json'; path.write_text(json.dumps(source))
            c = self.e.chart_import(str(path))
            self.assertEqual(self.e.chart_export(c['chart_ref'], c['digest'])['chart'], source)
            self.assertEqual(json.loads(path.read_text()), source)

    def test_edit_elsewhere_preserves_nonzero_source_floor_base_or_refuses(self):
        def line(floor_base, notes):
            return {'bpm': 120, 'notesAbove': notes, 'notesBelow': [],
                    'speedEvents': [{'startTime': 0, 'endTime': 10000, 'value': 2, 'floorPosition': floor_base}],
                    'judgeLineMoveEvents': [], 'judgeLineRotateEvents': [], 'judgeLineDisappearEvents': []}
        source = {'formatVersion': 3, 'offset': 0, 'judgeLineList': [
            line(6, [{'type': 1, 'time': 32, 'positionX': .9, 'holdTime': 0, 'speed': 1, 'floorPosition': 7}]),
            line(0, [])]}
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'floor-base.json'; path.write_text(json.dumps(source))
            c = self.e.chart_import(str(path))
            p = self.plan([{'type': 'insert_notes', 'line_id': 'line-1', 'notes': [
                {'kind': 'tap', 'beat': 2, 'x': .1}]}], c)
            c = self.apply(p, c)
            try:
                exported = self.e.chart_export(c['chart_ref'], c['digest'])['chart']
            except ValueError as error:
                self.assertRegex(str(error).lower(), 'compatib|floor|unsupported|source')
            else:
                first = exported['judgeLineList'][0]
                self.assertEqual(first['speedEvents'][0]['floorPosition'], 6)
                self.assertEqual(first['notesAbove'][0]['floorPosition'], 7)
            self.assertEqual(json.loads(path.read_text()), source)

    def test_short_interior_hold_excursion_requires_review_finding(self):
        p = self.plan([
            {'type': 'create_line', 'id': 'held-carrier', 'name': 'held carrier',
             'role': 'note_carrier', 'anchor_id': 'main'},
            {'type': 'set_effect', 'line_id': 'held-carrier', 'channel': 'y',
             'start': 1, 'end': [1, 1, 2], 'a': 0, 'b': .1},
            {'type': 'set_effect', 'line_id': 'held-carrier', 'channel': 'y',
             'start': [1, 1, 2], 'end': 2, 'a': .1, 'b': 0},
            {'type': 'insert_notes', 'line_id': 'held-carrier', 'notes': [
                {'kind': 'hold', 'beat': 1, 'duration': 4, 'x': 0}]}])
        c = self.apply(p)
        findings = self.e.authoring_check(c['chart_ref'], c['digest'])['findings']
        self.assertTrue(any(f['line_id'] == 'held-carrier' and
                            ('hold' in f['code'] or 'anchor' in f['code']) for f in findings),
                        'Carrier departs the anchor inside hold despite coincident head/mid/tail')

    def test_persistent_sidecar_digest_rejects_tampering(self):
        p = self.plan([{'type': 'insert_notes', 'line_id': 'main', 'notes': [
            {'id': 'a', 'kind': 'tap', 'beat': 1, 'x': .1}]}])
        c = self.apply(p)
        sidecar = self.e.chart_export(c['chart_ref'], c['digest'])['sidecar']
        sidecar['model']['lines'][0]['notes'][0]['x'] = .4
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'tampered.authoring.json'; path.write_text(json.dumps(sidecar))
            with self.assertRaises(ValueError):
                self.e.chart_import(str(path))

    def test_missing_source_floor_fields_use_cumulative_speed(self):
        source = {'formatVersion': 3, 'offset': 0, 'judgeLineList': [{
            'bpm': 120, 'notesAbove': [], 'notesBelow': [],
            'speedEvents': [{'startTime': 0, 'endTime': 32, 'value': 2},
                            {'startTime': 32, 'endTime': 10000, 'value': 4}],
            'judgeLineMoveEvents': [], 'judgeLineRotateEvents': [], 'judgeLineDisappearEvents': []}]}
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'missing-floor.json'; path.write_text(json.dumps(source))
            c = self.e.chart_import(str(path))
            p = self.plan([{'type': 'insert_notes', 'line_id': 'line-0', 'notes': [
                {'kind': 'tap', 'beat': [1, 1, 2], 'x': .1}]}], c)
            c = self.apply(p, c)
            try:
                ns = self.e.chart_export(c['chart_ref'], c['digest'])['chart']['judgeLineList'][0]['notesAbove']
            except ValueError as error:
                self.assertRegex(str(error).lower(), 'compatib|floor|unsupported|source')
            else:
                self.assertEqual(ns[0]['floorPosition'], 2)  # .5s*2 + .25s*4

    def test_reversed_source_alpha_record_preserved_as_opaque(self):
        source = {'formatVersion': 3, 'offset': 0, 'judgeLineList': [{
            'bpm': 120, 'notesAbove': [], 'notesBelow': [], 'speedEvents': [],
            'judgeLineMoveEvents': [], 'judgeLineRotateEvents': [],
            'judgeLineDisappearEvents': [{'startTime': 912, 'endTime': 896, 'start': 0, 'end': 0}]}]}
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'reversed-source.json'; path.write_text(json.dumps(source))
            c = self.e.chart_import(str(path))
            self.assertEqual(self.e.chart_export(c['chart_ref'], c['digest'])['chart'], source)

    def test_cached_evidence_detached_keyed_and_paginated(self):
        analysis = {'onsets': list(range(500)), 'summary': {'rms': .4}}
        first = self.e.evidence_register('a' * 64, {'hop': 256}, analysis, 'independent fixture v1')
        self.assertFalse(first['cached'])
        same = self.e.evidence_register('a' * 64, {'hop': 256}, analysis, 'independent fixture v1')
        self.assertTrue(same['cached'])
        self.assertEqual(first['evidence_ref'], same['evidence_ref'])
        changed = self.e.evidence_register('a' * 64, {'hop': 512}, analysis, 'independent fixture v1')
        self.assertNotEqual(changed['evidence_ref'], first['evidence_ref'])
        analysis['onsets'][0] = -1
        page = self.e.evidence_get(first['evidence_ref'], ['onsets'], limit=3, cursor=4)
        self.assertEqual(page['values'], {'onsets': [4, 5, 6]})
        self.assertEqual(page['total_lengths'], {'onsets': 500})
        page['values']['onsets'][0] = -1
        self.assertEqual(self.e.evidence_get(first['evidence_ref'], ['onsets'], limit=1, cursor=4)['values']['onsets'], [4])

    def test_compact_preset_variation_deterministic_gameplay_and_annotations(self):
        results = []
        for _ in range(2):
            e = Engine(); c = e.chart_create({'title': 'Repeated motif'})
            a = e.authoring_presets('four_key_stair')['operation']
            a.update(start=8, count=8, annotation={'voice': 'attack', 'hand_plan': 'four stations, review required'})
            b = copy.deepcopy(a); b.update(start=24, order=[3, 1, 2, 0], kind='flick')
            p = e.authoring_plan(c['chart_ref'], c['digest'], [a, b], section={'rationale': 'recognizable varied return'})
            c = e.authoring_apply(p['plan_ref'], c['digest'])
            r = e.chart_read(c['chart_ref'], c['digest'], start=8, end=10)
            self.assertEqual(len(r['notes']), 8)
            self.assertTrue(all(n['kind'] == 'tap' and n['annotation']['voice'] == 'attack' for n in r['notes']))
            r = e.chart_read(c['chart_ref'], c['digest'], start=24, end=26)
            self.assertEqual(len(r['notes']), 8)
            self.assertEqual([n['x'] for n in r['notes'][:4]], [.28, -.1, .1, -.28])
            self.assertTrue(all(n['kind'] == 'flick' for n in r['notes']))
            results.append(e.chart_export(c['chart_ref'], c['digest'])['chart'])
        self.assertEqual(results[0], results[1])

    def test_revision_restore_preserves_original_and_used_plan_guard(self):
        p = self.plan([{'type': 'insert_notes', 'line_id': 'main', 'notes': [
            {'id': 'a', 'kind': 'tap', 'beat': 1, 'x': .1}]}])
        c = self.apply(p)
        restored = self.e.chart_revision_restore(c['chart_ref'], c['digest'], self.created['digest'])
        self.assertEqual(restored['digest'], self.created['digest'])
        self.assertEqual(self.read(restored)['notes'], [])
        with self.assertRaises(ValueError): self.apply(p, restored)

    def test_compiled_track_overlap_gap_instant_and_half_open_boundaries(self):
        tracks = CompiledTracks([
            {'channel': 'x', 'start': '0', 'end': '10', 'a': 0, 'b': 10},
            {'channel': 'x', 'start': '5', 'end': '20', 'a': 100, 'b': 100},
            {'channel': 'x', 'start': '25', 'end': '25', 'a': 7, 'b': 7, 'instant': True},
            {'channel': 'x', 'start': '30', 'end': '32', 'a': 8, 'b': 12}])
        # Contract: first active interval wins; half-open ends; latest ended value fills gaps.
        witnesses = {-1: 0, 0: 0, 5: 5, 6: 6, 10: 100, 19: 100,
                     20: 100, 24: 100, 25: 7, 29: 7, 30: 8, 31: 10, 32: 12}
        for time, value in witnesses.items():
            with self.subTest(time=time): self.assertEqual(tracks.evaluate('x', time), value)

    def test_compact_thread_cannot_cross_future_same_finger_hold_reservation(self):
        p = self.plan([{'type': 'insert_notes', 'line_id': 'main', 'notes': [
            {'id': 'future-hold', 'kind': 'hold', 'beat': 2, 'duration': '1/2', 'x': -.0888888889,
             'annotation': {'finger_key': 'L_index', 'gesture': 'future-sustain'}}]}])
        c = self.apply(p)
        evidence = {'beat_start': 0, 'beat_end': 4, 'quantized_audio_attack_candidates': [],
                    'source_articulation_events': [{'beat': b, 'kind_evidence': [2],
                                                   'source_head_count': 1, 'sustained_durations': []}
                                                   for b in [1, 1.5, 2, 2.5, 3]]}
        er = self.e.evidence_register('a' * 64, {}, evidence, 'independent scheduling fixture')
        config = {'id': 'earlier-edit', 'motif': 'connected', 'purpose': 'continuous thread',
                  'source_kinds': [], 'drag_mode': True, 'drag_min_step': 2}
        try:
            p = self.e.authoring_plan_phrase(c['chart_ref'], c['digest'], er['evidence_ref'], config)
        except ValueError:
            return  # Explicit refusal is safe; silent contradictory ownership is not.
        edited = self.e.authoring_apply(p['plan_ref'], c['digest'])
        drags = [n for n in self.read(edited)['notes'] if n['kind'] == 'drag']
        groups = {}
        for n in drags: groups.setdefault(n['annotation']['gesture'], []).append(n)
        for group in groups.values():
            if group[0]['annotation']['finger_key'] == 'L_index':
                beats = [Fraction(n['beat']) for n in group]
                self.assertFalse(min(beats) < Fraction(5, 2) and max(beats) > 2,
                                 'Continuous thread reuses a finger occupied by a future hold')

    def test_compact_phrase_retry_is_deterministic_detached_and_stale_guarded(self):
        evidence = {'beat_start': 0, 'beat_end': 4, 'quantized_audio_attack_candidates': [],
                    'source_articulation_events': [{'beat': b, 'kind_evidence': [1],
                                                   'source_head_count': 1, 'sustained_durations': []}
                                                   for b in [0, 1, 2]]}
        er = self.e.evidence_register('a' * 64, {}, evidence, 'independent retry fixture')
        config = {'id': 'answer', 'motif': 'call_response', 'purpose': 'alternating answer',
                  'source_kinds': [1], 'finger_order': ['L_index', 'R_index'],
                  'allowed_fingers': ['L_index', 'R_index']}
        p = self.e.authoring_plan_phrase(self.created['chart_ref'], self.created['digest'], er['evidence_ref'], config)
        retry = self.e.authoring_plan_phrase(self.created['chart_ref'], self.created['digest'], er['evidence_ref'], config)
        self.assertEqual(p['proposed_digest'], retry['proposed_digest'])
        self.assertEqual(self.read(self.created)['notes'], [])
        config['allowed_fingers'].clear()
        evidence['source_articulation_events'].clear()
        c = self.apply(p)
        ns = self.read(c)['notes']
        self.assertEqual([n['annotation']['finger_key'] for n in ns], ['L_index', 'R_index', 'L_index'])
        with self.assertRaises(ValueError): self.apply(retry)
        self.assertEqual(len(self.read(c)['notes']), 3)


if __name__ == '__main__':
    unittest.main()
