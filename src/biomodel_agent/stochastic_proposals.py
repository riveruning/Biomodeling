"""Literal source-to-model checks for finite integer mass-action SSA drafts.

The mathematical authority is the existing stochastic backend. This profile
does not infer reactions, convert concentration constants or validate biology.
"""
from decimal import Decimal, InvalidOperation
import re

from . import model_contracts as c, stochastic

PROFILE='literal_stochastic_declarations_v1'
NAME=r'[A-Za-z][A-Za-z0-9_]{0,47}'
NUMBER=r'(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]{1,4})?'
INITIAL=re.compile(r'Initial ('+NAME+r')\s*=\s*(0|[1-9][0-9]{0,9})\.?')
REACTION=re.compile(r'Reaction ('+NAME+r')\s*:\s*(.*?)\s*->\s*(.*?)\.?')
RATE=re.compile(r'Stochastic rate ('+NAME+r')\s*=\s*('+NUMBER+r')\.?')
RATE_UNIT=re.compile(r'Stochastic rate unit ('+NAME+r')\s*=\s*(1/(?:second|minute|hour))\.?')
TIME=re.compile(r'Time unit\s*=\s*(second|minute|hour)\.?')
FIXED={
    'State unit':('state_unit','molecule_count'),
    'Rate convention':('rate_convention','stochastic_combinations'),
    'Mixing':('mixing','well_mixed'),
    'Kinetics':('kinetics','time_homogeneous')}
MENTION=re.compile(r'\b(Initial|Reaction|Stochastic rate unit|Stochastic rate)\s+('+NAME+r')\b'
                   r'|\b(Time unit|State unit|Rate convention|Mixing|Kinetics)\s*(?:=|:)')


def _side(text,names):
    text=text.strip()
    if text=='0':return {}
    out={}
    for token in re.split(r'\s*\+\s*',text):
        m=re.fullmatch(r'(?:([12])\s+)?('+NAME+r')',token.strip())
        if m is None or m[2] not in names:raise ValueError('unsupported stoichiometry or unknown species')
        out[m[2]]=out.get(m[2],0)+int(m[1] or 1)
    if not out or sum(out.values())>2:raise ValueError('only elementary reaction sides of total order 0..2 are supported')
    return out


def _declaration(line,names):
    text=line.strip()
    if m:=INITIAL.fullmatch(text):
        return m[1]+'/initial',c.integer(int(m[2]),0,10**9,'initial molecule count')
    if m:=REACTION.fullmatch(text):
        left,right=_side(m[2],names),_side(m[3],names)
        if left==right:raise ValueError('null reactions are outside the profile')
        return m[1]+'/reaction',dict(reactants=left,products=right)
    if m:=RATE_UNIT.fullmatch(text):return m[1]+'/rate_unit',m[2]
    if m:=RATE.fullmatch(text):
        if len(m[2])>80:raise ValueError('source rate literal exceeds the numeric profile')
        try:value=Decimal(m[2])
        except InvalidOperation as exc:raise ValueError('invalid source rate') from exc
        if not value.is_finite() or not 0<=value<=Decimal(10**9):raise ValueError('source rate outside bounded SSA range')
        return m[1]+'/rate',value
    if m:=TIME.fullmatch(text):return 'time_unit',m[1]
    for label,(target,value) in FIXED.items():
        if re.fullmatch(re.escape(label)+r'\s*=\s*'+re.escape(value)+r'\.?',text):return target,value
    return None


def _expected(model,rate_units):
    stochastic.validate(model)
    if len(model['species'])>8 or len(model['reactions'])>16:
        raise ValueError('source-bound SSA drafts support at most 8 species and 16 reactions')
    if model['units']['time'] not in ('second','minute','hour'):
        raise ValueError('source-bound SSA time must be second, minute or hour; no unit conversion')
    ids={r['id'] for r in model['reactions']}
    if not isinstance(rate_units,dict) or set(rate_units)!=ids:
        raise ValueError('supply stochastic_rate_units for every reaction exactly')
    unit='1/'+model['units']['time']
    if any(v!=unit for v in rate_units.values()):
        raise ValueError('each stochastic rate unit must match inverse model time under stochastic_combinations')
    result=dict(time_unit=model['units']['time'],state_unit='molecule_count',
        rate_convention='stochastic_combinations',mixing='well_mixed',kinetics='time_homogeneous')
    for species in model['species']:result[species['id']+'/initial']=species['initial']
    for reaction in model['reactions']:
        rid=reaction['id']
        result[rid+'/reaction']=dict(reactants=reaction['reactants'],products=reaction['products'])
        result[rid+'/rate']=Decimal(str(reaction['rate']))
        result[rid+'/rate_unit']=rate_units[rid]
    return result


