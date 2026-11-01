"""Conservative FBC v2 import; unsupported dynamic semantics are rejected."""
import math
import io
import re
import xml.etree.ElementTree as ET
import libsbml
from .fba import validate, MAX_BYTES, MAX_METABOLITES, MAX_REACTIONS, MAX_NONZERO


def parse(data):
    if not data or len(data)>MAX_BYTES or b'<!DOCTYPE' in data.upper() or b'<!ENTITY' in data.upper():
        raise ValueError('FBC XML size or declaration is unsupported')
    for _,(_,uri) in ET.iterparse(io.BytesIO(data),events=('start-ns',)):
        if re.search(r'/sbml/level3/version[0-9]+/[^/]+/version[0-9]+',uri) and uri!='http://www.sbml.org/sbml/level3/version1/fbc/version2':
            raise ValueError('unsupported SBML package namespace')
    doc=libsbml.readSBMLFromString(data.decode('utf-8'))
    errors=[doc.getError(i) for i in range(doc.getNumErrors())]
    if any(e.getSeverity()>=libsbml.LIBSBML_SEV_ERROR for e in errors):raise ValueError('FBC document has SBML parse errors')
    model=doc.getModel()
    if model is None or model.getLevel()!=3:raise ValueError('only SBML Level 3 FBC v2 is supported')
    fbc=model.getPlugin('fbc')
    if fbc is None or fbc.getPackageVersion()!=2 or not fbc.getStrict():raise ValueError('FBC v2 strict=true is required')
    if any(model.getPlugin(i).getPackageName()!='fbc' for i in range(model.getNumPlugins())):
        raise ValueError('additional SBML packages are unsupported')
    if (model.getNumRules() or model.getNumEvents() or model.getNumInitialAssignments() or
        model.getNumConstraints() or model.getNumFunctionDefinitions()):
        raise ValueError('dynamic rules/events/assignments/constraints/functions are not FBA inputs')
    if model.getNumSpecies()>MAX_METABOLITES or model.getNumReactions()>MAX_REACTIONS:
        raise ValueError('FBC model dimensions exceed supported budgets')
    if (model.getNumParameters()>5000 or model.getNumUnitDefinitions()>64 or model.getNumCompartments()>1000
        or fbc.getNumGeneProducts()>5000 or fbc.getNumObjectives()>32):
        raise ValueError('FBC metadata dimensions exceed supported budgets')
    doc.checkConsistency()
    errors=[doc.getError(i) for i in range(doc.getNumErrors())]
    if any(e.getSeverity()>=libsbml.LIBSBML_SEV_ERROR for e in errors):
        raise ValueError('FBC document has unsupported or inconsistent SBML semantics')
    metabolites=[dict(id=s.getId(),boundary_condition=s.getBoundaryCondition(),compartment=s.getCompartment())
                 for s in model.getListOfSpecies()]
    reactions=[];units=set();bound_refs={};nonzero=0
    for reaction in model.getListOfReactions():
        if reaction.isSetKineticLaw() or reaction.getNumModifiers():raise ValueError('kinetic laws/modifiers cannot be silently dropped for FBA')
        plugin=reaction.getPlugin('fbc')
        if plugin is None or not plugin.isSetLowerFluxBound() or not plugin.isSetUpperFluxBound():raise ValueError('both FBC flux bounds must be explicit')
        bounds=[];refs=[]
        for key,lower in ((plugin.getLowerFluxBound(),True),(plugin.getUpperFluxBound(),False)):
            p=model.getParameter(key)
            if p is None or not p.getConstant() or not p.isSetValue():raise ValueError('FBC bound must name a constant numeric parameter')
            value=p.getValue()
            if math.isnan(value) or (math.isinf(value) and ((lower and value>0) or (not lower and value<0))):
                raise ValueError('invalid FBC infinite bound direction')
            bounds.append(None if math.isinf(value) else value)
            refs.append(key);units.add(p.getUnits() if p.isSetUnits() else 'unknown')
        stoich={}
        for sign,participants in ((-1.,reaction.getListOfReactants()),(1.,reaction.getListOfProducts())):
            for species_ref in participants:
                if species_ref.isSetStoichiometryMath() or not species_ref.getConstant():raise ValueError('variable stoichiometry is unsupported')
                value=species_ref.getStoichiometry()
                if not species_ref.isSetStoichiometry() or not math.isfinite(value) or value<=0:raise ValueError('stoichiometry must be explicit, positive and finite')
                species=species_ref.getSpecies()
                stoich[species]=stoich.get(species,0.)+sign*value
                nonzero+=1
                if nonzero>MAX_NONZERO:raise ValueError('FBC participant budget exceeded')
        stoich={s:v for s,v in stoich.items() if v!=0.}
        reactions.append(dict(id=reaction.getId(),stoichiometry=stoich,lower_bound=bounds[0],upper_bound=bounds[1]))
        bound_refs[reaction.getId()]=refs
    if len(units)!=1:raise ValueError('mixed or partly missing flux units require explicit preprocessing')
    objective=fbc.getObjective(fbc.getActiveObjectiveId())
    if objective is None:raise ValueError('one explicit active FBC objective is required')
    coefficients={}
    for item in objective.getListOfFluxObjectives():
        if not item.isSetCoefficient() or not math.isfinite(item.getCoefficient()):raise ValueError('invalid FBC objective coefficient')
        rid=item.getReaction();coefficients[rid]=coefficients.get(rid,0.)+item.getCoefficient()
    normalized=validate(dict(schema_version=1,kind='fba',id=model.getId(),formulation='steady_state',flux_unit=next(iter(units)),
        metabolites=metabolites,reactions=reactions,objective=dict(sense=objective.getType(),coefficients=coefficients)))
    return normalized,dict(adapter='libsbml_fbc_v2',libsbml_version=libsbml.getLibSBMLDottedVersion(),
        active_objective=objective.getId(),bound_parameter_refs=bound_refs,
        excluded_boundary_species=[m['id'] for m in metabolites if m['boundary_condition']],
        unit_definitions=[libsbml.writeSBMLToString(_unit_document(u)) for u in model.getListOfUnitDefinitions()],
        warnings=[e.getMessage()[:300] for e in errors][:20],
        limitations=['Only the declared active linear objective and fixed bounds are used.',
                     'Gene-product associations and chemical annotations are preserved in the original blob, not interpreted as interventions.'])


def _unit_document(unit):
    doc=libsbml.SBMLDocument(3,1);model=doc.createModel();model.setId('units_metadata');model.addUnitDefinition(unit)
    return doc
