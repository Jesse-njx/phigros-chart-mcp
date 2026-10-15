"""Smoke-test a built wheel through MCP, without importing the source checkout."""
import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import sys
import zipfile

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def verify(wheel):
    code = ('import sys;sys.path.insert(0,' + repr(str(wheel)) + ');'
            'import phigros_chart_mcp;assert phigros_chart_mcp.__file__.startswith('
            + repr(str(wheel)) + ');from phigros_chart_mcp.server import main;main()')
    params = StdioServerParameters(command=sys.executable, args=['-I', '-c', code])
    async with stdio_client(params) as (reader, writer):
        async with ClientSession(reader, writer) as session:
            await session.initialize()
            listed = await session.list_tools()
            resources = await session.list_resources()
            for resource in resources.resources:
                result = await session.read_resource(str(resource.uri))
                assert result.contents

            async def call(name, arguments):
                result = await session.call_tool(name, arguments)
                assert not result.isError, result
                return result.structuredContent or json.loads(result.content[0].text)

            created = await call('chart_create', {'metadata': {'title': 'Wheel smoke fixture'}, 'bpm': 120})
            planned = await call('authoring_plan', {
                'chart_ref': created['chart_ref'], 'digest': created['digest'],
                'operations': [{'type': 'insert_pattern', 'line_id': 'main', 'pattern': 'stairs',
                                'start': 0, 'count': 8, 'step': '1/4', 'lane_x': [-.3, -.1, .1, .3]}]})
            applied = await call('authoring_apply', {'plan_ref': planned['plan_ref'], 'digest': created['digest']})
            checked = await call('authoring_check', {'chart_ref': applied['chart_ref'], 'digest': applied['digest']})
            assert checked['note_count'] == 8
            with zipfile.ZipFile(wheel) as archive:
                entries = [n for n in archive.namelist() if n.endswith('entry_points.txt')]
                assert len(entries) == 1
                entry_points = archive.read(entries[0]).decode()
                assert 'phigros-chart-mcp = phigros_chart_mcp.server:main' in entry_points
            return dict(status='PASS', wheel=str(wheel), size_bytes=wheel.stat().st_size,
                        sha256=hashlib.sha256(wheel.read_bytes()).hexdigest(),
                        tool_count=len(listed.tools), resource_count=len(resources.resources),
                        resource_uris=[str(r.uri) for r in resources.resources], entry_points=entry_points,
                        isolation='Fresh python -I process imports package from wheel archive; parent environment supplies third-party dependencies',
                        checks=['all bundled resources readable', 'real SDK discovery/create/plan/apply/check',
                                '8 expected notes', 'console entry point'])


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('wheel', type=Path)
    args = parser.parse_args()
    print(json.dumps(asyncio.run(verify(args.wheel.resolve(strict=True))), indent=2))
