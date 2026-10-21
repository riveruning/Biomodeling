"""Freeze supported imported-SBML tasks, separate from paper reconstruction."""
from hashlib import sha256
import json
import math
from pathlib import Path

from biomodel_agent import modeling
from biomodel_agent.domain.contracts import ModelDraft, State, Parameter, Reaction
from competitor_common import OUT, ROOT, write_json


def prepare():
    dest=ROOT/'benchmarks/inputs/competitor-tasks';dest.mkdir(parents=True,exist_ok=True)
    draft=ModelDraft('decay',states=(State('P',1.,'mole_per_litre','concentration'),),
        parameters=(Parameter('kdeg_P',.3,'unknown','analyst_specified'),),
        reactions=(Reaction('decay_P',{'P':1.},{},'kdeg_P*P'),),time_unit='second')
    path=dest/'decay.xml';path.write_text(modeling.build_model(draft).sbml)
    times=[i/2 for i in range(21)]
    reference=json.loads((ROOT/'benchmarks/development/references/BIOMD0000000010.json').read_text())
    cases={
        'decay':{'sbml':str(path.relative_to(ROOT)),'times':times,'species':['P'],
                 'reference':{'P':[math.exp(-.3*t) for t in times]},'scales':{'P':1.},
                 'prompt':'Using the uploaded SBML model, simulate from time 0 through 10 seconds with exactly 20 equal output intervals (21 output points including both endpoints). Keep all initial values and parameters unchanged. Retain the numerical time-course data for P. Do not fetch another model or use external sources. No plot or secondary question analysis is requested.'},
        'mapk':{'sbml':'benchmarks/inputs/public/BIOMD0000000010.xml','times':reference['times'],
                'species':list(reference['values']),'reference':reference['values'],'scales':reference['scales'],
                'prompt':'Using the uploaded SBML model, simulate from time 0 through 1800 with exactly 120 equal output intervals (121 output points including both endpoints). Keep all initial values and parameters unchanged. Retain numerical time-course data for all dynamic species. Do not fetch another model or use external sources. No plot or secondary question analysis is requested.'}}
    for name,case in cases.items():
        case['sbml_sha256']=sha256((ROOT/case['sbml']).read_bytes()).hexdigest()
        folder=dest/name;folder.mkdir(exist_ok=True)
        (folder/'model.xml').write_bytes((ROOT/case['sbml']).read_bytes())
        (folder/'TASK.txt').write_text(case['prompt']+'\n')
        case['public_folder']=str(folder.relative_to(ROOT))
    protocol={'schema_version':'1','cases':cases,'threshold':.001,
        'source_scope':'imported SBML; separate from text-to-model track',
        'systems':{'talk2biomodels':'official get_app with alternative qwen backend; original prompt/tools unmodified',
                   'codex':'native CLI fresh sessions, independent Python solution',
                   'biomodel_core':'deterministic API numerical control, not natural-language Agent score'},
        'limits':['two development inputs, one attempt per agent task','different models/harness budgets; not strict equal-budget','BioModel product Agent has no direct SBML import action; API control is not a matched Agent baseline','T2B publication-date source snapshot, compatible installed dependencies, original benchmark environment not exactly recreated']}
    write_json(OUT/'sbml-protocol.json',protocol)
    print('Frozen imported-SBML tasks: decay and public MAPK.')


if __name__=='__main__':prepare()
