"""Immutable offline chart revisions and explicit structured authoring.

Times are exact chart-relative seconds; audio offset is stored separately.
Public x/y are logical centered canvas fractions, not physical viewport promises.
"""
from __future__ import annotations
import copy
from bisect import bisect_right
from functools import lru_cache
from fractions import Fraction
import hashlib
import heapq
import json
import math
from pathlib import Path
import uuid

KINDS = {'tap': 1, 'drag': 2, 'hold': 3, 'flick': 4}
CHANNELS = ('x', 'y', 'rotation', 'opacity', 'speed')
LIMIT = 1000


def clean_json(value):
    """Reject nonfinite JSON recursively before hashing, planning or writing."""
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError('Nonfinite number')
    if isinstance(value, dict):
        for k, v in value.items():
            if not isinstance(k, str): raise ValueError('JSON object keys must be strings')
            clean_json(v)
    elif isinstance(value, list):
        for v in value: clean_json(v)
    elif value is not None and not isinstance(value, (str, int, float, bool)):
        raise ValueError('Non-JSON value')


@lru_cache(maxsize=200000)
def _rational(text):
    return Fraction(text)


def q(value):
    if isinstance(value, bool): raise ValueError('Boolean is not a beat/time')
    if isinstance(value, list):
        if len(value) != 3 or any(isinstance(v, bool) or not isinstance(v, int) for v in value):
            raise ValueError('Beat triple requires integers')
        w, n, d = value
        if w < 0 or not 0 <= n < d or d > 10000: raise ValueError('Invalid canonical beat triple')
        return Fraction(w) + Fraction(n, d)
    if isinstance(value, Fraction): return value
    try: result = _rational(str(value))
    except (ValueError, ZeroDivisionError, TypeError) as error: raise ValueError('Invalid rational') from error
    return result


def finite(value, name, lo=None, hi=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f'{name} must be finite numeric')
    if lo is not None and value < lo or hi is not None and value > hi: raise ValueError(f'{name} out of range')
    return value


def keys(value, allowed, required=()):
    if not isinstance(value, dict): raise ValueError('Expected object')
    if set(value) - set(allowed): raise ValueError('Unknown fields: ' + ', '.join(sorted(set(value) - set(allowed))))
    if set(required) - set(value): raise ValueError('Missing fields: ' + ', '.join(sorted(set(required) - set(value))))


