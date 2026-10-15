"""Reusable evidence-to-operation motifs; never reads/writes native chart formats.

The MCP backend alone creates revisions, validates, applies and exports notes.
A small model chooses compact phrase parameters, not thousands of coordinates.
"""
from fractions import Fraction
import statistics

FINGERS=('L_middle','L_index','R_index','R_middle')
def exact(v):return str(Fraction(str(v)))
def portable(v):
    if isinstance(v,Fraction):return str(v)
    if isinstance(v,dict):return {k:portable(x) for k,x in v.items()}
    if isinstance(v,list):return [portable(x) for x in v]
    return v
def compile_phrase(config,evidence,recipe,scheduling_state=None):
    exact_mode='beat_start_exact' in evidence
    def timing(value):return Fraction(str(value)) if exact_mode else value
    start,end=timing(evidence.get('beat_start_exact',evidence['beat_start'])),timing(evidence.get('beat_end_exact',evidence['beat_end']))
    lanes=recipe['lanes'];guard=timing(recipe['defaults']['release_guard_beats'])
    if config['motif']=='rest':return [],dict(id=config['id'],motif='rest',head_count=0,purpose=config['purpose'],source_range=portable([start,end]))
    state=scheduling_state if scheduling_state is not None else {}
    notes=[];busy={f:list(state.get('busy',{}).get(f,[])) for f in FINGERS};occupied={f:[] for f in FINGERS};serial=0
    last_strike={f:state.get('last_strike',{}).get(f,float('-inf')) for f in FINGERS}
    events=[]
    for event in evidence['source_articulation_events']:
        row=dict(event);row['beat']=timing(row.get('beat_exact',row['beat']))
        row['sustained_durations']=[timing(x) for x in row.get('sustained_durations_exact',row['sustained_durations'])]
        events.append(row)
    audio={timing(e.get('beat_exact',e['beat'])):e for e in evidence['quantized_audio_attack_candidates']}
    hold_heads=set();thread_number=0
    allowed=set(config.get('allowed_fingers',FINGERS))
    hold_fingers=config.get('hold_fingers',[f for f in ('L_middle','R_middle','L_index','R_index') if f in allowed][:2])
    drag_fingers=config.get('drag_fingers',[f for f in ('L_index','R_index','L_middle','R_middle') if f in allowed][:2])
    for name,fs in (('hold_fingers',hold_fingers),('drag_fingers',drag_fingers)):
        if not fs or len(set(fs))!=len(fs) or not set(fs)<=allowed:raise ValueError(name+' must name permitted distinct fingers')
    if config.get('hold_mode') in ('dyad','source_pair_or_single') and len(hold_fingers)<2:raise ValueError('Paired hold mode requires two hold_fingers; use alternating for one sustained contact')

    def free(f,b):return f in allowed and not any(a-1e-8<=b<z-1e-8 for a,z in busy[f])
    def add(kind,b,f,voice,rationale,duration=None,x=None,gesture=None,provenance=None):
        nonlocal serial
        serial+=1
        a=dict(phrase=config['id'],motif=config['motif'],voice=voice,hand=f[0],finger=f.split('_')[1],finger_key=f,
               gesture=gesture or f'{config["id"]}-{kind}-{serial}',rationale=rationale,evidence=provenance or {},authored_by='parameterized_motif_client_v1')
        n=dict(kind=kind,beat=exact(b),x=lanes[f] if x is None else round(x,9),side='above',speed=recipe['defaults']['note_speed'],annotation=portable(a))
        if duration is not None:n['duration']=exact(duration)
        notes.append(n)
        if kind!='drag':last_strike[f]=max(last_strike[f],b)
        if kind=='hold':occupied[f].append((b,b+duration));busy[f].append((b,b+duration+guard))
        elif kind=='flick':busy[f].append((b,b+guard))
        return n
    mode=config.get('hold_mode');hold_ordinal=0
    if mode:
        sustain_events=events
        if config.get('merge_sustains'):
            sustain_events=[]
            for e in events:
                ds=[d for d in e['sustained_durations'] if d>=config.get('hold_threshold',1)]
                if not ds:continue
                if sustain_events and abs(sustain_events[-1]['beat']+sustain_events[-1]['sustained_durations'][0]-e['beat'])<1e-8:
                    sustain_events[-1]['sustained_durations'][0]+=max(ds)
                    sustain_events[-1]['merged_articulation_heads'].append(e['beat'])
                else:sustain_events.append(dict(e,sustained_durations=[max(ds)],merged_articulation_heads=[e['beat']]))
        for e in sustain_events:
            ds=sorted([d for d in e['sustained_durations'] if d>=config.get('hold_threshold',1)],reverse=True)
            if not ds:continue
            b=e['beat'];cap=timing(config.get('hold_max',max(ds)));chosen=[]
            if mode=='dyad':chosen=[(hold_fingers[0],ds[0]),(hold_fingers[1],ds[0])]
            elif mode=='source_pair_or_single':chosen=[(hold_fingers[0] if len(ds)>1 else hold_fingers[hold_ordinal%len(hold_fingers)],ds[0])]+([(hold_fingers[1],ds[1])] if len(ds)>1 else [])
            else:chosen=[(hold_fingers[hold_ordinal%len(hold_fingers)],ds[0])]
            if any(not free(f,b) or b-last_strike[f]<guard-1e-8 for f,d in chosen):continue
            made=False
            for f,d in chosen:
                duration=min(d,cap,end-b)
                duration-=timing(config.get('hold_release_trim',0))
                # Staccato holds retain at least quarter-beat duration, longer notes leave a real release interval.
                duration=max(timing(.25),duration)
                if b+duration>end or not free(f,b):continue
                add('hold',b,f,'sustained_chord' if mode=='dyad' or len(chosen)>1 else 'occupied_punctuation',
                    'Source sustained articulation; new fixed outer contact role, with source-supported tail and explicit release scheduling.'+(' Dyad is a deliberate bimanual sustained-chord arrangement, not a claim of two identified instruments.' if mode=='dyad' else ''),duration=duration,
                    provenance=dict(tier='official_sustain_timing',beat=b,source_durations=e['sustained_durations'],merged_articulation_heads=e.get('merged_articulation_heads',[b])))
                made=True
            if made:hold_heads.add(b);hold_ordinal+=1
    if config.get('drag_mode'):
        drag=[e['beat'] for e in events if 2 in e['kind_evidence']]
        groups=[]
        for b in drag:
            if not groups or b-groups[-1][-1]>.75:groups.append([b])
            else:groups[-1].append(b)
        for g in groups:
            if len(g)<2:continue
            step=timing(config.get('drag_min_step',.25));selected=[g[0]]
            for b in g[1:]:
                if b-selected[-1]>=step-1e-8:selected.append(b)
            if selected[-1]!=g[-1]:selected.append(g[-1])
            # Bound a continuous finger thread to four beats before a genuine handoff/reset.
            pieces=[]
            for b in selected:
                if not pieces or b-pieces[-1][0]>config.get('drag_piece_max',4):pieces.append([b])
                else:pieces[-1].append(b)
            for piece in pieces:
                if len(piece)<2:continue
                thread_number+=1;f=drag_fingers[(thread_number-1)%len(drag_fingers)];a,z=piece[0],piece[-1]
                def thread_free(f):
                    return f in allowed and not any(a<stop-1e-8 and begin<z+guard-1e-8 for begin,stop in busy[f])
                if not thread_free(f):
                    alternatives=[other for other in drag_fingers if thread_free(other)]
                    if not alternatives:continue
                    f=alternatives[0]
                gesture=f'{config["id"]}-thread-{thread_number}';center=lanes[f]
                for i,b in enumerate(piece):
                    progress=i/max(1,len(piece)-1);bend=(1-abs(2*progress-1))*config.get('drag_bend',.02)
                    x=center+bend*(1 if f[0]=='L' else -1)
                    add('drag',b,f,'connected_figure','Source connected articulation is sampled into one travelling contact; no fresh strike assumed per drag.',x=x,gesture=gesture,
                        provenance=dict(tier='official_connected_timing',beat=b,source_thread_range=[g[0],g[-1]],sampling_min_step_beats=step))
                occupied[f].append((a,z+timing(.125)));busy[f].append((a,z+guard))
    # Hold scheduling precedes attacks, so reset strike history to the previous phrase;
    # upcoming holds are separately reserved by the chooser's approach/release margin.
    last_strike={f:state.get('last_strike',{}).get(f,float('-inf')) for f in FINGERS}
    order=config.get('finger_order',list(FINGERS));cursor=0
    def choose(b,count=1):
        nonlocal cursor
        available=[]
        preference=order[cursor:]+order[:cursor]+[f for f in FINGERS if f not in order]
        for f in preference:
            soon_hold=any(c['kind']=='hold' and c['annotation']['finger_key']==f and b<float(Fraction(c['beat']))<b+guard-1e-8 for c in notes)
            if f not in available and free(f,b) and b-last_strike[f]>=guard-1e-8 and not soon_hold:available.append(f)
        available.sort(key=lambda f:(0 if f in order else 1,last_strike[f],preference.index(f)))
        result=available[:count]
        if result and result[-1] in order:cursor=(order.index(result[-1])+1)%len(order)
        return result
    foreground={e['beat']:e for e in events if set(config.get('source_kinds',[]))&set(e['kind_evidence']) and (e['beat']not in hold_heads or 1 in e['kind_evidence'])}
    new_pulse=set();step=timing(config.get('pulse_step')) if config.get('pulse_step') else None
    if step:
        register=config['pulse_register'];flux=[e['register_evidence'][register]['positive_flux'] for e in audio.values() if register in e['register_evidence']]
        threshold=statistics.median(flux) if flux else float('inf')
        lattice_count=sum(abs(e['beat']/step-round(e['beat']/step))<1e-8 for e in events if 1 in e['kind_evidence'] or 3 in e['kind_evidence'])
        for b,e in audio.items():
            m=e['register_evidence'].get(register)
            # Require established source pulse lattice, close digital timing and stronger-than-median register attack.
            if m and lattice_count>=3 and abs(b/step-round(b/step))<1e-8 and m['distance_ms']<=12 and m['positive_flux']>=threshold and b not in foreground and b not in hold_heads:
                foreground[b]=dict(beat=b,kind_evidence=[],source_head_count=0,sustained_durations=[]);new_pulse.add(b)
    for b,e in sorted(foreground.items()):
        grid=timing(config.get('accent_grid')) if config.get('accent_grid') else None;accent=bool(grid and abs(b/grid-round(b/grid))<1e-8 and (e['source_head_count']>=2 or config.get('hold_mode')=='dyad' or b in new_pulse))
        count=2 if accent else 1
        fingers=choose(b,count)
        if not fingers:continue
        kind='flick' if config.get('articulation')=='paired_release' else 'tap'
        if kind=='flick':fingers=choose(b,2) # statement is a single paired, pre-cued release.
        prov=dict(tier='audio_new_pulse_candidate' if b in new_pulse else 'official_attack_timing',beat=b,source_kind_evidence=e['kind_evidence'])
        if b in new_pulse:prov.update(register=config['pulse_register'],digital=audio[b]['register_evidence'][config['pulse_register']],acceptance='source pulse lattice + register timing/strength; no instrument identification claimed')
        for f in fingers:
            add(kind,b,f,'register_pulse' if b in new_pulse else 'foreground_attack',
                config['purpose']+(' Bimanual accent against occupied outer voices is intentional synchronization.' if len(fingers)==2 else ''),provenance=prov)
    # Selected releases use source timing only and do not steal a travelling/held finger.
    if config['motif']not in ('statement','sparse_contour','final_pedal_thread'):
        releases=[e for e in events if 4 in e['kind_evidence'] and e['beat']>=end-2]
        if releases:
            e=releases[-1];b=e['beat']
            if not any((Fraction(n['beat']) if exact_mode else float(Fraction(n['beat'])))==b for n in notes):
                fs=[f for f in choose(b,4) if not any(n['annotation']['finger_key']==f and b<float(Fraction(n['beat']))<b+guard for n in notes)][:1]
                if fs:add('flick',b,fs[0],'phrase_release','Selected source release accent; assigned only to a free finger with reserved exit window.',provenance=dict(tier='official_release_timing',beat=b))
    notes.sort(key=lambda n:(Fraction(n['beat']),FINGERS.index(n['annotation']['finger_key']),n['kind']))
    state['busy']=busy
    state['last_strike']={f:max([state.get('last_strike',{}).get(f,float('-inf'))]+[float(Fraction(n['beat'])) for n in notes if n['kind']!='drag' and n['annotation']['finger_key']==f]) for f in FINGERS}
    return [dict(type='insert_notes',line_id='main',notes=notes)] if notes else [],dict(id=config['id'],motif=config['motif'],purpose=config['purpose'],head_count=len(notes),kinds={k:sum(n['kind']==k for n in notes) for k in ['tap','drag','hold','flick']},new_audio_pulse_candidates=sum(n['annotation']['evidence']['tier']=='audio_new_pulse_candidate' for n in notes),source_range=portable([start,end]))
