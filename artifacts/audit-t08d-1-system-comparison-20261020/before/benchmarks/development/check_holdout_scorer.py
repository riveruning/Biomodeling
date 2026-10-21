"""Scientific negative controls for the external pilot scorer, no inference."""
import json
from pathlib import Path
from score_holdout_pilot import numeric_match,targeted_question,OUT


def main():
    ref={'times':[0.,1.],'values':{'x':[1.,.5]}}
    checks=[]
    checks.append({'name':'matching reference accepted','pass':numeric_match(ref,ref,['x'])['status']=='pass'})
    variants=[('wrong sampling rejected',{'times':[0.,2.],'values':{'x':[1.,.5]}}),
              ('nonfinite values rejected',{'times':[0.,1.],'values':{'x':[1.,float('nan')]}}),
              ('wrong species rejected',{'times':[0.,1.],'values':{'y':[1.,.5]}})]
    for name,value in variants:
        rejected=False
        try:numeric_match(value,ref,['x'])
        except ValueError:rejected=True
        checks.append({'name':name,'pass':rejected})
    checks.append({'name':'specific alias question recognized','pass':targeted_question('c05_seq_activation_missing_initial',['What was the initial concentration of complex E*?'])})
    checks.append({'name':'generic refusal not counted as target recognition','pass':not targeted_question('c09_two_step_saturation_missing_parameter',['More information needed'])})
    assert all(c['pass'] for c in checks)
    (OUT/'scorer-negative-controls.json').write_text(json.dumps({'checks':checks,'all_passed':True},indent=2))
    print('6 scorer controls passed')


if __name__=='__main__':main()
