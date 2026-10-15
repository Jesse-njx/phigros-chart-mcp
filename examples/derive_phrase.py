"""Generic any-chart MCP evidence→compact motif demo; no song-specific parsing."""
import argparse
import asyncio
import json
from pathlib import Path
from sdk_client import connect,call

async def run(args):
    config=json.loads(Path(args.config).read_text())
    async with connect() as session:
        source=await call(session,'chart_import',{'source_path':args.source})
        context=await call(session,'chart_context',{'chart_ref':source['chart_ref']})
        audio=await call(session,'audio_analyze',{'source_path':args.audio}) if args.audio else None
        evidence=await call(session,'evidence_from_chart',dict(chart_ref=source['chart_ref'],digest=source['digest'],start=args.start,end=args.end,**({'audio_ref':audio['audio_ref']} if audio else {})))
        target=await call(session,'chart_create',dict(metadata={'title':args.title,'rationale':'Source articulation study; independent contact arrangement, provisional draft'},tempo_map=context['tempo_map'],offset_ms=context['offset_ms']))
        plan=await call(session,'authoring_plan_phrase',dict(chart_ref=target['chart_ref'],digest=target['digest'],evidence_ref=evidence['evidence_ref'],config=config))
        target=await call(session,'authoring_apply',{'plan_ref':plan['plan_ref'],'digest':target['digest']})
        check=await call(session,'authoring_check',{'chart_ref':target['chart_ref'],'digest':target['digest']})
        contacts=await call(session,'authoring_contacts',{'chart_ref':target['chart_ref'],'digest':target['digest']})
        exported=await call(session,'chart_export',{'chart_ref':target['chart_ref'],'digest':target['digest'],'output_path':args.output})
        print(json.dumps({'evidence':evidence,'phrase_summary':plan['phrase_summary'],'findings':check['finding_counts'],'declared_contact_findings':contacts['finding_counts'],'export':exported},ensure_ascii=False,indent=2))

def main():
    p=argparse.ArgumentParser(__doc__)
    p.add_argument('--source',required=True,help='Absolute official v3/sidecar JSON path')
    p.add_argument('--audio',help='Optional absolute audio file; install analysis extra and ffmpeg')
    p.add_argument('--start',required=True,help='Exact global beat, e.g. 32 or 64/3')
    p.add_argument('--end',required=True);p.add_argument('--config',required=True,help='Compact motif config JSON path')
    p.add_argument('--output',required=True,help='Absolute new official JSON output path')
    p.add_argument('--title',default='Source articulation phrase study')
    args=p.parse_args();asyncio.run(run(args))

if __name__=='__main__':main()
