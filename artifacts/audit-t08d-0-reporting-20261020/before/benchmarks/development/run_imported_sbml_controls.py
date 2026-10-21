"""Product numerical API controls, explicitly not natural-language Agent scores."""
import json
from biomodel_agent import application
from biomodel_agent.domain.contracts import ExperimentSpec, ModelArtifact
from biomodel_agent.storage import ArtifactStore
from competitor_common import OUT, ROOT, write_json


def main():
    protocol=json.loads((OUT/'sbml-protocol.json').read_text())
    for name,case in protocol['cases'].items():
        dest=OUT/'biomodel_core'/name;dest.mkdir(parents=True,exist_ok=True)
        store=ArtifactStore(dest/'store')
        ref=application.run_reproduction(store,
            ModelArtifact(name,(ROOT/case['sbml']).read_text()),
            ExperimentSpec(tuple(case['times'])),use_cache=False)
        result=store.get(ref)['payload']
        write_json(dest/'result.json',{'run_ref':ref,'status':result['status'],
                    'scope':'deterministic_API_numerical_control_not_Agent_baseline',
                    'scientific_status':result['scientific_status']})
        write_json(dest/'trajectory.json',result['simulation'])
        print(name,result['status'],flush=True)


if __name__=='__main__':main()
