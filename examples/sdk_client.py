"""Reusable official SDK client helpers for recipe replay through public MCP."""
import asyncio
from contextlib import asynccontextmanager
import json
import sys
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

@asynccontextmanager
async def connect(python=sys.executable):
    async with stdio_client(StdioServerParameters(command=python,args=['-m','phigros_chart_mcp.server'])) as (read,write):
        async with ClientSession(read,write) as session:
            await session.initialize(); yield session

async def call(session,name,arguments):
    result=await session.call_tool(name,arguments)
    value=result.structuredContent
    if value is None:
        text='\n'.join(c.text for c in result.content if c.type=='text')
        try:value=json.loads(text)
        except json.JSONDecodeError:value={'message':text}
    if not isinstance(value,dict):raise ValueError(f'{name}: unexpected non-object response')
    if result.isError: raise ValueError(f'{name}: {value}')
    return value

async def demo():
    async with connect() as session:
        state=await call(session,'chart_create',dict(metadata={'title':'Two-bar example'},bpm=120))
        args=dict(chart_ref=state['chart_ref'],digest=state['digest'],section={'name':'two bars','meter':'4/4','rationale':'synthetic SDK fixture'},operations=[dict(type='insert_pattern',line_id='main',pattern='stairs',start='32',count=32,step='1/4',lane_x=[-.28,-.1,.1,.28]),dict(type='transform_notes',line_id='main',start='36',end='40',mirror=True),dict(type='transform_notes',line_id='main',start='39.75',end='40',kind='flick')])
        plan=await call(session,'authoring_plan',args)
        state=await call(session,'authoring_apply',dict(plan_ref=plan['plan_ref'],digest=state['digest']))
        print(json.dumps(await call(session,'authoring_check',dict(chart_ref=state['chart_ref'],digest=state['digest'])),indent=2))

if __name__=='__main__':asyncio.run(demo())
