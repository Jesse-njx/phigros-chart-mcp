import json
import random
import unittest
from fractions import Fraction
from importlib.resources import files
from phigros_chart_mcp.core import Engine, CompiledTracks

class AuthoringCoreTests(unittest.TestCase):
    def test_compiled_tracks_preserve_source_overlap_and_instant_order(self):
        rng=random.Random(81)
        rows=[dict(channel='x',start=str(rng.randrange(20)),end=str(rng.randrange(20,40)),a=rng.random(),b=rng.random()) for _ in range(80)]
        rows.extend([dict(channel='x',start='10',end='10',a=3,b=4),dict(channel='x',start='50',end='50',a=5,b=6)])
        ordered=sorted(rows,key=lambda e:Fraction(e['start']))
        tracks=CompiledTracks(rows)
        for t in [Fraction(i,4) for i in range(241)]:
            expected=0
            for e in ordered:
                start,end=Fraction(e['start']),Fraction(e['end'])
                if t<start:break
                if t<end:
                    expected=e['a']+(e['b']-e['a'])*float((t-start)/(end-start));break
                expected=e['b']
            self.assertEqual(tracks.evaluate('x',t),expected)

    def test_bundled_recipes_are_executable_and_settle_through_hold_tail(self):
        recipes=json.loads(files('phigros_chart_mcp').joinpath('resources/recipes.json').read_text())
        e=Engine(); c=e.chart_create(**recipes['mixed_roles_phrase']['create'])
        for name in ('mixed_roles_phrase','settled_hold_carrier'):
            p=e.authoring_plan(c['chart_ref'],c['digest'],recipes[name]['operations'])
            self.assertNotIn('moving_hold_contact',p['finding_counts'])
            self.assertNotIn('carrier_misses_anchor',p['finding_counts'])
            c=e.authoring_apply(p['plan_ref'],c['digest'])
        self.assertEqual(e.authoring_check(c['chart_ref'],c['digest'])['note_count'],9)

    def test_stable_id_repair_and_delete(self):
        e=Engine();c=e.chart_create({'title':'Repair'})
        p=e.authoring_plan(c['chart_ref'],c['digest'],[{'type':'insert_notes','line_id':'main','notes':[{'id':'attack','kind':'tap','beat':'1','x':.1}]}]);c=e.authoring_apply(p['plan_ref'],c['digest'])
        p=e.authoring_plan(c['chart_ref'],c['digest'],[{'type':'update_note','line_id':'main','id':'attack','note':{'kind':'flick','beat':'2','x':.2}}]);c=e.authoring_apply(p['plan_ref'],c['digest'])
        note=e.chart_read(c['chart_ref'],c['digest'])['notes'][0]
        self.assertEqual((note['id'],note['kind'],note['beat']),('attack','flick','2'))
        p=e.authoring_plan(c['chart_ref'],c['digest'],[{'type':'delete_notes','line_id':'main','ids':['attack']}]);c=e.authoring_apply(p['plan_ref'],c['digest'])
        self.assertEqual(e.authoring_check(c['chart_ref'],c['digest'])['note_count'],0)

    def test_compact_phrase_expansion_carries_prior_finger_guard(self):
        e=Engine();c=e.chart_create({'title':'Compact'})
        evidence={'beat_start':0,'beat_end':2,'source_articulation_events':[{'beat':0,'kind_evidence':[1],'source_head_count':1,'sustained_durations':[]},{'beat':1.75,'kind_evidence':[1],'source_head_count':1,'sustained_durations':[]}],'quantized_audio_attack_candidates':[]}
        cached=e.evidence_register('a'*64,{},evidence,'synthetic fixture')
        p=e.authoring_plan_phrase(c['chart_ref'],c['digest'],cached['evidence_ref'],{'id':'first','motif':'call_response','purpose':'Synthetic response','source_kinds':[1],'finger_order':['L_index','R_index']})
        self.assertEqual(p['phrase_summary']['head_count'],2)
        c=e.authoring_apply(p['plan_ref'],c['digest'])
        previous=e.chart_read(c['chart_ref'],c['digest'])['notes'][-1]['annotation']['finger_key']
        evidence={'beat_start':2,'beat_end':3,'source_articulation_events':[{'beat':2,'kind_evidence':[1],'source_head_count':1,'sustained_durations':[]}],'quantized_audio_attack_candidates':[]}
        cached=e.evidence_register('a'*64,{},evidence,'synthetic fixture')
        p=e.authoring_plan_phrase(c['chart_ref'],c['digest'],cached['evidence_ref'],{'id':'second','motif':'call_response','purpose':'Synthetic continuation','source_kinds':[1],'finger_order':['L_index','R_index']})
        c=e.authoring_apply(p['plan_ref'],c['digest'])
        self.assertNotEqual(e.chart_read(c['chart_ref'],c['digest'])['notes'][-1]['annotation']['finger_key'],previous)

    def test_evidence_import_returns_compact_reference(self):
        import tempfile
        from pathlib import Path
        e=Engine()
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory)/'phrase.json'
            p.write_text(json.dumps({'source_sha256':'a'*64,'settings':{},'producer':'fixture','analysis':{'field':[1,2,3]}}))
            result=e.evidence_import(str(p))
            self.assertNotIn('analysis',result)
            self.assertTrue(e.evidence_import(str(p))['cached'])

    def test_canonical_projection_keeps_carrier_translation_and_local_provenance(self):
        e=Engine();c=e.chart_create({'title':'Projection'})
        p=e.authoring_plan(c['chart_ref'],c['digest'],[{'type':'create_line','id':'carrier','name':'Hidden flight','role':'note_carrier','anchor_id':'main','x':.2,'opacity':0},{'type':'insert_notes','line_id':'carrier','notes':[{'kind':'tap','beat':'1','x':-.1}]}])
        c=e.authoring_apply(p['plan_ref'],c['digest'])
        note=e.chart_read(c['chart_ref'],c['digest'],line_id='carrier')['notes'][0]
        self.assertEqual(note['x'],-.1)
        self.assertAlmostEqual(note['world_target']['logical_x'],.1)
        self.assertAlmostEqual(note['canonical_axis']['axis_position'],.1)
        self.assertAlmostEqual(note['canonical_axis']['perpendicular_distance'],0)
        self.assertFalse(note['canonical_axis']['unresolved_remapping'])

    def test_two_finger_sustain_and_other_hand_answer(self):
        e=Engine();c=e.chart_create({'title':'Two contacts'})
        recipe=json.loads(files('phigros_chart_mcp').joinpath('resources/recipes.json').read_text())['two_finger_sustain_answer']
        cached=e.evidence_register(**recipe['evidence_record']);config=recipe['config']
        p=e.authoring_plan_phrase(c['chart_ref'],c['digest'],cached['evidence_ref'],config)
        c=e.authoring_apply(p['plan_ref'],c['digest'])
        notes=e.chart_read(c['chart_ref'],c['digest'])['notes']
        self.assertEqual([(n['kind'],n['annotation']['finger_key']) for n in notes],[('hold','L_index'),('tap','R_index'),('tap','R_index')])
        contacts=e.authoring_contacts(c['chart_ref'],c['digest'])
        self.assertEqual(contacts['peak_declared_contacts'],2)
        self.assertEqual(contacts['finding_counts'],{})

    def test_annotation_patch_preserves_scoring_export_and_scope(self):
        e=Engine();c=e.chart_create({'title':'Prose repair'})
        p=e.authoring_plan(c['chart_ref'],c['digest'],[{'type':'insert_pattern','line_id':'main','pattern':'stairs','start':'0','count':4,'step':'1','lane_x':[-.1,.1],'annotation':{'rationale':'Unverified instrument','voice':'contour'}}]);c=e.authoring_apply(p['plan_ref'],c['digest'])
        before=e.chart_export(c['chart_ref'],c['digest'])['chart']
        p=e.authoring_plan(c['chart_ref'],c['digest'],[{'type':'annotate_notes','line_id':'main','start':'1','end':'3','annotation_patch':{'rationale':'Measured melodic contour'}}]);c=e.authoring_apply(p['plan_ref'],c['digest'])
        self.assertEqual(before,e.chart_export(c['chart_ref'],c['digest'])['chart'])
        notes=e.chart_read(c['chart_ref'],c['digest'])['notes']
        self.assertEqual([n['annotation']['rationale'] for n in notes],['Unverified instrument','Measured melodic contour','Measured melodic contour','Unverified instrument'])
        self.assertTrue(all(n['annotation']['voice']=='contour' for n in notes))

    def test_portable_case_cards_are_bounded_and_retrievable(self):
        e=Engine();index=e.authoring_reference()
        names={r['name'] for r in index['categories']}
        self.assertTrue({'occupied_voice_answer','true_rest_after_tails','carrier_main_locus','terminal_length_duration','recent_lateral_cell'}<=names)
        for name in names:self.assertLess(len(json.dumps(e.authoring_reference(name)).encode()),4096)
        rest=e.authoring_reference('true_rest_after_tails')
        self.assertEqual(rest['actual_rest_seconds'],[106.206897,111.724138])
        stair=e.authoring_reference('four_key_stair')
        self.assertEqual([n['positionX'] for n in stair['source_notes']],[-6,-2,2,6])
