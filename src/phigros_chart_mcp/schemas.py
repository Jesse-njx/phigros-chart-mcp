"""Discoverable strict JSON Schemas for tools and discriminated operations."""
import inspect
from typing import get_type_hints
from pydantic import ConfigDict, create_model
from .core import Engine


def obj(properties,required):
    return dict(type='object',properties=properties,required=required,additionalProperties=False)

BEAT={'oneOf':[{'type':'string','minLength':1,'maxLength':100},{'type':'number','minimum':0},
              {'type':'array','items':{'type':'integer'},'minItems':3,'maxItems':3}],
      'description':'Exact beat string/integer or canonical[whole,numerator,denominator]; no negative authored beats'}
NUMBER={'type':'number'}
IDENT={'type':'string','minLength':1,'maxLength':256}
ANNOTATION={'type':'object','description':'Bounded JSON voice/hand/finger/gesture/rationale/evidence annotations, persisted in sidecar'}
NOTE=obj({'kind':{'enum':['tap','drag','hold','flick']},'beat':BEAT,'x':NUMBER,'side':{'enum':['above','below']},'speed':{'type':'number','exclusiveMinimum':0},'duration':BEAT,'annotation':ANNOTATION,'id':IDENT},['kind','beat','x'])
NOTE['allOf']=[{'if':{'properties':{'kind':{'const':'hold'}}},'then':{'required':['duration']},'else':{'not':{'required':['duration']}}}]
pose={k:NUMBER for k in ('x','y','rotation','speed')};pose['opacity']={'type':'number','minimum':0,'maximum':1}
line={'line_id':IDENT}
OPS=[]
OPS.append(obj({'type':{'const':'create_line'},'id':IDENT,'name':IDENT,'role':{'enum':['playing_anchor','note_carrier','decorative']},'anchor_id':IDENT,**pose},['type','id','name','role']))
OPS.append(obj({'type':{'const':'annotate_line'},**line,'role':{'enum':['playing_anchor','note_carrier','decorative','unclassified']},'anchor_id':IDENT,'annotation':ANNOTATION},['type','line_id','role']))
OPS.append(obj({'type':{'const':'insert_notes'},**line,'notes':{'type':'array','items':NOTE,'minItems':1,'maxItems':1000}},['type','line_id','notes']))
pattern=obj({'type':{'const':'insert_pattern'},**line,'pattern':{'enum':['stairs','alternating','chord','drag_path']},'start':BEAT,'count':{'type':'integer','minimum':1,'maximum':1000},'step':BEAT,'beats':{'type':'array','items':BEAT,'minItems':1,'maxItems':1000},'lane_x':{'type':'array','items':NUMBER,'minItems':1,'maxItems':10,'uniqueItems':True},'order':{'type':'array','items':{'type':'integer','minimum':0,'maximum':9},'minItems':1,'maxItems':100},'kind':{'enum':['tap','drag','hold','flick']},'duration':BEAT,'side':{'enum':['above','below']},'speed':{'type':'number','exclusiveMinimum':0},'annotation':ANNOTATION},['type','line_id','pattern','lane_x'])
pattern['anyOf']=[{'required':['beats']},{'required':['start','count','step']}];OPS.append(pattern)
OPS.append(obj({'type':{'const':'transform_notes'},**line,'start':BEAT,'end':BEAT,'mirror':{'type':'boolean'},'shift':NUMBER,'scale':{'type':'number','exclusiveMinimum':0},'speed_multiplier':{'type':'number','exclusiveMinimum':0},'beat_shift':{'oneOf':[{'type':'string'},{'type':'number'}]},'kind':{'enum':['tap','drag','flick']},'side':{'enum':['above','below']}},['type','line_id','start','end']))
OPS.append(obj({'type':{'const':'set_effect'},**line,'channel':{'enum':['x','y','rotation','opacity','speed']},'start':BEAT,'end':BEAT,'a':NUMBER,'b':NUMBER,'easing':{'const':'linear'},'replace':{'type':'boolean'}},['type','line_id','channel','start','end','a']))
OPS.append(obj({'type':{'const':'delete_notes'},**line,'ids':{'type':'array','items':IDENT,'minItems':1,'maxItems':1000,'uniqueItems':True}},['type','line_id','ids']))
OPS.append(obj({'type':{'const':'update_note'},**line,'id':IDENT,'note':NOTE},['type','line_id','id','note']))
OPS.append(obj({'type':{'const':'move_note'},**line,'id':IDENT,'target_line_id':IDENT,'x':NUMBER},['type','line_id','id','target_line_id','x']))

