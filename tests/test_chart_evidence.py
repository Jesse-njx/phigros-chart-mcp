"""Chart-to-evidence contracts: exact times, carry-ins and coordinate exclusion."""
import json
from pathlib import Path
import tempfile
import unittest
from phigros_chart_mcp.core import Engine

class ChartEvidenceTests(unittest.TestCase):
    def source(self,engine,operations,**kwargs):
        state=engine.chart_create({'title':'Evidence fixture'},**kwargs)
        plan=engine.authoring_plan(state['chart_ref'],state['digest'],operations)
        return engine.authoring_apply(plan['plan_ref'],state['digest'])

    def test_tempo_crossing_duration_offset_and_carry_in_are_distinct(self):
        e=Engine();c=self.source(e,[{'type':'insert_notes','line_id':'main','notes':[{'kind':'hold','beat':'1','duration':'2','x':-.2},{'kind':'tap','beat':'2','x':.1}]}],offset_ms=125,tempo_map=[{'beat':0,'bpm':120},{'beat':2,'bpm':240}])
        cached=e.evidence_from_chart(c['chart_ref'],c['digest'],'0','4');analysis=e.evidence[cached['evidence_ref']]['analysis']
        hold=analysis['source_articulation_events'][0]
        self.assertEqual((hold['beat_exact'],hold['chart_seconds_exact'],hold['audio_seconds_exact'],hold['sustained_durations_exact']),('1','1/2','5/8',['2']))
        clipped=e.evidence_from_chart(c['chart_ref'],c['digest'],'2','3');analysis=e.evidence[clipped['evidence_ref']]['analysis']
        self.assertEqual(len(analysis['source_articulation_events']),1)
        self.assertEqual(analysis['source_articulation_events'][0]['kind_evidence'],[1])
        self.assertEqual(analysis['carry_in_sustains'][0]['remaining_beats_exact'],'1')
        self.assertEqual(analysis['carry_in_sustains'][0]['tail_chart_seconds_exact'],'5/4')
        tail=e.evidence_from_chart(c['chart_ref'],c['digest'],'3','4')
        self.assertEqual(tail['summary']['carry_in_holds'],0)

    def test_mixed_source_line_bpm_groups_simultaneous_roles(self):
        e=Engine()
        def line(bpm,time,kind,hold=0):
            return {'bpm':bpm,'notesAbove':[{'type':kind,'time':time,'positionX':3,'holdTime':hold,'speed':1,'floorPosition':0}],'notesBelow':[],'speedEvents':[{'startTime':0,'endTime':10000,'value':2,'floorPosition':0}],'judgeLineMoveEvents':[],'judgeLineRotateEvents':[],'judgeLineDisappearEvents':[]}
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'source.json';p.write_text(json.dumps({'formatVersion':3,'offset':.125,'judgeLineList':[line(120,64,1),line(240,128,3,64)]}));c=e.chart_import(str(p))
        result=e.evidence_from_chart(c['chart_ref'],c['digest'],'2','3');row=e.evidence[result['evidence_ref']]['analysis']['source_articulation_events'][0]
        self.assertEqual((row['chart_seconds_exact'],row['audio_seconds_exact'],row['beat_exact']),('1','9/8','2'))
        self.assertEqual((row['source_head_count'],row['kind_evidence'],row['sustained_durations_exact']),(2,[1,3],['1']))

    def test_exact_fractional_source_beats_compile_without_float_rounding(self):
        e=Engine();c=self.source(e,[{'type':'insert_notes','line_id':'main','notes':[{'kind':'hold','beat':'1/3','duration':'4/3','x':-.2},{'kind':'tap','beat':'2/3','x':.2}]}],bpm=120)
        cached=e.evidence_from_chart(c['chart_ref'],c['digest'],'0','2')
        target=e.chart_create({'title':'New arrangement'},bpm=120)
        p=e.authoring_plan_phrase(target['chart_ref'],target['digest'],cached['evidence_ref'],{'id':'exact phrase','motif':'held_answer','purpose':'Exact source timing','hold_mode':'alternating','hold_threshold':1,'source_kinds':[1],'allowed_fingers':['L_index','R_index'],'hold_fingers':['L_index'],'finger_order':['R_index']})
        target=e.authoring_apply(p['plan_ref'],target['digest']);notes=e.chart_read(target['chart_ref'],target['digest'])['notes']
        self.assertEqual([(n['kind'],n['beat'],n.get('duration')) for n in notes],[('hold','1/3','4/3'),('tap','2/3',None)])

    def test_bounded_coordinate_free_record_and_repeat_cache_hit(self):
        e=Engine();c=self.source(e,[{'type':'insert_notes','line_id':'main','notes':[{'kind':'tap','beat':'0','x':-.314159}]}])
        one=e.evidence_from_chart(c['chart_ref'],c['digest'],'0','1');two=e.evidence_from_chart(c['chart_ref'],c['digest'],'0','1')
        self.assertEqual(one['evidence_ref'],two['evidence_ref']);self.assertTrue(two['cached'])
        analysis=e.evidence[one['evidence_ref']]['analysis'];text=json.dumps(analysis)
        self.assertNotIn('positionX',text);self.assertNotIn('-.314159',text);self.assertNotIn('rotation',text)
        self.assertNotIn('analysis',one)
        with self.assertRaises(ValueError):e.evidence_from_chart(c['chart_ref'],'0'*64,'0','1')
        with self.assertRaises(ValueError):e.evidence_from_chart(c['chart_ref'],c['digest'],'1','0')

    def test_optional_audio_join_selects_audio_relative_peak_with_offset(self):
        import importlib.util,shutil
        if importlib.util.find_spec('numpy') is None or shutil.which('ffmpeg') is None:self.skipTest('Optional NumPy/ffmpeg unavailable')
        import numpy as np
        import wave,os
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as directory,patch.dict(os.environ,{'PHIGROS_CHART_MCP_CACHE_DIR':directory}):
            path=Path(directory)/'pulse.wav';sr=22050;t=np.arange(sr)/sr
            samples=(np.sin(2*np.pi*100*t)*((t>=.625)&(t<.745))*.8*32767).astype('<i2')
            with wave.open(str(path),'wb') as w:w.setnchannels(1);w.setsampwidth(2);w.setframerate(sr);w.writeframes(samples.tobytes())
            e=Engine();audio=e.audio_analyze(str(path));c=self.source(e,[{'type':'insert_notes','line_id':'main','notes':[{'kind':'tap','beat':'1','x':0}]}],bpm=120,offset_ms=125)
            result=e.evidence_from_chart(c['chart_ref'],c['digest'],'1','5/4',audio_ref=audio['audio_ref'])
            candidates=e.evidence[result['evidence_ref']]['analysis']['quantized_audio_attack_candidates']
            self.assertEqual(len(candidates),1)
            self.assertEqual(candidates[0]['audio_seconds_exact'],'5/8')
            match=candidates[0]['register_evidence']['low_register']
            self.assertLessEqual(match['distance_ms'],25)
            self.assertGreater(abs(match['nearest_peak_s']-.5)*1000,25)
            self.assertTrue(e.audio_analyze(str(path))['cache_hit'])
            changed=bytearray(path.read_bytes());changed[-1]=1;path.write_bytes(changed)
            self.assertNotEqual(e.audio_analyze(str(path))['audio_ref'],audio['audio_ref'])
