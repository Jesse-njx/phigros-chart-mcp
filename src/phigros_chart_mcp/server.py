"""Official MCP SDK stdio server with executable strict tool schemas."""
import asyncio
from importlib.resources import files
import json
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp import types
from .core import Engine
from .schemas import TOOL_NAMES,READ_ONLY,tool_schema,OPS

engine=Engine()
server=Server('phigros-chart-mcp',instructions='Read phigros-authoring://guide/v1. Structured tools do not parse speech. Default one main playing anchor; engine carriers/decorations are separate. Bounded phrase workflow and explicit checkpoint exports.')
RESOURCES={'phigros-authoring://guide/v1':('Authoring guide','guide.md'),
           'phigros-authoring://operations/v1':('Operations and units','operations.md'),
           'phigros-authoring://references/v1':('Small derived reference lessons','references.json'),
           'phigros-authoring://schemas/v1':('Executable operations/tool schemas',None),
           'phigros-authoring://recipes/v1':('All-kind and carrier recipes','recipes.json')}

@server.list_tools()
async def list_tools():
    return [types.Tool(name=name,description=getattr(engine,name).__doc__ or name,inputSchema=tool_schema(name),annotations=types.ToolAnnotations(readOnlyHint=name in READ_ONLY,destructiveHint=False,openWorldHint=False)) for name in TOOL_NAMES]

@server.call_tool(validate_input=True)
async def call_tool(name,arguments):
    if name not in TOOL_NAMES: raise ValueError('Unknown tool')
    try:
        result=getattr(engine,name)(**arguments)
        # SDK structuredContent plus compact text supports older clients too.
        return types.CallToolResult(content=[types.TextContent(type='text',text=json.dumps(result,separators=(',',':'),ensure_ascii=False))],structuredContent=result,isError=False)
    except (ValueError,KeyError,TypeError,OSError,ZeroDivisionError) as error:
        message=str(error)
        stale='stale' in message.lower() or 'digest' in message.lower()
        result={'error':{'code':'stale_state' if stale else 'request_rejected','message':message,'repair':'Read chart_context, inspect bounded affected passage and regenerate plan' if stale else 'Inspect executable schema/operation units and correct the named field; rejected offline operation made no chart edit'}}
        return types.CallToolResult(content=[types.TextContent(type='text',text=json.dumps(result))],structuredContent=result,isError=True)

@server.list_resources()
async def list_resources():
    return [types.Resource(uri=uri,name=title,mimeType='application/json' if filename is None or filename.endswith('.json') else 'text/markdown') for uri,(title,filename) in RESOURCES.items()]

@server.read_resource()
async def read_resource(uri):
    uri=str(uri)
    if uri not in RESOURCES: raise ValueError('Unknown resource')
    filename=RESOURCES[uri][1]
    if filename is None:return json.dumps({'schema_version':'1','operations':{'oneOf':OPS},'tools':{n:tool_schema(n) for n in TOOL_NAMES}},indent=2)
    return files('phigros_chart_mcp').joinpath('resources/'+filename).read_text()

@server.list_prompts()
async def list_prompts():
    return [types.Prompt(name='author_chart_phrase',description='Compact evidence to explicit phrase plan, one playing anchor by default',arguments=[types.PromptArgument(name='musical_evidence',required=True),types.PromptArgument(name='goal',required=True)])]

@server.get_prompt()
async def get_prompt(name,arguments):
    if name!='author_chart_phrase': raise ValueError('Unknown prompt')
    content='Read guide/operations; choose bounded preset/reference, retain evidence uncertainty, annotate ownership. Plan/check/apply under digest and checkpoint separately. Evidence: '+arguments['musical_evidence']+'\nGoal: '+arguments['goal']
    return types.GetPromptResult(messages=[types.PromptMessage(role='user',content=types.TextContent(type='text',text=content))])

async def run():
    async with stdio_server() as (read,write):
        await server.run(read,write,server.create_initialization_options())

def main(): asyncio.run(run())
if __name__=='__main__':main()