def inspect(store,model,rate_units,evidence,evidence_map,source_refs):
    """Recheck literal values, explicit units/assumptions and complete scope."""
    try:expected=_expected(model,rate_units)
    except (ValueError,KeyError,TypeError) as exc:
        return dict(state='blocked',issues=[dict(target='model/units',message=str(exc))],scientific_support='unknown')
    names={s['id'] for s in model['species']}
    issues=[]
    def issue(target,message):issues.append(dict(target=target,message=message))
    if not isinstance(evidence_map,dict) or set(evidence_map)!=set(expected):
        actual=set(evidence_map) if isinstance(evidence_map,dict) else set()
        extra=actual-set(expected)
        return dict(state='blocked',issues=[dict(target='evidence_map',
            message='Map every initial, reaction, stochastic rate/unit, time/state unit, rate convention, mixing and kinetics declaration exactly.',
            missing_targets=sorted(set(expected)-actual),extra_targets=[str(x)[:120] for x in sorted(extra,key=str)[:16]],
            extra_target_count=len(extra))],scientific_support='unknown')
    # Every allowed source is a complete raw text snapshot; no unsupported
    # extraction, source subset or hidden page can certify absence of conflicts.
    texts=[];size=0
    for source_ref in source_refs:
        source=store.get(source_ref)['payload']
        try:
            if source.get('parser')!='utf8' or len(source['pages'])!=1:raise ValueError('complete UTF-8 text snapshots are required')
            text=source['pages'][0]['text']
            if store.get_blob(source['blob_ref']).decode('utf-8')!=text:raise ValueError('source text disagrees with its raw snapshot')
            size+=len(text)
            if size>64000:raise ValueError('source scope exceeds 64000 characters')
            texts.append(text)
        except (ValueError,KeyError,TypeError) as exc:issue('sources',str(exc))
    by_id={a['id']:a for a in evidence}
    for target,value in expected.items():
        anchors=evidence_map[target]
        if (not isinstance(anchors,list) or not 1<=len(anchors)<=4 or any(not isinstance(x,str) for x in anchors)
                or len(set(anchors))!=len(anchors)):
            issue(target,'Each target needs 1..4 distinct literal source evidence IDs.');continue
        for eid in anchors:
            a=by_id.get(eid)
            if a is None or a.get('kind')!='source' or a.get('source_ref') not in source_refs:
                issue(target,'This target requires authorized source text, not goal/model evidence.');continue
            try:
                from .evidence import _source_page_text
                text=_source_page_text(store,a['source_ref'],a['page'])
                c.integer(a['start'],0,len(text),'start');c.integer(a['end'],a['start']+1,len(text),'end')
                if text[a['start']:a['end']]!=a['quote']:raise ValueError('citation slice mismatch')
                start=text.rfind('\n',0,a['start'])+1;end=text.find('\n',a['end'])
                whole=text[start:len(text) if end<0 else end].strip()
                if whole!=a['quote'].strip():raise ValueError('cite the complete declaration line, including qualifiers')
                parsed=_declaration(a['quote'],names)
                if parsed is None or parsed[0]!=target or parsed[1]!=value:raise ValueError('source declaration differs from target/value/units')
            except (ValueError,KeyError,TypeError) as exc:issue(target,str(exc))
    seen=set();declared_species=set();declared_reactions=set()
    if size<=64000:
        for text in texts:
            for line in text.splitlines():
                mention=MENTION.search(line)
                if not mention:continue
                if mention[1]=='Initial':declared_species.add(mention[2])
                elif mention[1]=='Reaction':declared_reactions.add(mention[2])
                try:parsed=_declaration(line,names)
                except ValueError:parsed=None
                if parsed is None:
                    issue(mention[2] or mention[3],'A relevant declaration is qualified or outside the supported grammar.');continue
                target,value=parsed;seen.add(target)
                if target not in expected or value!=expected[target]:
                    issue(target,'The authorized scope contains an omitted, mismatched or conflicting declaration.')
        if declared_species!=names:issue('species','Source-declared and proposed species must match exactly.')
        if declared_reactions!={r['id'] for r in model['reactions']}:issue('reactions','Source-declared and proposed reaction channels must match exactly.')
        for target in expected:
            if target not in seen:issue(target,'A complete supported declaration is missing.')
    binding=dict(version=1,profile=PROFILE,rate_units=rate_units,rate_convention='stochastic_combinations',
        mixing='well_mixed',kinetics='time_homogeneous',
        unit_interpretation='inverse model time per distinct reactant combination; counts are discrete combinatorial counts; no concentration conversion')
    return dict(state='source_bound' if not issues else 'blocked',issues=issues,source_scope_characters=size,
        binding=binding,scientific_support='unknown',
        limitations=['Literal declarations only; no reaction inference or concentration/volume conversion.',
            'Units checked only within the explicitly supplied stochastic-combinations convention.',
            'Recognized declarations are scanned across the complete bounded text scope, not arbitrary natural-language meaning.',
            'Finite seeded samples are not distributional validation or biological certification.'])

