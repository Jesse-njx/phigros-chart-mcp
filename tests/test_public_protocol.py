"""Exercise the installed stdio boundary, including restart persistence."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


class PublicProtocol(unittest.IsolatedAsyncioTestCase):
    async def test_workflow_and_restart(self):
        params = StdioServerParameters(command=sys.executable,
                                       args=['-m', 'phigros_chart_mcp.server'])
        async def invoke(session, name, arguments):
            response = await session.call_tool(name, arguments)
            self.assertFalse(response.isError, response)
            if response.structuredContent is not None:
                return response.structuredContent
            return json.loads(next(c.text for c in response.content if c.type == 'text'))

        with tempfile.TemporaryDirectory() as td:
            output = str(Path(td) / 'phrase.json')
            async with stdio_client(params) as (reader, writer):
                async with ClientSession(reader, writer) as session:
                    await session.initialize()
                    listed = await session.list_tools()
                    self.assertTrue(all(t.description for t in listed.tools))
                    resources = await session.list_resources()
                    self.assertIn('phigros-authoring://guide/v1',
                                  [str(r.uri) for r in resources.resources])
                    guide = await session.read_resource('phigros-authoring://guide/v1')
                    self.assertTrue(guide.contents[0].text.strip())
                    measured = {'source_sha256': 'a' * 64,
                                'settings': {'fixture': True},
                                'analysis': {'onsets': [8, 9, 10, 11]},
                                'producer': 'synthetic-protocol-fixture-v1'}
                    evidence = await invoke(session, 'evidence_register', measured)
                    cached = await invoke(session, 'evidence_register', measured)
                    self.assertFalse(evidence['cached'])
                    self.assertTrue(cached['cached'])
                    self.assertEqual(evidence['evidence_ref'], cached['evidence_ref'])
                    created = await invoke(session, 'chart_create',
                                           {'metadata': {'title': 'Protocol witness'}, 'bpm': 120})
                    base = {k: created[k] for k in ('chart_ref', 'digest')}
                    planned = await invoke(session, 'authoring_plan', {
                        **base, 'section': {'evidence_refs': [evidence['evidence_ref']]}, 'operations': [
                            {'type': 'insert_pattern', 'line_id': 'main', 'pattern': 'stairs',
                             'start': 8, 'count': 8, 'step': '1/4',
                             'lane_x': [-.3, -.1, .1, .3]},
                            {'type': 'insert_notes', 'line_id': 'main', 'notes': [
                                {'id': 'sustain', 'kind': 'hold', 'beat': 12,
                                 'duration': 4, 'x': -.3},
                                {'id': 'trace', 'kind': 'drag', 'beat': 13, 'x': .1},
                                {'id': 'release', 'kind': 'flick', 'beat': 14, 'x': .3}]}]})
                    applied = await invoke(session, 'authoring_apply',
                                           {'plan_ref': planned['plan_ref'], 'digest': base['digest']})
                    current = {k: applied[k] for k in ('chart_ref', 'digest')}
                    ownership = await invoke(session, 'authoring_contacts', current)
                    self.assertEqual(ownership['assigned_heads'], 0)
                    self.assertEqual(ownership['unassigned_heads'], 11)
                    self.assertEqual(ownership['peak_declared_contacts'], 0)
                    stale = await session.call_tool('chart_read', base)
                    self.assertTrue(stale.isError)
                    bounded = await invoke(session, 'chart_read',
                                           {**current, 'start': '13', 'end': '14', 'limit': 10})
                    self.assertEqual({n['id'] for n in bounded['notes']}, {'sustain', 'trace'})
                    exported = await invoke(session, 'chart_export',
                                            {**current, 'output_path': output})
                    self.assertEqual(exported['note_count'], 11)
                    sidecar = exported['sidecar_path']
                    snapshot = json.loads(Path(output).read_text())
                    self.assertEqual({n['type'] for l in snapshot['judgeLineList']
                                      for n in l['notesAbove'] + l['notesBelow']}, {1, 2, 3, 4})
            # Fresh process: no inherited engine objects or ephemeral chart handles.
            async with stdio_client(params) as (reader, writer):
                async with ClientSession(reader, writer) as session:
                    await session.initialize()
                    restored = await invoke(session, 'chart_import', {'source_path': sidecar})
                    self.assertEqual(restored['digest'], current['digest'])
                    restored_args = {k: restored[k] for k in ('chart_ref', 'digest')}
                    replay = await invoke(session, 'chart_export', restored_args)
                    self.assertEqual(replay['chart'], snapshot)
                    recovered = await invoke(session, 'evidence_get',
                                             {'evidence_ref': evidence['evidence_ref'],
                                              'fields': ['onsets'], 'limit': 2, 'cursor': 1})
                    self.assertEqual(recovered['values']['onsets'], [9, 10])
                    self.assertEqual(recovered['total_lengths']['onsets'], 4)


if __name__ == '__main__':
    unittest.main()