annotation_op=obj({'type':{'const':'annotate_notes'},**line,'ids':{'type':'array','items':IDENT,'minItems':1,'maxItems':1000,'uniqueItems':True},'start':BEAT,'end':BEAT,'annotation_patch':ANNOTATION},['type','line_id','annotation_patch'])
annotation_op['oneOf']=[{'required':['ids'],'not':{'anyOf':[{'required':['start']},{'required':['end']}]}},{'required':['start','end'],'not':{'required':['ids']}}];OPS.append(annotation_op)

TOOL_NAMES=('chart_capabilities','chart_create','chart_import','chart_context','chart_read','authoring_plan','authoring_apply','authoring_check','chart_export','authoring_diagnostics','evidence_register','evidence_get','chart_revision_restore','authoring_presets','authoring_reference','authoring_plan_phrase','evidence_import','authoring_contacts','evidence_from_chart','audio_analyze')
READ_ONLY={'chart_capabilities','chart_context','chart_read','authoring_check','authoring_diagnostics','evidence_get','authoring_presets','authoring_reference','authoring_contacts'}

def tool_schema(name):
    method=getattr(Engine,name); annotations=get_type_hints(method); properties={}
    for k,p in inspect.signature(method).parameters.items():
        if k=='self':continue
        properties[k]=(annotations.get(k,object),p.default if p.default is not inspect.Parameter.empty else ...)
    schema=create_model(name+'_Input',__config__=ConfigDict(extra='forbid'),**properties).model_json_schema()
    if name=='authoring_plan': schema['properties']['operations']={'type':'array','items':{'oneOf':OPS},'minItems':1,'maxItems':1000}
    if name=='evidence_from_chart':
        for key in ('start','end'):schema['properties'][key]=BEAT
        schema['properties']['audio_step']=dict(BEAT,default='1/4')
    if name=='authoring_plan_phrase':
        config={'id':IDENT,'motif':IDENT,'purpose':IDENT,'source_kinds':{'type':'array','items':{'enum':[1,2,3,4]},'maxItems':4},
                'finger_order':{'type':'array','items':{'enum':['L_middle','L_index','R_index','R_middle']},'minItems':1,'maxItems':4,'uniqueItems':True},
                'hold_mode':{'enum':['dyad','source_pair_or_single','alternating']},'drag_mode':{'const':'thread'},
                'merge_sustains':{'type':'boolean'},'articulation':{'const':'paired_release'},'pulse_register':{'enum':['low_register','middle_register','high_register']}}
        config['allowed_fingers']={'type':'array','items':{'enum':['L_middle','L_index','R_index','R_middle']},'minItems':1,'maxItems':4,'uniqueItems':True}
        for key in ('hold_fingers','drag_fingers'):
            config[key]={'type':'array','items':{'enum':['L_middle','L_index','R_index','R_middle']},'minItems':1,'maxItems':2,'uniqueItems':True}
        for key in ('hold_threshold','hold_max','hold_release_trim','drag_min_step','drag_piece_max','drag_bend','pulse_step','accent_grid'):
            config[key]={'type':'number','minimum':0}
        schema['properties']['config']=obj(config,['id','motif','purpose'])
        fingers=['L_middle','L_index','R_index','R_middle']
        schema['properties']['lanes']={'anyOf':[obj({f:{'type':'number','minimum':-.5,'maximum':.5} for f in fingers},fingers),{'type':'null'}],'default':None}
        schema['properties']['defaults']={'anyOf':[obj({'release_guard_beats':{'type':'number','minimum':0,'maximum':8},'note_speed':{'type':'number','exclusiveMinimum':0}},[]),{'type':'null'}],'default':None}
    if name=='chart_create':
        schema['properties']['metadata']=obj({k:{'type':'string'} for k in ('title','composer','charter','difficulty_label','rationale')},['title'])
        schema['properties']['main_line']={'anyOf':[obj(pose,[]),{'type':'null'}],'default':None}
    return schema
