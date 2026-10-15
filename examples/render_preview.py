"""Optional bounded renderer CLI adapter; no editor dependency in MCP core."""
import argparse
import json
from pathlib import Path
import subprocess


def main():
    p=argparse.ArgumentParser(__doc__)
    p.add_argument('--renderer',required=True);p.add_argument('--project',required=True);p.add_argument('--output',required=True)
    p.add_argument('--from-seconds',type=float,required=True);p.add_argument('--to-seconds',type=float,required=True)
    p.add_argument('--width',type=int,default=960);p.add_argument('--height',type=int,default=540);p.add_argument('--fps',type=int,default=30)
    a=p.parse_args();renderer=Path(a.renderer);project=Path(a.project);output=Path(a.output)
    if not all(x.is_absolute() for x in (renderer,project,output)):p.error('All paths must be absolute')
    if not renderer.is_file() or not project.is_dir() or not (project/'chart.json').is_file() or not (project/'meta.json').is_file():p.error('Renderer/project/chart.json/meta.json required')
    if output.exists() or not output.parent.is_dir():p.error('Output must be new with existing parent')
    if not 0<=a.from_seconds<a.to_seconds<=a.from_seconds+32:p.error('Preview must span >0 and <=32 seconds')
    if not 240<=a.width<=1920 or not 135<=a.height<=1080 or not 1<=a.fps<=60:p.error('Resolution/fps bounds exceeded')
    args=[str(renderer),str(project),'-o',str(output),'--from',str(a.from_seconds),'--to',str(a.to_seconds),'--width',str(a.width),'--height',str(a.height),'--fps',str(a.fps),'--no-telemetry']
    subprocess.run(args,check=True,timeout=600)
    if not output.is_file():raise RuntimeError('Renderer exited without requested output')
    print(json.dumps({'output_path':str(output),'range_seconds':[a.from_seconds,a.to_seconds],'width':a.width,'height':a.height,'fps':a.fps}))

if __name__=='__main__':main()
