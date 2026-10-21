"""Independent, hand-transcribed kinetics for execution-track acceptance.

These fixtures reference curated SBML, not original paper figure measurements.
They never call the application or libRoadRunner. Shared transcription errors
remain possible: numerical agreement alone cannot establish paper fidelity.
"""
from pathlib import Path
import json
import numpy as np
from scipy.integrate import solve_ivp

ROOT = Path(__file__).resolve().parents[2]


def repressilator(t, y):
    px, py, pz, x, yy, z = y
    kd = np.log(2) / 2
    kp = np.log(2) / 10
    tl = 20 * kd
    transcription = lambda p: .03 + 29.97 * 40**2 / (40**2 + p**2)
    return [tl*x-kp*px, tl*yy-kp*py, tl*z-kp*pz,
            transcription(pz)-kd*x, transcription(px)-kd*yy,
            transcription(py)-kd*z]


def toggle(t, y):
    u, v, iptg = y
    effective_u = u / (1 + iptg / 2.9618e-5)**2.0015
    return [156.25/(1+v**2.5)-u, 15.6/(1+effective_u)-v, 0.]


def mapk(t, y):
    a,b,c,d,e,f,g,h = y
    j0 = 2.5*a/((1+h/9)*(10+a))
    j1 = .25*b/(8+b)
    j2 = .025*b*c/(15+c)
    j3 = .025*b*d/(15+d)
    j4 = .75*e/(15+e)
    j5 = .75*d/(15+d)
    j6 = .025*e*f/(15+f)
    j7 = .025*e*g/(15+g)
    j8 = .5*h/(15+h)
    j9 = .5*g/(15+g)
    return [-j0+j1,j0-j1,-j2+j5,j2-j3+j4-j5,j3-j4,
            -j6+j9,j6-j7+j8-j9,j7-j8]


def markevich(t, y):
    m, mp, mpp, kinase, phosphatase = y
    j1 = .01*kinase*m/50/(1+m/50+mp/500)
    j2 = 15*kinase*mp/500/(1+m/50+mp/500)
    den = 1+mpp/22+mp/18+m/78
    j3 = .084*phosphatase*mpp/22/den
    j4 = .06*phosphatase*mp/18/den
    return [-j1+j4,j1-j2+j3-j4,j2-j3,0.,0.]


CASES = {
    'BIOMD0000000012': (repressilator, ['PX','PY','PZ','X','Y','Z'],
                         [0,0,0,0,20,0], 600., [1000,1000,1000,100,100,100]),
    'BIOMD0000000507': (toggle, ['species_1','species_2','species_3'],
                         [0,0,0], 100., [156.25,15.6,1.]),
    'BIOMD0000000010': (mapk, ['MKKK','MKKK_P','MKK','MKK_P','MKK_PP',
                              'MAPK','MAPK_P','MAPK_PP'],
                         [90,10,280,10,10,280,10,10], 1800.,
                         [100,100,300,300,300,300,300,300]),
    'BIOMD0000000027': (markevich, ['M','Mp','Mpp','MAPKK','MKP3'],
                         [500,0,0,50,100], 10000., [500,500,500,50,100]),
}


def generate(output=None):
    output = Path(output or ROOT/'benchmarks/development/references')
    output.mkdir(parents=True, exist_ok=True)
    summary = []
    for mid, (rhs, names, initial, end, scales) in CASES.items():
        times = np.linspace(0., end, 121)
        coarse = solve_ivp(rhs, (0., end), initial, method='DOP853',
                           t_eval=times, rtol=1e-10, atol=1e-12)
        fine = solve_ivp(rhs, (0., end), initial, method='Radau',
                         t_eval=times, rtol=1e-11, atol=1e-13)
        if not coarse.success or not fine.success:
            raise RuntimeError(f'{mid}: reference integration failed')
        error = float(np.max(np.abs(coarse.y-fine.y)/np.array(scales)[:,None]))
        if error > 1e-4:
            raise RuntimeError(f'{mid}: reference cross-method discrepancy {error}')
        data = {'schema_version':'1','track':'structured_execution',
                'model_id':mid,'times':times.tolist(),
                'values':{n:fine.y[i].tolist() for i,n in enumerate(names)},
                'scales':dict(zip(names,scales)), 'threshold':1e-3,
                'reference_methods':['DOP853','Radau'],
                'cross_method_error':error,
                'time_unit':'minute' if mid.endswith('0012') else
                    ('second' if mid.endswith('0507') else 'SBML Level 2 default second'),
                'limitations':['Hand transcription from curated SBML',
                               'Not original paper figure reproduction']}
        (output/f'{mid}.json').write_text(json.dumps(data,allow_nan=False,indent=2))
        summary.append({'model_id':mid,'cross_method_error':error})
    return summary


if __name__ == '__main__':
    print(json.dumps(generate(),indent=2))