def digest(model):
    clean_json(model)
    return hashlib.sha256(json.dumps(model, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def triple(value, tolerance=1e-6):
    f = q(value)
    if f < 0 or f > 1000000: raise ValueError('Native beat out of range')
    bounded = f.limit_denominator(10000)
    if abs(float(f - bounded)) > tolerance: raise ValueError('Native beat precision exceeds tolerance')
    w = bounded.numerator // bounded.denominator
    n = bounded - w
    return [w, n.numerator, n.denominator]


def tempo_check(points):
    if not isinstance(points, list) or not 1 <= len(points) <= 1000: raise ValueError('Invalid tempo map')
    result = []
    for p in points:
        keys(p, ('beat', 'bpm'), ('beat', 'bpm'))
        b = q(p['beat']); bpm = finite(p['bpm'], 'bpm', 1, 1000)
        if b < 0 or result and b <= q(result[-1]['beat']): raise ValueError('Tempo map must strictly increase')
        result.append(dict(beat=str(b), bpm=bpm))
    if q(result[0]['beat']) != 0: raise ValueError('Tempo map begins at zero')
    return result


def time_at(beat, points):
    b = q(beat)
    if b < 0: raise ValueError('Negative beat')
    total = Fraction(0)
    for i, p in enumerate(points):
        start = q(p['beat'])
        stop = min(b, q(points[i + 1]['beat'])) if i + 1 < len(points) else b
        if b < start: break
        total += (stop - start) * 60 / q(p['bpm'])
        if stop == b: break
    return total


def beat_at(time, points):
    t = q(time)
    if t < 0: raise ValueError('Negative chart time')
    for i, p in enumerate(points):
        start = q(p['beat']); bpm = q(p['bpm'])
        if i + 1 < len(points):
            span = (q(points[i + 1]['beat']) - start) * 60 / bpm
            if t >= span: t -= span; continue
        return start + t * bpm / 60
    raise ValueError('Missing tempo map')


class CompiledTracks:
    """Sorted channel tracks; first active source interval wins overlapping records.

    Prefix maximum ends locate the first still-active interval with two binary
    searches. Gaps inherit the latest started record's end value, including
    instantaneous records, exactly as the source-order evaluator did.
    """
    def __init__(self, events):
        self.channels = {}
        for channel in CHANNELS:
            rows = sorted((e for e in events if e['channel'] == channel), key=lambda e: q(e['start']))
            starts, ends, maximums = [], [], []
            maximum = Fraction(-10**30)
            for e in rows:
                start, end = q(e['start']), q(e['end'])
                starts.append(start); ends.append(end)
                maximum = max(maximum, end); maximums.append(maximum)
            self.channels[channel] = (rows, starts, ends, maximums)

    def evaluate(self, channel, time):
        time = q(time)
        rows, starts, ends, maximums = self.channels[channel]
        last = bisect_right(starts, time) - 1
        if last < 0:
            return {'x': 0, 'y': 0, 'rotation': 0, 'opacity': 1, 'speed': 2.2222222222222223}[channel]
        active = bisect_right(maximums, time)
        if active <= last:
            e = rows[active]
            ratio = float((time - starts[active]) / (ends[active] - starts[active]))
            return e['a'] + (e['b'] - e['a']) * ratio
        return rows[last]['b']


def evaluate(events, channel, time):
    return CompiledTracks(events).evaluate(channel, time)


def scroll_distance(events, time):
    """Integral of official scroll speed in seconds, with inherited gaps."""
    t = q(time)
    if t < 0: return 0.0
    cuts = {Fraction(0), t}
    for e in events:
        if e['channel'] == 'speed':
            cuts.update(v for v in (q(e['start']), q(e['end'])) if 0 < v < t)
    total = 0.0
    compiled = CompiledTracks(events)
    ordered = sorted(cuts)
    for a, b in zip(ordered, ordered[1:]):
        # Speed is required constant to match official v3 semantics.
        total += compiled.evaluate('speed', (a + b) / 2) * float(b - a)
    return total


def new_line(ident, name, role, anchor_id=None, **pose):
    if not ident or not isinstance(ident, str) or not isinstance(name, str): raise ValueError('Line id/name required')
    if role not in ('playing_anchor', 'note_carrier', 'decorative', 'unclassified'): raise ValueError('Unknown line role')
    if role == 'note_carrier' and not anchor_id: raise ValueError('Carrier requires anchor_id')
    keys(pose, CHANNELS)
    defaults = dict(x=0, y=0, rotation=0, opacity=1, speed=2.2222222222222223)
    defaults.update(pose)
    events = []
    for k, v in defaults.items():
        finite(v, k, 0 if k == 'opacity' else None, 1 if k == 'opacity' else None)
        events.append(dict(channel=k, start='0', end='1/1000', a=v, b=v))
    return dict(id=ident, name=name, role=role, anchor_id=anchor_id, notes=[], events=events)


def compile_note(data, model, line_id):
    keys(data, ('kind','beat','x','side','speed','duration','annotation','id'), ('kind','beat','x'))
    kind = data['kind']
    if kind not in KINDS: raise ValueError('Unknown note kind')
    at = time_at(data['beat'], model['tempo_map'])
    end = at
    if kind == 'hold':
        if 'duration' not in data or q(data['duration']) <= 0: raise ValueError('Hold requires positive duration')
        end = time_at(q(data['beat']) + q(data['duration']), model['tempo_map'])
    elif 'duration' in data: raise ValueError('Only holds have duration')
    side = data.get('side', 'above')
    if side not in ('above','below'): raise ValueError('side must be above or below')
    annotation = data.get('annotation', {})
    if not isinstance(annotation, dict) or len(json.dumps(annotation)) > 16000: raise ValueError('Invalid annotation')
    ordinal=sum(len(l['notes']) for l in model['lines'])
    generated_id=str(uuid.uuid5(uuid.NAMESPACE_URL,line_id+':'+str(ordinal)+':'+json.dumps(data,sort_keys=True)))
    return dict(id=data.get('id',generated_id), kind=kind, time=str(at), end=str(end),
                x=finite(data['x'],'x'), side=side, speed=finite(data.get('speed',1),'speed',.000001),
                annotation=copy.deepcopy(annotation))


def pattern_notes(op):
    keys(op, ('type','line_id','pattern','start','count','step','beats','lane_x','order','kind','duration','side','speed','annotation'),
         ('type','line_id','pattern','lane_x'))
    explicit=op.get('beats')
    if explicit is not None:
        if not isinstance(explicit,list) or not explicit: raise ValueError('beats must be nonempty exact onset array')
        onsets=[q(v) for v in explicit]
        if any(b<=a for a,b in zip(onsets,onsets[1:])) or onsets[0]<0: raise ValueError('Explicit beats strictly increasing and nonnegative')
        count=len(onsets)
        if 'count' in op and op['count']!=count: raise ValueError('count differs from beats')
    else:
        if not all(k in op for k in ('start','count','step')): raise ValueError('Pattern requires beats OR start/count/step')
        count=op['count']
    if isinstance(count,bool) or not isinstance(count,int) or not 1 <= count <= LIMIT: raise ValueError('Pattern count must be1..1000')
    lanes = op['lane_x']
    if not isinstance(lanes,list) or not 1 <= len(lanes) <= 10 or len(set(lanes)) != len(lanes): raise ValueError('Distinct lane_x required')
    for x in lanes: finite(x,'lane x')
    name = op['pattern']
    if name not in ('stairs','alternating','chord','drag_path'): raise ValueError('Unknown pattern')
    if name == 'alternating' and len(lanes) != 2: raise ValueError('Alternating needs two lanes')
    kind = op.get('kind','drag' if name == 'drag_path' else 'tap')
    if name == 'drag_path' and kind != 'drag': raise ValueError('Drag path must use drags')
    order = op.get('order',list(range(len(lanes))))
    if not isinstance(order,list) or not order or any(isinstance(v,bool) or not isinstance(v,int) or not 0 <= v < len(lanes) for v in order): raise ValueError('Invalid lane order')
    if explicit is None:
        start, step=q(op['start']),q(op['step'])
        if start<0 or step<=0: raise ValueError('Invalid pattern time')
        onsets=[start+i*step for i in range(count)]
    result=[]
    for i in range(count):
        if name == 'chord': xs=lanes
        elif name == 'drag_path':
            pos=i*(len(lanes)-1)/max(1,count-1); j=min(int(pos),max(0,len(lanes)-2))
            xs=[lanes[0] if len(lanes)==1 else lanes[j]+(lanes[j+1]-lanes[j])*(pos-j)]
        else: xs=[lanes[order[i%len(order)]]]
        for x in xs:
            n=dict(kind=kind,beat=str(onsets[i]),x=x)
            for key in ('duration','side','speed','annotation'):
                if key in op: n[key]=copy.deepcopy(op[key])
            result.append(n)
    if len(result)>LIMIT: raise ValueError('Pattern exceeds1000 objects')
    return result


class Engine:
    """Process-local immutable revisions. Exports persist artifacts explicitly."""
    def __init__(self):
        self.charts={}; self.plans={}; self.evidence={}; self.revisions={}; self.check_cache={}

    def _get(self,chart_ref,digest_value):
        if chart_ref not in self.charts: raise ValueError('Unknown chart_ref (server handles expire on restart)')
        row=self.charts[chart_ref]
        if row['digest'] != digest_value: raise ValueError('Stale digest; read current chart and regenerate plan')
        return row

    def _store(self,model,chart_ref=None):
        ref=chart_ref or 'chart://'+str(uuid.uuid4())
        d=digest(model)
        self.charts[ref]=dict(model=copy.deepcopy(model),digest=d)
        self.revisions[(ref,d)]=copy.deepcopy(model)
        return dict(chart_ref=ref,digest=self.charts[ref]['digest'],line_ids=[l['id'] for l in model['lines']])

    def chart_capabilities(self) -> dict:
        """Read implemented formats, units and limitations; no editor is required."""
        return dict(schema_version='1',formats=['phigros_v3','phichain_native'],
                    adapters={'phichain_native':'offline export only; no live editor mutation'},
                    transport='stdio',units={'x_y':'centered logical canvas fraction, physical scaling adapter-dependent','rotation':'degrees','opacity':'normalized 0..1','speed':'official line-scroll units; note speed separate multiplier','offset':'milliseconds'},
                    patterns=['stairs','alternating','chord','drag_path'],max_plan_note_changes=1000,
                    phrase_compilation='cached single-phrase measured evidence + compact config; server derives existing annotated scheduling',
                    persistence='verified sidecar checkpoints; imported cached evidence retained',
                    defaults='one main playing anchor; carriers/decorations distinct',
                    limitations=['linear move/rotation/alpha effects and constant speed only','no human-playability proof','handles process-local; use sidecar export/import for persistence','no gameplay renderer bundled','officialv1 unsupported'])

    def chart_create(self, metadata: dict, bpm: float=120, offset_ms: float=0,
                     audio_duration_seconds: float|None=None, main_line: dict|None=None,
                     tempo_map: list[dict]|None=None) -> dict:
        """Create empty offline chart with stable main line id 'main'. No file writes.

        main_line{x,y,rotation,opacity,speed}; x/y logical centered fractions,
        opacity0..1, speed official scroll units. tempo_map beats exact rationals.
        """
        clean_json(metadata); keys(metadata,('title','composer','charter','difficulty_label','rationale'),('title',))
        if not isinstance(metadata['title'],str) or not metadata['title'].strip(): raise ValueError('Title required')
        if audio_duration_seconds is not None: finite(audio_duration_seconds,'audio duration',.001)
        model=dict(schema_version='1',metadata=copy.deepcopy(metadata),offset_ms=finite(offset_ms,'offset'),
                   tempo_map=tempo_check(tempo_map or [dict(beat=0,bpm=bpm)]),
                   audio_duration_seconds=audio_duration_seconds,
                   lines=[new_line('main','Main playing line','playing_anchor',**(main_line or {}))],source=None,dirty=True)
        return self._store(model)

    def chart_import(self, source_path: str) -> dict:
        """Import officialv3 JSON or this server's authoring sidecar. Preserve source.

        Imported line roles remain unclassified until annotated; raw lines are not
        assumed to be distinct playing axes. Reject unsupported format versions.
        """
        path=Path(source_path).expanduser()
        if not path.is_absolute() or not path.is_file() or path.stat().st_size>64*1024*1024: raise ValueError('Import needs absolute existing JSON <=64MiB')
        src=json.loads(path.read_text()); clean_json(src)
        if isinstance(src,dict) and src.get('authoring_sidecar_version')==1:
            model=src['model']; self._basic_check(model)
            if src.get('model_digest')!=globals()['digest'](model): raise ValueError('Sidecar model digest mismatch')
            self.evidence.update(copy.deepcopy(src.get('cached_evidence',{})))
            return self._store(model)
        if src.get('formatVersion')!=3 or not isinstance(src.get('judgeLineList'),list) or not src['judgeLineList']: raise ValueError('Only official formatVersion3 supported')
        models=[]
        for i,l in enumerate(src['judgeLineList']):
            bpm=finite(l['bpm'],'line bpm',1,1000)
            t=lambda raw: str(q(raw)*Fraction(15,8)/q(bpm))
            events=[]; opaque=[]
            for key,channels in [('judgeLineMoveEvents',('x','y')),('judgeLineRotateEvents',('rotation',)),('judgeLineDisappearEvents',('opacity',)),('speedEvents',('speed',))]:
                for e in l.get(key,[]):
                    a,b=t(e['startTime']),t(e['endTime'])
                    if q(b)<q(a):
                        opaque.append(dict(channels=list(channels),source_key=key,event=copy.deepcopy(e),reason='reversed source event quarantined from evaluation'))
                        continue
                    if key=='judgeLineMoveEvents': values=[(e['start']-.5,e['end']-.5),(e['start2']-.5,e['end2']-.5)]
                    elif key=='speedEvents': values=[(e['value'],e['value'])]
                    else: values=[(e['start'],e['end'])]
                    for channel,(v,w) in zip(channels,values): events.append(dict(channel=channel,start=a,end=b,a=finite(v,channel),b=finite(w,channel),**({'instant':True} if a==b else {})))
            row=dict(id=f'line-{i}',name=f'Imported line{i}',role='unclassified',anchor_id=None,notes=[],events=events,opaque_events=opaque,source_bpm=bpm,source_line=copy.deepcopy(l))
            for side,array in [('above','notesAbove'),('below','notesBelow')]:
                for j,n in enumerate(l.get(array,[])):
                    kind=next((k for k,v in KINDS.items() if v==n['type']),None)
                    if kind is None: raise ValueError('Unsupported official note kind')
                    at=q(t(n['time'])); end=at+q(t(n.get('holdTime',0))) if kind=='hold' else at
                    multiplier=finite(n['speed'],'note speed')
                    if kind=='hold':
                        if end<=at: raise ValueError('Hold duration must be positive')
                        onset=evaluate(events,'speed',at)
                        unresolved=onset<=0
                        if not unresolved: multiplier/=onset
                    row['notes'].append(dict(id=f'{row["id"]}-{side}-{j}',kind=kind,time=str(at),end=str(end),x=finite(n['positionX'],'note x')/18,side=side,speed=multiplier,annotation={},source_note=copy.deepcopy(n),source_unusual_speed=multiplier<=0,hold_speed_unresolved=(kind=='hold' and unresolved)))
            models.append(row)
        model=dict(schema_version='1',metadata=dict(title=path.stem),offset_ms=finite(src.get('offset',0),'offset')*1000,
                   tempo_map=tempo_check([dict(beat=0,bpm=models[0]['source_bpm'])]),audio_duration_seconds=None,lines=models,
                   source=dict(path=str(path),sha256=hashlib.sha256(path.read_bytes()).hexdigest(),official=copy.deepcopy(src)),dirty=False)
        self._basic_check(model)
        result=self._store(model); result['warnings']=['Imported roles unclassified; use annotate_line before role-aware conclusions']
        if any(l.get('opaque_events') for l in models): result['warnings'].append('Reversed source events preserved opaquely; related edits/native export refuse incompatible conversion')
        return result

    def chart_context(self, chart_ref: str) -> dict:
        """Read current digest, line roles, tempo, metadata; recover from stale plans."""
        if chart_ref not in self.charts: raise ValueError('Unknown chart_ref')
        row=self.charts[chart_ref]; m=row['model']
        return dict(chart_ref=chart_ref,digest=row['digest'],metadata=copy.deepcopy(m['metadata']),tempo_map=copy.deepcopy(m['tempo_map']),offset_ms=m['offset_ms'],
                    lines=[dict(id=l['id'],name=l['name'],role=l['role'],anchor_id=l['anchor_id'],note_count=len(l['notes'])) for l in m['lines']])

    def chart_read(self, chart_ref: str, digest: str, start: str='0', end: str='1000001', time_basis: str='beat', limit: int=200, cursor: int=0, line_id: str|None=None, include_events: bool=False) -> dict:
        """Read all line events and heads/overlapping holds in half-open interval.

        time_basis='beat' or 'audio_ms'. Includes exact time strings, beat strings,
        annotations, side, line ID. Does not mutate or imply finger requirements.
        """
        m=self._get(chart_ref,digest)['model']
        if isinstance(limit,bool) or not isinstance(limit,int) or not 1<=limit<=1000 or isinstance(cursor,bool) or not isinstance(cursor,int) or cursor<0: raise ValueError('Read limit1..1000/cursor>=0')
        if line_id is not None and not any(l['id']==line_id for l in m['lines']): raise ValueError('Unknown line_id')
        if time_basis=='beat': a,b=time_at(start,m['tempo_map']),time_at(end,m['tempo_map'])
        elif time_basis=='audio_ms': a,b=q(start)/1000-q(m['offset_ms'])/1000,q(end)/1000-q(m['offset_ms'])/1000
        else: raise ValueError('Unknown time basis')
        if b<=a: raise ValueError('Range end must exceed start')
        notes=[]
        for l in m['lines']:
            if line_id is not None and l['id']!=line_id: continue
            for n in l['notes']:
                if q(n['time'])<b and q(n['end'])>=a:
                    row=copy.deepcopy(n); row.pop('source_note',None); row.update(line_id=l['id'],beat=str(beat_at(n['time'],m['tempo_map'])),audio_seconds=float(q(n['time'])+q(m['offset_ms'])/1000))
                    if n['kind']=='hold': row['duration']=str(beat_at(n['end'],m['tempo_map'])-beat_at(n['time'],m['tempo_map']))
                    notes.append(row)
        notes.sort(key=lambda n:(q(n['time']),n['line_id'],n['id']))
        line_rows=[dict(id=l['id'],name=l['name'],role=l['role'],anchor_id=l['anchor_id'],note_count=len(l['notes'])) for l in m['lines'] if line_id is None or l['id']==line_id]
        if include_events:
            for row in line_rows:
                l=next(l for l in m['lines'] if l['id']==row['id'])
                matching=[e for e in l['events'] if q(e['start'])<b and q(e['end'])>=a]
                row['events']=copy.deepcopy(matching[:1000])
                row['total_matching_events']=len(matching)
                row['events_truncated']=len(matching)>1000
                row['event_repair']='Narrow time range or line_id to retrieve remaining events' if len(matching)>1000 else None
        selected=notes[cursor:cursor+limit]; by_id={l['id']:l for l in m['lines']}; tracks={}
        for note in selected:
            line=by_id[note['line_id']]; at=q(note['time'])
            if line['id'] not in tracks:tracks[line['id']]=CompiledTracks(line['events'])
            track=tracks[line['id']];theta=math.radians(track.evaluate('rotation',at))
            x=track.evaluate('x',at)+note['x']*math.cos(theta)
            y=track.evaluate('y',at)+note['x']*1.5*math.sin(theta)
            note['world_target']={'logical_x':x,'logical_y':y,'line_rotation_degrees':math.degrees(theta),'convention':'1350x900 logical adapter canvas; impact only'}
            owner=by_id.get(line['anchor_id']) if line['role']=='note_carrier' else line if line['role']=='playing_anchor' else None
            if owner:
                if owner['id'] not in tracks:tracks[owner['id']]=CompiledTracks(owner['events'])
                ot=tracks[owner['id']];angle=math.radians(ot.evaluate('rotation',at))
                dx=(x-ot.evaluate('x',at))*1350;dy=(y-ot.evaluate('y',at))*900
                note['canonical_axis']={'anchor_id':owner['id'],'axis_position':(dx*math.cos(angle)+dy*math.sin(angle))/1350,
                    'perpendicular_distance':-dx*math.sin(angle)+dy*math.cos(angle),'method':'impact target projected onto declared anchor tangent; preserves source-local x separately','unresolved_remapping':False}
            else:note['canonical_axis']={'anchor_id':None,'unresolved_remapping':True,'method':'No declared anchor; infer/annotate role before normalization'}
        return dict(chart_ref=chart_ref,digest=digest,notes=selected,total_matching_notes=len(notes),next_cursor=cursor+limit if cursor+limit<len(notes) else None,lines=line_rows,offset_ms=m['offset_ms'],tempo_map=copy.deepcopy(m['tempo_map']))

    def _basic_check(self,m):
        clean_json(m); tempo_check(m['tempo_map']); finite(m['offset_ms'],'offset')
        if not m['lines'] or len(m['lines'])>10000: raise ValueError('Invalid line count')
        ids=set(); note_ids=set()
        for l in m['lines']:
            if l['id'] in ids: raise ValueError('Duplicate line ID')
            ids.add(l['id'])
            if l['role'] not in ('playing_anchor','note_carrier','decorative','unclassified'): raise ValueError('Invalid role')
            if l['role']=='decorative' and l['notes']: raise ValueError('Decorative line cannot own scoring notes')
            for e in l['events']:
                if e['channel'] not in CHANNELS or q(e['end'])<q(e['start']) or q(e['end'])==q(e['start']) and not e.get('instant'): raise ValueError('Invalid event')
                finite(e['a'],'event'); finite(e['b'],'event')
                if e['channel']=='speed' and e['a']!=e['b']: raise ValueError('Officialv3 speed event must be constant')
            for n in l['notes']:
                if n['id'] in note_ids: raise ValueError('Duplicate note ID')
                note_ids.add(n['id'])
                if n['kind'] not in KINDS or n['side'] not in ('above','below') or q(n['time'])<0: raise ValueError('Invalid note')
                finite(n['x'],'note x'); finite(n['speed'],'note speed')
                if n['speed']<=0 and not n.get('source_unusual_speed'): raise ValueError('Authored note speed must be positive')
                if n['kind']=='hold' and q(n['end'])<=q(n['time']): raise ValueError('Invalid hold duration')
                if n['kind']!='hold' and q(n['end'])!=q(n['time']): raise ValueError('Nonhold has duration')
                if q(n['time'])+q(m['offset_ms'])/1000<0: raise ValueError('Note precedes audio zero')
                duration=m.get('audio_duration_seconds')
                if duration is not None and q(n['end'])+q(m['offset_ms'])/1000>q(duration): raise ValueError('Note tail exceeds audio duration')
        for l in m['lines']:
            if l['role']=='note_carrier' and l['anchor_id'] not in ids: raise ValueError('Carrier anchor missing')
            if l['role']=='note_carrier' and next(x for x in m['lines'] if x['id']==l['anchor_id'])['role']!='playing_anchor': raise ValueError('Carrier target must be a playing_anchor')
        if len(note_ids)>100000: raise ValueError('Chart exceeds100000 notes')

    def authoring_plan_phrase(self, chart_ref: str, digest: str, evidence_ref: str, config: dict,
                              lanes: dict|None=None, defaults: dict|None=None, line_id: str='main') -> dict:
        """Plan one compact measured-evidence motif without expanding notes in client context.

        Cached analysis must be one phrase: beat_start/end, source_articulation_events
        and quantized_audio_attack_candidates. No audio analysis runs here. Config selects
        ownership, hold/thread/attack/release rules; expansion <=1000 notes. Uses the same
        transactional planner/check/apply protocol. All annotations/evidence survive checkpoint.
        """
        from .motifs import compile_phrase, FINGERS
        clean_json(config)
        keys(config,('id','motif','purpose','source_kinds','finger_order','allowed_fingers','hold_fingers','drag_fingers','hold_mode','drag_mode','merge_sustains','articulation','pulse_register','hold_threshold','hold_max','hold_release_trim','drag_min_step','drag_piece_max','drag_bend','pulse_step','accent_grid'),('id','motif','purpose'))
        for key in ('id','motif','purpose'):
            if not isinstance(config[key],str) or not config[key]: raise ValueError('Phrase '+key+' required')
        for key in ('hold_threshold','hold_max','hold_release_trim','drag_min_step','drag_piece_max','drag_bend','pulse_step','accent_grid'):
            if key in config: finite(config[key],key,0,1000000)
        if 'finger_order' in config and (not config['finger_order'] or len(set(config['finger_order']))!=len(config['finger_order']) or not set(config['finger_order'])<=set(FINGERS)): raise ValueError('Invalid finger order')
        if 'allowed_fingers' in config and (not config['allowed_fingers'] or len(set(config['allowed_fingers']))!=len(config['allowed_fingers']) or not set(config['allowed_fingers'])<=set(FINGERS)): raise ValueError('Invalid allowed fingers')

        if evidence_ref not in self.evidence: raise ValueError('Evidence unavailable; register a single phrase first')
        m=self._get(chart_ref,digest)['model']; target=next((l for l in m['lines'] if l['id']==line_id),None)
        if target is None or target['role']=='decorative': raise ValueError('Phrase requires scoring line')
        lanes=lanes or dict(zip(FINGERS,[-.2666666667,-.0888888889,.0888888889,.2666666667]))
        if set(lanes)!=set(FINGERS): raise ValueError('Lanes require all four finger keys')
        for value in lanes.values(): finite(value,'lane x',-.5,.5)
        defaults=dict({'release_guard_beats':.5,'note_speed':1},**(defaults or {}))
        keys(defaults,('release_guard_beats','note_speed'),('release_guard_beats','note_speed'))
        finite(defaults['release_guard_beats'],'release guard',0,8);finite(defaults['note_speed'],'note speed',.00001,100)
        evidence=copy.deepcopy(self.evidence[evidence_ref]['analysis'])
        for key in ('beat_start','beat_end','source_articulation_events','quantized_audio_attack_candidates'):
            if key not in evidence: raise ValueError('Phrase evidence missing '+key)
        if len(evidence['source_articulation_events'])>1000 or len(evidence['quantized_audio_attack_candidates'])>2000: raise ValueError('Phrase evidence too large; split into bounded sections')
        if not 0<=evidence['beat_start']<evidence['beat_end']: raise ValueError('Invalid phrase range')
        scheduling={'busy':{f:[] for f in FINGERS},'last_strike':{}}
        threads={}; start=evidence['beat_start']; guard=defaults['release_guard_beats']
        for line in m['lines']:
            for note in line['notes']:
                annotation=note.get('annotation',{}); finger=annotation.get('finger_key')
                if finger not in FINGERS: continue
                head=float(beat_at(note['time'],m['tempo_map'])); tail=float(beat_at(note['end'],m['tempo_map']))
                if note['kind'] in ('hold','flick'):
                    scheduling['busy'][finger].append((head,tail+guard))
                elif note['kind']=='drag':
                    key=(finger,annotation.get('gesture',note['id']))
                    span=threads.setdefault(key,[head,head]);span[0]=min(span[0],head);span[1]=max(span[1],head)
                if note['kind']!='drag' and head<=start:
                    scheduling['last_strike'][finger]=max(scheduling['last_strike'].get(finger,float('-inf')),head)
        for (finger,_),(head,tail) in threads.items():scheduling['busy'][finger].append((head,tail+guard))
        operations,summary=compile_phrase(copy.deepcopy(config),evidence,dict(lanes=lanes,defaults=defaults),scheduling)

        for operation in operations: operation['line_id']=line_id
        if not operations: operations=[dict(type='annotate_line',line_id=line_id,role=target['role'],anchor_id=target['anchor_id'],annotation={'rest_phrase':config['id']})]
        section=dict(name=config['id'],start_beat=str(evidence['beat_start']),end_beat=str(evidence['beat_end']),
                     evidence_refs=[evidence_ref],motif_config=config,lanes=lanes,defaults=defaults,summary=summary)
        result=self.authoring_plan(chart_ref,digest,operations,section=section)
        result['phrase_summary']=summary
        return result

    def authoring_plan(self, chart_ref: str, digest: str, operations: list[dict], section: dict|None=None) -> dict:
        """Compile explicit operations into prospective revision without edits.

        See phigros-authoring://operations/v1. section is phrase metadata, not a scope restriction. Ranges belong in each
        operation explicitly. Single transaction <=1000 changed note objects. Patterns/transforms compile sequentially before apply.
        """
        base=self._get(chart_ref,digest)['model']; m=copy.deepcopy(base)
        clean_json(operations)
        if not isinstance(operations,list) or not 1<=len(operations)<=1000: raise ValueError('Operations1..1000 required')
        changed=0
        for op in operations:
            action=op.get('type')
            if action=='create_line':
                keys(op,('type','id','name','role','anchor_id',*CHANNELS),('type','id','name','role'))
                if any(l['id']==op['id'] for l in m['lines']): raise ValueError('Line already exists')
                m['lines'].append(new_line(op['id'],op['name'],op['role'],op.get('anchor_id'),**{k:op[k] for k in CHANNELS if k in op})); continue
            line_id=op.get('line_id')
            l=next((x for x in m['lines'] if x['id']==line_id),None)
            if l is None: raise ValueError('Unknown line_id')
            l['edited']=True
            if action=='insert_notes':
                keys(op,('type','line_id','notes'),('type','line_id','notes'))
                if not isinstance(op['notes'],list) or not 1<=len(op['notes'])<=LIMIT: raise ValueError('notes1..1000')
                l['notes'].extend(compile_note(n,m,line_id) for n in op['notes']); changed+=len(op['notes'])
            elif action=='annotate_notes':
                keys(op,('type','line_id','ids','start','end','annotation_patch'),('type','line_id','annotation_patch'))
                patch=op['annotation_patch']
                if not isinstance(patch,dict) or len(json.dumps(patch))>16384: raise ValueError('Annotation patch must be object <=16KiB')
                if 'ids' in op:
                    if 'start' in op or 'end' in op or not isinstance(op['ids'],list) or not 1<=len(op['ids'])<=LIMIT or len(set(op['ids']))!=len(op['ids']): raise ValueError('Choose unique ids OR start/end range')
                    selected=[n for n in l['notes'] if n['id'] in op['ids']]
                    if len(selected)!=len(op['ids']):raise ValueError('Unknown note ID')
                else:
                    if 'start' not in op or 'end' not in op:raise ValueError('Annotation range requires start/end')
                    start,end=time_at(op['start'],m['tempo_map']),time_at(op['end'],m['tempo_map'])
                    if start>=end:raise ValueError('Annotation range must increase')
                    selected=[n for n in l['notes'] if start<=q(n['time'])<end]
                for note in selected:note.setdefault('annotation',{}).update(copy.deepcopy(patch))
                changed+=len(selected)
            elif action=='insert_pattern':
                ns=pattern_notes(op); l['notes'].extend(compile_note(n,m,line_id) for n in ns); changed+=len(ns)
            elif action=='annotate_line':
                keys(op,('type','line_id','role','anchor_id','annotation'),('type','line_id','role'))
                l['role']=op['role']; l['anchor_id']=op.get('anchor_id'); l['annotation']=copy.deepcopy(op.get('annotation',{}))
            elif action=='set_effect':
                keys(op,('type','line_id','channel','start','end','a','b','easing','replace'),('type','line_id','channel','start','end','a'))
                channel=op['channel']; a=time_at(op['start'],m['tempo_map']); b=time_at(op['end'],m['tempo_map'])
                if any(channel in e['channels'] for e in l.get('opaque_events',[])): raise ValueError('Source compatibility: channel has quarantined reversed events; cannot edit faithfully')
                if channel not in CHANNELS or b<=a or op.get('easing','linear')!='linear': raise ValueError('Effect channel/range/easing unsupported')
                v=finite(op['a'],'effect value',0 if channel=='opacity' else None,1 if channel=='opacity' else None)
                w=finite(op.get('b',v),'effect value',0 if channel=='opacity' else None,1 if channel=='opacity' else None)
                if channel=='speed' and v!=w: raise ValueError('Speed transitions unsupported for officialv3')
                if 'replace' in op and not isinstance(op['replace'],bool): raise ValueError('replace must be boolean')
                old=copy.deepcopy(l['events']); replacement=[]; overlaps=[]
                for e in old:
                    if e['channel']!=channel or q(e['end'])<=a or q(e['start'])>=b: replacement.append(e); continue
                    overlaps.append(e)
                    if not op.get('replace',False): raise ValueError('Event overlap: explicit replace required')
                    if q(e['start'])<a:
                        left=copy.deepcopy(e); left['end']=str(a); left['b']=evaluate([e],channel,a); replacement.append(left)
                    if q(e['end'])>b:
                        right=copy.deepcopy(e); right['start']=str(b); right['a']=evaluate([e],channel,b); replacement.append(right)
                replacement.append(dict(channel=channel,start=str(a),end=str(b),a=v,b=w))
                # Explicit restoration preserves inherited state outside replacement.
                if op.get('replace',False) and not any(e['channel']==channel and q(e['start'])==b for e in replacement):
                    next_start=min((q(e['start']) for e in old if e['channel']==channel and q(e['start'])>b),default=b+Fraction(1,1000))
                    restored=evaluate(old,channel,b)
                    replacement.append(dict(channel=channel,start=str(b),end=str(next_start),a=restored,b=restored))
                l['events']=replacement
                l.setdefault('edited_channels',[])
                if channel not in l['edited_channels']: l['edited_channels'].append(channel)
            elif action=='transform_notes':
                keys(op,('type','line_id','start','end','mirror','shift','scale','speed_multiplier','beat_shift','kind','side'),('type','line_id','start','end'))
                a,b=q(op['start']),q(op['end'])
                if a<0 or b<=a: raise ValueError('Invalid transform range')
                if 'mirror' in op and not isinstance(op['mirror'],bool): raise ValueError('mirror must be boolean')
                if op.get('kind')=='hold': raise ValueError('Create holds with explicit duration; kind transform accepts tap/drag/flick')
                shift=finite(op.get('shift',0),'shift'); scale=finite(op.get('scale',1),'scale',.000001); speed=finite(op.get('speed_multiplier',1),'speed',.000001)
                for n in l['notes']:
                    at=beat_at(n['time'],m['tempo_map'])
                    if a<=at<b:
                        duration=beat_at(n['end'],m['tempo_map'])-at
                        newat=at+q(op.get('beat_shift',0)); n['time']=str(time_at(newat,m['tempo_map'])); n['end']=str(time_at(newat+duration,m['tempo_map']))
                        n['x']=(-1 if op.get('mirror',False) else 1)*n['x']*scale+shift; n['speed']*=speed
                        if 'kind' in op: n['kind']=op['kind']; n['end']=n['time']
                        if 'side' in op: n['side']=op['side']
                        n['edited']=True
                        changed+=1
            elif action=='delete_notes':
                keys(op,('type','line_id','ids'),('type','line_id','ids'))
                ids=op['ids']
                if not isinstance(ids,list) or not ids or len(set(ids))!=len(ids): raise ValueError('Distinct note IDs required')
                known={n['id'] for n in l['notes']}
                if any(i not in known for i in ids): raise ValueError('Delete ID not on target line')
                l['notes']=[n for n in l['notes'] if n['id'] not in ids]; changed+=len(ids)
            elif action=='update_note':
                keys(op,('type','line_id','id','note'),('type','line_id','id','note'))
                index=next((i for i,n in enumerate(l['notes']) if n['id']==op['id']),None)
                if index is None: raise ValueError('Update ID not on target line')
                old=l['notes'][index]; replacement=compile_note({**op['note'],'id':op['id']},m,line_id)
                if 'source_note' in old: replacement['source_note']=copy.deepcopy(old['source_note'])
                replacement['edited']=True; l['notes'][index]=replacement; changed+=1
            elif action=='move_note':
                keys(op,('type','line_id','id','target_line_id','x'),('type','line_id','id','target_line_id','x'))
                index=next((i for i,n in enumerate(l['notes']) if n['id']==op['id']),None)
                target=next((x for x in m['lines'] if x['id']==op['target_line_id']),None)
                if index is None or target is None: raise ValueError('Unknown move note/target')
                n=l['notes'].pop(index)
                if n.get('hold_speed_unresolved') or n.get('source_unusual_speed'): raise ValueError('Opaque source-speed note cannot be moved; author replacement explicitly')
                n['x']=finite(op['x'],'target local x'); n['edited']=True; target['notes'].append(n); target['edited']=True; changed+=1
            else: raise ValueError('Unknown operation type')
            if changed>LIMIT: raise ValueError('Plan exceeds1000 changed note objects; split phrases')
        m['dirty']=True
        m.setdefault('phrase_recipes',[]).append(dict(section=copy.deepcopy(section),operations=copy.deepcopy(operations),base_digest=digest))
        self._basic_check(m)
        checks=self._compact_findings(m)
        plan='plan://'+str(uuid.uuid4())
        self.plans[plan]=dict(chart_ref=chart_ref,base_digest=digest,model=m,operations=copy.deepcopy(operations),section=copy.deepcopy(section),used=False)
        return dict(plan_ref=plan,base_digest=digest,proposed_digest=globals()['digest'](m),summary=dict(changed_note_objects=changed,total_notes=sum(len(l['notes']) for l in m['lines']),line_count=len(m['lines'])),findings=checks['findings'],total_findings=checks['total_findings'],finding_counts=checks['finding_counts'])

    def authoring_apply(self, plan_ref: str, digest: str) -> dict:
        """Atomically apply one planned offline revision. Stale/used plans fail.

        All checks run before mutation. No file writes, editor actions or retries.
        """
        if plan_ref not in self.plans: raise ValueError('Unknown plan_ref')
        p=self.plans[plan_ref]
        if p['used'] or digest!=p['base_digest']: raise ValueError('Used plan or wrong base digest')
        self._get(p['chart_ref'],digest); self._basic_check(p['model'])
        result=self._store(p['model'],p['chart_ref']); p['used']=True
        result.update(plan_ref=plan_ref,previous_digest=digest,operations_applied=len(p['operations']),section=p['section'],status='applied_offline')
        return result

    def _findings(self,m):
        findings=[]; hits={}; sustain={}; fresh={}; notes=[]
        def add(code,message,line_id=None,note_id=None,severity='warning'):
            findings.append(dict(code=code,message=message,line_id=line_id,note_id=note_id,severity=severity))
        tracks={l['id']: CompiledTracks(l['events']) for l in m['lines']}
        anchors=[l for l in m['lines'] if l['role']=='playing_anchor']
        if len(anchors)!=1: add('anchor_count',f'{len(anchors)} declared playing anchors; regular default is one, imported roles may be unresolved')
        for l in m['lines']:
            if l.get('opaque_events'): add('opaque_source_events','Quarantined reversed source events; related editing/native conversion unavailable',l['id'])
            if l['role']=='unclassified': add('unclassified_line','Do not infer playing axes from source line count',l['id'])
            for channel in CHANNELS:
                es=sorted([e for e in l['events'] if e['channel']==channel],key=lambda e:q(e['start']))
                for prev,e in zip(es,es[1:]):
                    if q(prev['end'])>q(e['start']): add('event_overlap',f'{channel} source events overlap; export refuses edited ambiguous tracks',l['id'])
            for n in l['notes']:
                at=q(n['time']); notes.append((l,n));
                if n.get('source_unusual_speed') or n.get('hold_speed_unresolved'): add('opaque_source_speed','Unusual imported speed preserved; native export or related edits may refuse compatibility',l['id'],n['id'])
                hits[at]=hits.get(at,0)+1
                if n['kind'] in ('tap','flick'): fresh[at]=fresh.get(at,0)+1
                if n['kind']=='hold':
                    sustain[at]=sustain.get(at,0)+1; end=q(n['end']); sustain[end]=sustain.get(end,0)-1
                angle=math.radians(tracks[l['id']].evaluate('rotation',at))
                x=tracks[l['id']].evaluate('x',at)+n['x']*math.cos(angle)
                # Logical y note-distance at impact is zero; x cross-term maps via1350/900.
                y=tracks[l['id']].evaluate('y',at)+n['x']*1.5*math.sin(angle)
                if abs(x)>.5 or abs(y)>.5: add('target_outside_logical_canvas',f'Target ({x:.3f},{y:.3f}); verify actual viewport geometry',l['id'],n['id'])
                owner=l
                if l['role']=='note_carrier': owner=next(a for a in m['lines'] if a['id']==l['anchor_id'])
                if tracks[owner['id']].evaluate('opacity',at)<.1: add('anchor_invisible','Assigned anchor near invisible at judgment; carrier alpha alone is not note visibility',l['id'],n['id'])
                if l['role']=='note_carrier':
                    theta=math.radians(tracks[owner['id']].evaluate('rotation',at))
                    dx=(x-tracks[owner['id']].evaluate('x',at))*1350; dy=(y-tracks[owner['id']].evaluate('y',at))*900
                    error=abs(-math.sin(theta)*dx+math.cos(theta)*dy)
                    if error>1: add('carrier_misses_anchor',f'Carrier target is{error:.2f} logical units off declared main locus',l['id'],n['id'])
                if tracks[l['id']].evaluate('speed',at)<=0: add('nonpositive_scroll','Zero/negative line scroll needs special approach review',l['id'],n['id'])
                if n['kind']=='hold' and l['role']=='note_carrier':
                    end=q(n['end']); samples={at,(at+end)/2,end}
                    samples.update(q(e[k]) for e in l['events'] for k in ('start','end') if at<q(e[k])<end)
                    if any(abs(tracks[l['id']].evaluate(c,at)-tracks[l['id']].evaluate(c,t))>1e-6 for c in ('x','y','rotation') for t in samples):
                        add('moving_hold_contact','Sampled carrier motion during hold; inspect continuous contact/anchor path',l['id'],n['id'])
        identity={}
        for l,n in notes:
            key=(n['time'],l['id'],n['side'],n['x'])
            if key in identity: add('coincident_local_objects','Coincident objects may share a gesture; do not count as independent fingers',l['id'],n['id'])
            identity[key]=n['id']
        active=peak=0
        for at in sorted(sustain): active+=sustain[at]; peak=max(peak,active)
        return dict(valid=True,note_count=len(notes),raw_simultaneous_objects_peak=max(hits.values(),default=0),fresh_strikes_peak=max(fresh.values(),default=0),raw_active_holds_peak=peak,
                    findings=findings,limitations=['raw objects/holds are not independent fingers','geometry is sampled logical-canvas estimate, not gameplay/readability proof','no continuous approach/hold-body simulation','no human playtest'])

    def authoring_contacts(self, chart_ref: str, digest: str, max_witnesses: int=20, rapid_repeat_ms: float=100) -> dict:
        """Read bounded declared-finger ownership conflicts/repeats/travel; labels are hypotheses.

        Holds occupy half-open intervals; same finger+gesture drags share one continuous
        contact. Threshold milliseconds is a review heuristic, not a feasibility rule.
        Travel uses logical 1350x900 adapter canvas target positions, not physical pixels.
        Unassigned coverage is reported; no raw-object to finger inference is made.
        """
        from .contact_audit import audit_contacts
        m=self._get(chart_ref,digest)['model'];tracks={l['id']:CompiledTracks(l['events']) for l in m['lines']}
        def target(line,note,time):
            t=tracks[line['id']];angle=math.radians(t.evaluate('rotation',time))
            return ((t.evaluate('x',time)+note['x']*math.cos(angle))*1350,(t.evaluate('y',time)+note['x']*1.5*math.sin(angle))*900)
        result=audit_contacts(m,max_witnesses,rapid_repeat_ms,target)
        result.update(chart_ref=chart_ref,digest=digest,travel_units='1350x900 logical adapter canvas; actual viewport dependent')
        return result

    def authoring_check(self, chart_ref: str, digest: str) -> dict:
        """Read schema, role-aware geometry and raw-role findings without mutation."""
        m=self._get(chart_ref,digest)['model']; self._basic_check(m)
        result=self._compact_findings(m); result.update(chart_ref=chart_ref,digest=digest); return result

    def _compact_findings(self,m,limit=20,cursor=0,code=None):
        d=globals()['digest'](m)
        if d not in self.check_cache: self.check_cache[d]=self._findings(m)
        full=self.check_cache[d]; rows=full['findings']
        counts={}
        for row in rows: counts[row['code']]=counts.get(row['code'],0)+1
        selected=[r for r in rows if code is None or r['code']==code]
        out={k:copy.deepcopy(v) for k,v in full.items() if k!='findings'}
        out.update(findings=copy.deepcopy(selected[cursor:cursor+limit]),total_findings=len(rows),finding_counts=counts,next_cursor=cursor+limit if cursor+limit<len(selected) else None)
        return out

    def authoring_diagnostics(self, chart_ref: str, digest: str, code: str|None=None, limit: int=20, cursor: int=0) -> dict:
        """Read cached bounded diagnostic witnesses by code and page; max100."""
        if not 1<=limit<=100 or cursor<0: raise ValueError('Diagnostic limit1..100/cursor>=0')
        m=self._get(chart_ref,digest)['model']; return self._compact_findings(m,limit,cursor,code)

    def chart_export(self, chart_ref: str, digest: str, format: str='phigros_v3', output_path: str|None=None) -> dict:
        """Export new officialv3/native6 JSON plus persistent authoring sidecar.

        Never overwrite files. Without output_path returns JSON data. Native
        adapter includes alpha*255, x*1350, y*900 and speed*9/2. No editor needed.
        """
        m=self._get(chart_ref,digest)['model']; self._basic_check(m)
        if format not in ('phigros_v3','phichain_native'): raise ValueError('Unsupported format')
        if format=='phigros_v3' and not m['dirty'] and m.get('source'):
            exported=copy.deepcopy(m['source']['official'])
        elif format=='phigros_v3': exported=official_export(m)
        else: exported=native_export(m)
        refs=set()
        for recipe in m.get('phrase_recipes',[]):
            section=recipe.get('section') or {}
            refs.update(section.get('evidence_refs',[]))
        sidecar=dict(authoring_sidecar_version=1,model=copy.deepcopy(m),model_digest=digest,cached_evidence={r:copy.deepcopy(self.evidence[r]) for r in refs if r in self.evidence})
        result=dict(format=format,chart_ref=chart_ref,digest=digest,note_count=sum(len(l['notes']) for l in m['lines']),line_count=len(m['lines']),limitations=['No gameplay/playability proof','Native adapter may quantize beats within1e-6beat; linear events only'])
        if output_path is None:
            result.update(chart=exported,sidecar=sidecar); return result
        path=Path(output_path).expanduser()
        if not path.is_absolute() or path.suffix.lower()!='.json' or not path.parent.is_dir(): raise ValueError('Output needs absolute new .json in existing directory')
        sidepath=path.with_name(path.stem+'.authoring.json')
        if path.exists() or sidepath.exists(): raise ValueError('Export path/sidecar already exists; never overwrite')
        # Both payloads serialized before either write; files created exclusively.
        payload=json.dumps(exported,ensure_ascii=False,indent=2,allow_nan=False)+'\n'
        annotation=json.dumps(sidecar,ensure_ascii=False,indent=2,allow_nan=False)+'\n'
        with path.open('x') as f: f.write(payload)
        try:
            with sidepath.open('x') as f: f.write(annotation)
        except OSError as error:
            raise ValueError(f'Chart exported at{path}, sidecar failed; inspect files before retry: {error}') from error
        result.update(output_path=str(path),sidecar_path=str(sidepath),export_sha256=hashlib.sha256(payload.encode()).hexdigest())
        return result



    def evidence_from_chart(self, chart_ref: str, digest: str, start: str, end: str,
                            audio_ref: str|None=None, audio_step: str='1/4', audio_tolerance_ms: float=25) -> dict:
        """Cache coordinate-free phrase articulation from exact chart IR, returning compact reference.

        start/end are half-open exact musical beats. Imported per-line BPM is already
        decoded to exact chart seconds; global tempo converts durations, offset maps audio.
        Carry-in holds are separate evidence, never fabricated onset heads. Max1000 heads,
        1000 carry-in holds,2000 audio lattice candidates; split oversized phrases.
        Optional audio_ref joins cached measured register peaks without audio in model context.
        """
        m=self._get(chart_ref,digest)['model'];first,last=q(start),q(end)
        if not 0<=first<last<=1000000:raise ValueError('Evidence beat range must increase within0..1000000')
        step=q(audio_step)
        if step<=0:raise ValueError('audio_step must be positive exact beats')
        finite(audio_tolerance_ms,'audio tolerance ms',0,1000)
        a,b=time_at(first,m['tempo_map']),time_at(last,m['tempo_map']);offset=q(m['offset_ms'])/1000
        grouped={};carry=[];head_count=0
        for line in m['lines']:
            for note in line['notes']:
                head,tail=q(note['time']),q(note['end'])
                if a<=head<b:
                    head_count+=1
                    if head_count>1000:raise ValueError('More than1000 source heads; split evidence phrase')
                    beat=beat_at(head,m['tempo_map'])
                    row=grouped.setdefault(head,dict(beat=float(beat),beat_exact=str(beat),chart_seconds_exact=str(head),audio_seconds_exact=str(head+offset),
                        kind_evidence=[],source_head_count=0,sustained_durations=[],sustained_durations_exact=[]))
                    row['source_head_count']+=1
                    if KINDS[note['kind']] not in row['kind_evidence']:row['kind_evidence'].append(KINDS[note['kind']])
                    if note['kind']=='hold':
                        duration=beat_at(tail,m['tempo_map'])-beat
                        row['sustained_durations'].append(float(duration));row['sustained_durations_exact'].append(str(duration))
                elif note['kind']=='hold' and head<a<tail:
                    head_beat,tail_beat=beat_at(head,m['tempo_map']),beat_at(tail,m['tempo_map'])
                    carry.append(dict(source_note_id=note['id'],source_line_id=line['id'],head_beat_exact=str(head_beat),tail_beat_exact=str(tail_beat),
                        remaining_beats_exact=str(tail_beat-first),head_chart_seconds_exact=str(head),tail_chart_seconds_exact=str(tail),
                        role='already active sustained object; no new phrase head'))
                    if len(carry)>1000:raise ValueError('More than1000 carry-in holds; split/review evidence phrase')
        events=[grouped[t] for t in sorted(grouped)]
        for event in events:event['kind_evidence'].sort()
        candidates=[]
        if audio_ref:
            from .audio_analysis import load_audio_analysis,match_register_peaks
            audio=load_audio_analysis(audio_ref)
            index=-(-first//step);count=max(0,int(-(-(last-index*step)//step)))
            if count>2000:raise ValueError('More than2000 audio candidates; split phrase or enlarge audio_step')
            for i in range(count):
                beat=(index+i)*step;chart_time=time_at(beat,m['tempo_map']);audio_time=chart_time+offset
                if audio_time<0:continue
                matches=match_register_peaks(audio,float(audio_time),audio_tolerance_ms)
                if matches:candidates.append(dict(beat=float(beat),beat_exact=str(beat),audio_seconds_exact=str(audio_time),register_evidence=matches,already_in_source=chart_time in grouped))
        analysis=dict(beat_start=float(first),beat_end=float(last),beat_start_exact=str(first),beat_end_exact=str(last),
            source_articulation_events=events,quantized_audio_attack_candidates=candidates,carry_in_sustains=carry,
            source_coordinates_excluded=True,source_offset_ms=m['offset_ms'],source_tempo_map=copy.deepcopy(m['tempo_map']),
            limitations=['Source note types are articulation evidence, not identified instruments','Objects and carry-in holds are not inferred independent contacts','Carry-in evidence does not fabricate target-chart heads or finger ownership'])
        settings=dict(method='chart-ir-articulation-v1',source_hash_kind='immutable model digest',start_beat_exact=str(first),end_beat_exact=str(last),
            audio_ref=audio_ref,audio_step_exact=str(step),audio_tolerance_ms=audio_tolerance_ms)
        result=self.evidence_register(digest,settings,analysis,'phigros-chart-mcp authoritative IR extraction v1')
        result['limitations']=['Derived from authoritative chart IR; coordinates excluded','Audio peak matches corroborate timing, not musical instrument/attack truth','Carry-in holds are evidence, not fabricated new heads']
        result['summary']=dict(source_heads=head_count,grouped_onsets=len(events),carry_in_holds=len(carry),audio_candidates=len(candidates),
            start_beat_exact=str(first),end_beat_exact=str(last),source_coordinates_excluded=True)
        return result

    def audio_analyze(self, source_path: str, settings: dict|None=None) -> dict:
        """Optional cached audio register-peak/RMS analysis; return compact audio_ref only.

        Requires optional audio dependencies and ffmpeg. Source is local absolute path;
        no download or instrument recognition. Cache is keyed by file hash/settings/producer.
        Chart-only tools never import audio dependencies. Join via evidence_from_chart(audio_ref).
        """
        path=Path(source_path)
        if not path.is_absolute() or not path.is_file():raise ValueError('Audio analysis needs absolute existing file')
        try:
            from .audio_analysis import analyze_audio
            return analyze_audio(str(path),settings)
        except ImportError as error:raise ValueError('Optional audio dependencies unavailable; install package[analysis] and ffmpeg') from error

    def evidence_import(self, source_path: str) -> dict:
        """Import a local precomputed evidence record by absolute path, returning compact cache reference.

        JSON fields source_sha256/settings/producer/analysis are validated exactly as
        evidence_register. Max file 256KiB; no download, audio analysis or model payload dump.
        """
        path=Path(source_path)
        if not path.is_absolute() or not path.is_file(): raise ValueError('Absolute existing evidence file required')
        if path.stat().st_size>262144: raise ValueError('Evidence file exceeds 256KiB; split phrases')
        record=json.loads(path.read_text())
        keys(record,('source_sha256','settings','producer','analysis'),('source_sha256','settings','producer','analysis'))
        return self.evidence_register(**record)

    def evidence_register(self, source_sha256: str, settings: dict, analysis: dict, producer: str) -> dict:
        """Cache caller-supplied measured evidence by source/settings/content hash.

        Does not run FFT or claim to hear audio. Maximum256KiB input. Registered
        evidence can supply compact onset beats to a phrase; persist via sidecar.
        """
        clean_json(settings); clean_json(analysis)
        if len(source_sha256)!=64 or any(c not in '0123456789abcdef' for c in source_sha256): raise ValueError('source_sha256 must be lowercase64hex')
        if not producer or len(producer)>1000: raise ValueError('Producer/version required')
        record=dict(source_sha256=source_sha256,settings=copy.deepcopy(settings),analysis=copy.deepcopy(analysis),producer=producer)
        if len(json.dumps(record))>262144: raise ValueError('Evidence exceeds256KiB')
        ref='evidence://'+globals()['digest'](record)
        reused=ref in self.evidence; self.evidence[ref]=record
        return dict(evidence_ref=ref,cached=reused,fields=list(analysis),limitations=['Caller supplied evidence; this server did not analyze/hear audio'])

    def evidence_get(self, evidence_ref: str, fields: list[str], limit: int=100, cursor: int=0) -> dict:
        """Read selected cached analysis fields with bounded array pagination."""
        if evidence_ref not in self.evidence: raise ValueError('Evidence unavailable; re-register or import sidecar')
        if not 1<=limit<=1000 or cursor<0 or not 1<=len(fields)<=10: raise ValueError('Bounded fields/limit/cursor required')
        row=self.evidence[evidence_ref]; values={}; counts={}
        for field in fields:
            if field not in row['analysis']: raise ValueError('Unknown evidence field')
            v=row['analysis'][field]
            if isinstance(v,list): values[field]=copy.deepcopy(v[cursor:cursor+limit]); counts[field]=len(v)
            else: values[field]=copy.deepcopy(v)
        return dict(evidence_ref=evidence_ref,source_sha256=row['source_sha256'],producer=row['producer'],values=values,total_lengths=counts,cursor=cursor,limit=limit)

    def chart_revision_restore(self, chart_ref: str, digest: str, previous_digest: str) -> dict:
        """Restore known offline revision under current digest; no file/editor undo.

        Revisions are process-local. Persistent recipe/sidecar exports allow
        restart/reimport and replay; no automatic source-file overwrite.
        """
        self._get(chart_ref,digest)
        key=(chart_ref,previous_digest)
        if key not in self.revisions: raise ValueError('Revision unavailable in this process; import checkpoint or replay recipe')
        result=self._store(self.revisions[key],chart_ref); result['restored_from']=digest; return result

    def authoring_presets(self, name: str='index') -> dict:
        """Read compact reusable structured motifs; edit beats/lanes/roles explicitly."""
        presets={
          'four_key_stair':dict(intent='four ordered stations; execution hypothesis, not persistent4K proof',operation=dict(type='insert_pattern',line_id='main',pattern='stairs',start='0',count=32,step='1/4',lane_x=[-.28,-.1,.1,.28],order=[0,1,2,3],kind='tap')),
          'two_hand_answer':dict(intent='alternating discrete attacks with breathing room',operation=dict(type='insert_pattern',line_id='main',pattern='alternating',start='0',count=8,step='1/2',lane_x=[-.2,.2],kind='tap')),
          'connected_thread':dict(intent='one travelling contact, not one finger per drag',operation=dict(type='insert_pattern',line_id='main',pattern='drag_path',start='0',count=12,step='1/8',lane_x=[-.25,.25],kind='drag')),
          'sustained_pair':dict(intent='two sustained roles; annotate ownership and add answers only to free contacts',operation=dict(type='insert_pattern',line_id='main',pattern='chord',start='0',count=1,step='1',lane_x=[-.28,.28],kind='hold',duration='4'))}
        if name=='index': return dict(presets=[dict(name=k,intent=v['intent']) for k,v in presets.items()],defaults='one main playing anchor; modifiers are phrase-specific')
        if name not in presets: raise ValueError('Unknown preset; request index')
        return copy.deepcopy(presets[name])

    def authoring_reference(self, category: str='index') -> dict:
        """Read one bounded portable reference lesson, no full corpus dependency."""
        from importlib.resources import files
        data=json.loads(files('phigros_chart_mcp').joinpath('resources/references.json').read_text())
        if category=='index': return {'categories':[{'name':key,'lesson':row['lesson'],'chart':row.get('chart'),'window_seconds':row.get('window_seconds')} for key,row in data.items()],'retrieval':'Request one category; source corpus not required'}
        if category not in data: raise ValueError('Unknown category; choices: '+', '.join(data))
        return copy.deepcopy(data[category])


def segments(events, channels, end):
    """Sweep event boundaries once; refuse ambiguous overlapping export intervals."""
    cuts={Fraction(0),end}; compiled=CompiledTracks(events)
    for e in events:
        if e['channel'] in channels:
            cuts.update(v for v in (q(e['start']),q(e['end'])) if 0<v<end)
    ordered=sorted(cuts)
    states={c: {'next':0,'active':[]} for c in channels}
    for a,b in zip(ordered,ordered[1:]):
        mid=(a+b)/2; values=[]
        for channel in channels:
            rows,starts,ends,_=compiled.channels[channel]; state=states[channel]
            while state['next']<len(rows) and starts[state['next']]<=mid:
                i=state['next']; heapq.heappush(state['active'],(ends[i],i)); state['next']+=1
            while state['active'] and state['active'][0][0]<=mid: heapq.heappop(state['active'])
            if len(state['active'])>1: raise ValueError(f'Overlapping {channel} events; edited export ambiguous')
            if state['active']:
                i=state['active'][0][1];e=rows[i];start,stop=starts[i],ends[i]
                f=lambda t: e['a']+(e['b']-e['a'])*float((t-start)/(stop-start))
                values.append((f(a),f(b)))
            else:
                v=compiled.evaluate(channel,mid);values.append((v,v))
        yield a,b,values



def source_floor(line,time):
    """Honor explicit source floor bases, integrate missing bases cumulatively."""
    src=line.get('source_line')
    if not src: return scroll_distance(line['events'],time)
    rows=sorted(src.get('speedEvents',[]),key=lambda e:q(e['startTime']))
    bpm=q(src['bpm'])
    def original_at(t):
        previous=None
        for e in rows:
            start=q(e['startTime'])*Fraction(15,8)/bpm
            if start>t: break
            if 'floorPosition' in e: base=float(e['floorPosition'])
            elif previous:
                old,oldstart,oldbase=previous
                base=oldbase+float(start-oldstart)*old['value']
            else: base=scroll_distance(line['events'],max(Fraction(0),start))
            previous=(e,start,base)
        if previous:
            e,start,base=previous
            return base+float(t-start)*e['value']
        return scroll_distance(line['events'],t)
    if 'speed' not in line.get('edited_channels',[]): return original_at(time)
    return original_at(Fraction(0))+scroll_distance(line['events'],time)


def official_export(m):
    out=copy.deepcopy(m['source']['official']) if m.get('source') else dict(formatVersion=3,offset=0,judgeLineList=[])
    out['offset']=m['offset_ms']/1000; out['judgeLineList']=[]
    for l in m['lines']:
        if l.get('source_line') and not l.get('edited'):
            out['judgeLineList'].append(copy.deepcopy(l['source_line'])); continue
        bpm=l.get('source_bpm',m['tempo_map'][0]['bpm'])
        raw=lambda t: float(t*q(bpm)*Fraction(8,15))
        end=max((q(n['end']) for n in l['notes']),default=Fraction(0))+2
        end=max(end,max((q(e['end']) for e in l['events'] if q(e['end'])<Fraction(86400)),default=end))
        row=copy.deepcopy(l.get('source_line',{})); row.update(bpm=bpm,notesAbove=[],notesBelow=[],speedEvents=[],judgeLineMoveEvents=[],judgeLineRotateEvents=[],judgeLineDisappearEvents=[])
        edited_channels=l.get('edited_channels',[])
        for channels,key in [(('speed',),'speedEvents'),(('x','y'),'judgeLineMoveEvents'),(('rotation',),'judgeLineRotateEvents'),(('opacity',),'judgeLineDisappearEvents')]:
            if l.get('source_line') and not any(c in edited_channels for c in channels):
                row[key]=copy.deepcopy(l['source_line'].get(key,[])); continue
            for a,b,values in segments(l['events'],channels,end):
                e=dict(startTime=raw(a),endTime=raw(b))
                if key=='speedEvents': e.update(value=values[0][0],floorPosition=source_floor(l,a))
                elif key=='judgeLineMoveEvents': e.update(start=values[0][0]+.5,end=values[0][1]+.5,start2=values[1][0]+.5,end2=values[1][1]+.5)
                else: e.update(start=values[0][0],end=values[0][1])
                row[key].append(e)
        for n in sorted(l['notes'],key=lambda n:(q(n['time']),n['x'])):
            at,endtime=q(n['time']),q(n['end'])
            if n.get('hold_speed_unresolved') and (n.get('edited') or 'speed' in edited_channels): raise ValueError('Cannot faithfully regenerate opaque hold-speed source; edit refused')
            speed=n['speed']*evaluate(l['events'],'speed',at) if n['kind']=='hold' else n['speed']
            if n.get('source_note') and not n.get('edited') and 'speed' not in edited_channels:
                row['notesAbove' if n['side']=='above' else 'notesBelow'].append(copy.deepcopy(n['source_note'])); continue
            e=copy.deepcopy(n.get('source_note',{})); e.update(type=KINDS[n['kind']],time=raw(at),positionX=n['x']*18,holdTime=raw(endtime-at) if n['kind']=='hold' else 0,speed=speed,floorPosition=source_floor(l,at))
            row['notesAbove' if n['side']=='above' else 'notesBelow'].append(e)
        row['numOfNotes']=len(l['notes']); out['judgeLineList'].append(row)
    out['numOfNotes']=sum(len(l['notes']) for l in m['lines'])
    return out


def native_export(m):
    out=dict(format=6,offset=m['offset_ms'],bpm_list=[dict(beat=triple(p['beat']),bpm=p['bpm']) for p in m['tempo_map']],lines=[])
    for l in m['lines']:
        if l.get('opaque_events'): raise ValueError('Native source compatibility: reversed source events cannot be represented faithfully')
        row=dict(name=l['name'],notes=[],events=[],children=[],curve_note_tracks=[])
        end=max((q(n['end']) for n in l['notes']),default=Fraction(0))+2
        end=max(end,max((q(e['end']) for e in l['events'] if q(e['end'])<Fraction(86400)),default=end))
        for channel in CHANNELS:
            multiplier={'x':1350,'y':900,'rotation':1,'opacity':255,'speed':4.5}[channel]
            for a,b,values in segments(l['events'],(channel,),end):
                v,w=(v*multiplier for v in values[0]); start=triple(beat_at(a,m['tempo_map'])); stop=triple(beat_at(b,m['tempo_map']))
                if start==stop: raise ValueError('Native event quantization collapses interval')
                val=dict(type='constant',value=v) if v==w else dict(type='transition',start=v,end=w,easing=dict(type='linear'))
                row['events'].append(dict(kind=channel,start_beat=start,end_beat=stop,value=val))
        for n in sorted(l['notes'],key=lambda n:(q(n['time']),n['x'])):
            if n['speed']<=0 or n.get('hold_speed_unresolved'): raise ValueError('Native adapter cannot faithfully encode imported nonpositive/unresolved note speed')
            at=beat_at(n['time'],m['tempo_map']); tail=beat_at(n['end'],m['tempo_map'])
            note=dict(kind=n['kind'],above=n['side']=='above',beat=triple(at),x=n['x']*1350,speed=n['speed'])
            if n['kind']=='hold': note['hold_beat']=triple(tail-at)
            row['notes'].append(note)
        out['lines'].append(row)
    return out
