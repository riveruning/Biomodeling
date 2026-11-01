"""Bounded literal source checks for proposed, unclamped Boolean networks.

This profile checks explicit declarations, not inferred regulatory mechanisms.
The existing Boolean backend remains the mathematical/runtime authority.
"""
import ast
import re

from . import boolean

NAME=r'[A-Za-z][A-Za-z0-9_]{0,47}'
INITIAL=re.compile(r'Initial ('+NAME+r')\s*=\s*(True|False)\.?')
RULE=re.compile(r'Rule ('+NAME+r')\s*=\s*(.*?)\.?')
UPDATE=re.compile(r'Update\s*=\s*(synchronous|random_sequential)\.?')
TIME=re.compile(r'Time unit\s*=\s*sweep\.?')
CLAMPS=re.compile(r'Clamps\s*=\s*none\.?')
MENTION=re.compile(r'\b(Initial|Rule)\s+('+NAME+r')\b|\b(Update|Time unit|Clamps)\s*(?:=|:)')


def _declaration(line,names):
    text=line.strip()
    if m:=INITIAL.fullmatch(text):return m[1]+'/initial',m[2]=='True'
    if m:=RULE.fullmatch(text):
        # A final sentence period is not part of the expression.
        expression=m[2].strip()
        return m[1]+'/rule',ast.dump(boolean.parse_rule(expression,names))
    if m:=UPDATE.fullmatch(text):return 'update',m[1]
    if TIME.fullmatch(text):return 'time_unit','sweep'
    if CLAMPS.fullmatch(text):return 'clamps','none'
    return None


def inspect(store,model,evidence,evidence_map,source_refs):
    """Read-only recheck of every initial/rule and finite whole-source scope."""
    issues=[]
    def issue(target,message):issues.append(dict(target=target,message=message))
    try:
        boolean.validate(model)
        if len(model['nodes'])>16 or model['clamps']:
            raise ValueError('source-bound Boolean proposals support 1..16 nodes and no clamps')
    except (ValueError,TypeError,KeyError) as exc:
        return dict(state='blocked',issues=[dict(target='model',message=str(exc))],scientific_support='unknown')
    names={n['id'] for n in model['nodes']}
    expected={'update':model['update'],'time_unit':'sweep','clamps':'none'}
    for n in model['nodes']:
        expected[n['id']+'/initial']=n['initial']
        expected[n['id']+'/rule']=ast.dump(boolean.parse_rule(n['rule'],names))
    if not isinstance(evidence_map,dict) or set(evidence_map)!=set(expected):
        actual=set(evidence_map) if isinstance(evidence_map,dict) else set()
        extra=actual-set(expected)
        return dict(state='blocked',issues=[dict(target='evidence_map',message='Map every initial, rule, update, time_unit and clamps target exactly once.',
            missing_targets=sorted(set(expected)-actual),extra_targets=[str(x)[:120] for x in sorted(extra,key=str)[:16]],
            extra_target_count=len(extra))],scientific_support='unknown')
    by_id={e['id']:e for e in evidence}
    for target,value in expected.items():
        anchors=evidence_map[target]
        if (not isinstance(anchors,list) or not 1<=len(anchors)<=4
                or any(not isinstance(x,str) for x in anchors) or len(set(anchors))!=len(anchors)):
            issue(target,'Each target needs 1..4 distinct source evidence IDs.');continue
        for eid in anchors:
            anchor=by_id.get(eid)
            if anchor is None or anchor['kind']!='source':
                issue(target,'A model value/rule requires literal authorized source evidence.');continue
            from .evidence import _source_page_text
            text=_source_page_text(store,anchor['source_ref'],anchor['page'])
            start=text.rfind('\n',0,anchor['start'])+1
            end=text.find('\n',anchor['end'])
            whole=text[start:len(text) if end<0 else end].strip()
            if whole!=anchor['quote'].strip():
                issue(target,'Cite the complete declaration line, including any qualifiers.');continue
            try:parsed=_declaration(anchor['quote'],names)
            except ValueError:parsed=None
            if parsed is None or parsed[0]!=target or parsed[1]!=value:
                issue(target,'The cited complete declaration does not match this target/value/rule.')
    texts=[];size=0
    for ref in source_refs:
        source=store.get(ref)['payload']
        if (source.get('parser')!='utf8' or len(source['pages'])!=1
                or store.get_blob(source['blob_ref']).decode('utf-8')!=source['pages'][0]['text']):
            issue('sources','Source-bound Boolean adoption currently requires complete UTF-8 text snapshots; PDF/other adapters need a reviewed transcription.')
        for page in source['pages']:
            text=page['text'];size+=len(text)
            if size>64000:
                issue('sources','Source scope exceeds the 64000-character conflict-check profile.');break
            texts.append(text)
        if size>64000:break
    if size<=64000:
        seen={};declared=set()
        for text in texts:
            for line in text.splitlines():
                mention=MENTION.search(line)
                if not mention:continue
                if mention[2]:declared.add(mention[2])
                try:parsed=_declaration(line,names)
                except ValueError:parsed=None
                if parsed is None:
                    issue(mention[2] or mention[3],'A relevant source line is qualified or outside the supported declaration grammar.');continue
                target,value=parsed
                if target not in expected or value!=expected[target]:
                    issue(target,'The authorized source scope contains an omitted or conflicting declaration.')
                seen.setdefault(target,[]).append(value)
        if declared!=names:issue('nodes','Source-declared nodes and proposed nodes must match exactly.')
        for target in expected:
            if target not in seen:issue(target,'No complete supported source declaration for this target.')
    return dict(state='source_bound' if not issues else 'blocked',issues=issues,
                source_scope_characters=size,scientific_support='unknown',
                limitations=['Literal declaration/AST comparison only; no biological rule inference.',
                    'Whole authorized scope checked for recognized conflicts; ambiguous/qualified declarations block adoption.'])
