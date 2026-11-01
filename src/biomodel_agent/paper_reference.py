"""Retrospective, phase-free figure features; never a scientific pass certificate."""
from copy import deepcopy
from hashlib import sha256
import io
import json
import math
from pathlib import Path

import numpy as np
from scipy.signal import find_peaks
from .storage import canonical_bytes

MAX_PIXELS=16_000_000
MAX_BYTES=8_000_000


def digest(value):return sha256(canonical_bytes(value)).hexdigest()


def calibration(spec):
    fields={'version','image_sha256','image_size','x_pixels','time_range','y_pixels','value_range',
            'sample_x','legend_mask','pixel_uncertainty','series','excluded','source','review'}
    if not isinstance(spec,dict) or set(spec)!=fields or type(spec['version']) is not int or spec['version']!=1:
        raise ValueError('unsupported image calibration contract')
    p=deepcopy(spec)
    if not isinstance(p['image_sha256'],str) or len(p['image_sha256'])!=64 or any(c not in '0123456789abcdef' for c in p['image_sha256']):raise ValueError('invalid source image hash')
    size=p['image_size']
    if not isinstance(size,list) or len(size)!=2 or any(type(n) is not int or n<2 or n>6000 for n in size) or size[0]*size[1]>MAX_PIXELS:raise ValueError('image dimension budget exceeded')
    for key in ('x_pixels','time_range','y_pixels','value_range','sample_x'):
        v=p[key]
        if not isinstance(v,list) or len(v)!=2 or any(type(x) not in (int,float) or not math.isfinite(x) for x in v) or v[0]>=v[1]:raise ValueError('invalid calibration '+key)
    if not 0<=p['x_pixels'][0]<p['x_pixels'][1]<size[0] or not 0<=p['y_pixels'][0]<p['y_pixels'][1]<size[1]:raise ValueError('axes outside image')
    if not all(type(x) is int for x in p['sample_x']) or not p['x_pixels'][0]<p['sample_x'][0]<p['sample_x'][1]<p['x_pixels'][1]:raise ValueError('sample columns outside axes')
    mask=p['legend_mask']
    if not isinstance(mask,list) or len(mask)!=4 or any(type(x) is not int for x in mask) or not (0<=mask[0]<mask[2]<=size[0] and 0<=mask[1]<mask[3]<=size[1]):raise ValueError('invalid explicit legend mask')
    u=p['pixel_uncertainty']
    if type(u) not in (int,float) or not .5<=u<=5.:raise ValueError('pixel uncertainty must be declared in 0.5..5')
    if not isinstance(p['series'],dict) or not 1<=len(p['series'])<=8:raise ValueError('invalid curve count')
    for name,s in p['series'].items():
        if not isinstance(name,str) or not name or len(name)>32 or not isinstance(s,dict) or set(s)!={'channel','minimum','margins','value_roi','prominence','minimum_separation'}:raise ValueError('invalid curve specification')
        if type(s['channel']) is not int or s['channel'] not in (0,1,2) or type(s['minimum']) is not int or not 1<=s['minimum']<=255:raise ValueError('invalid color channel')
        margins=s['margins']
        if not isinstance(margins,list) or len(margins)!=3 or any(type(v) is not int or not 0<=v<=255 for v in margins) or margins[s['channel']]!=0:raise ValueError('invalid color margins')
        roi=s['value_roi']
        if not isinstance(roi,list) or len(roi)!=2 or any(type(v) not in (int,float) or not math.isfinite(v) for v in roi) or not p['value_range'][0]<=roi[0]<roi[1]<=p['value_range'][1]:raise ValueError('invalid curve ROI')
        for k in ('prominence','minimum_separation'):
            if type(s[k]) not in (int,float) or not math.isfinite(s[k]) or s[k]<=0:raise ValueError('invalid feature detection limit')
    if not isinstance(p['excluded'],dict) or any(not isinstance(k,str) or not isinstance(v,str) or not v for k,v in p['excluded'].items()):raise ValueError('invalid exclusions')
    if set(p['series'])&set(p['excluded']) or not isinstance(p['source'],dict) or not p['source'] or p['review']!='developer_only_independent_review_pending':raise ValueError('declare exclusions and pending independent review')
    return p


def digitize_rgb(rgb,spec):
    p=calibration(spec)
    a=np.asarray(rgb)
    if a.shape!=(p['image_size'][1],p['image_size'][0],3) or a.dtype!=np.uint8:raise ValueError('source must be original RGB8 dimensions')
    a=a.astype(np.int16)
    x0,x1=p['x_pixels'];y_top,y_bottom=p['y_pixels'];t0,t1=p['time_range'];v0,v1=p['value_range']
    ts=(t1-t0)/(x1-x0);vs=(v1-v0)/(y_bottom-y_top);u=p['pixel_uncertainty']
    xx=np.arange(a.shape[1])[None,:];yy=np.arange(a.shape[0])[:,None]
    values=v0+(y_bottom-yy)*vs
    mask=p['legend_mask'];occluded=(xx>=mask[0])&(xx<mask[2])&(yy>=mask[1])&(yy<mask[3])
    result={}
    for name,s in p['series'].items():
        color=a[:,:,s['channel']]
        eligible=color>=s['minimum']
        for channel,margin in enumerate(s['margins']):eligible &= color-a[:,:,channel]>=margin
        eligible &= ~occluded
        eligible &= (values>=s['value_roi'][0])&(values<=s['value_roi'][1])
        rows=[]
        for x in range(p['sample_x'][0],p['sample_x'][1]+1):
            pixels=np.flatnonzero(eligible[:,x]);t=t0+(x-x0)*ts
            if len(pixels):
                low=v0+(y_bottom-float(pixels.max())-u)*vs;high=v0+(y_bottom-float(pixels.min())+u)*vs
                rows.append(dict(pixel_x=x,time=t,time_low=t-u*ts,time_high=t+u*ts,value=v0+(y_bottom-float(np.median(pixels)))*vs,
                                 low=low,high=high,pixel_y_min=int(pixels.min()),pixel_y_max=int(pixels.max()),status='observed'))
            else:
                rows.append(dict(pixel_x=x,time=t,time_low=t-u*ts,time_high=t+u*ts,value=None,low=None,high=None,
                                 pixel_y_min=None,pixel_y_max=None,status='legend_or_unresolved' if mask[0]<=x<mask[2] else 'no_color_match'))
        result[name]=rows
    return result


def features(rows,*,prominence,minimum_separation):
    """No smoothing or interpolation. Never count periods across a missing segment."""
    if any(type(v) not in (int,float) or not math.isfinite(v) or v<=0 for v in (prominence,minimum_separation)):raise ValueError('invalid feature detection limits')
    if len(rows)<3:raise ValueError('insufficient feature samples')
    times=[r['time'] for r in rows]
    if any(type(t) not in (int,float) or not math.isfinite(t) for t in times) or any(b<=a for a,b in zip(times,times[1:])):raise ValueError('feature time must increase')
    observed=[];segments=[];segment=[]
    for r in rows:
        if r['value'] is None:
            if segment:segments.append(segment);segment=[]
            continue
        if any(type(r.get(k)) not in (int,float) or not math.isfinite(r[k]) for k in ('value','low','high','time_low','time_high')) or not r['low']<=r['value']<=r['high'] or not r['time_low']<=r['time']<=r['time_high']:raise ValueError('invalid observed interval')
        observed.append(r);segment.append(r)
    if segment:segments.append(segment)
    maxima=[];minima=[];periods=[]
    for seg_id,seg in enumerate(segments):
        if len(seg)<3:continue
        t=np.array([r['time'] for r in seg]);step=float(np.median(np.diff(t)))
        if not np.allclose(np.diff(t),step,rtol=1e-7,atol=1e-10):raise ValueError('irregular feature sampling is unsupported')
        v=np.array([r['value'] for r in seg])
        for sign,out in ((1.,maxima),(-1.,minima)):
            ix,properties=find_peaks(sign*v,prominence=prominence,distance=max(1,int(minimum_separation/step)),plateau_size=(1,None))
            current=[]
            for j,i in enumerate(ix):
                plateau=seg[int(properties['left_edges'][j]):int(properties['right_edges'][j])+1]
                record=dict(time=float(t[i]),time_low=plateau[0]['time_low'],time_high=plateau[-1]['time_high'],
                            value=float(v[i]),low=min(r['low'] for r in plateau),high=max(r['high'] for r in plateau),segment=seg_id)
                out.append(record);current.append(record)
            if sign<0:
                for left,right in zip(current,current[1:]):
                    periods.append(dict(value=right['time']-left['time'],low=right['time_low']-left['time_high'],high=right['time_high']-left['time_low'],segment=seg_id))
    def aggregate(records):
        if not records:return None
        return dict(value=float(np.median([r['value'] for r in records])),low=float(np.median([r['low'] for r in records])),
                    high=float(np.median([r['high'] for r in records])),n=len(records))
    return dict(minimum=aggregate(minima),maximum=aggregate(maxima),period=aggregate(periods),
                minima=minima,maxima=maxima,period_intervals=periods,observed=len(observed),missing=len(rows)-len(observed),
                note='Median extrema/within-segment trough spacings; stroke/calibration intervals are not confidence intervals.')


def _decode(raw,p):
    if len(raw)>MAX_BYTES:raise ValueError('reference image byte budget exceeded')
    if sha256(raw).hexdigest()!=p['image_sha256']:raise ValueError('reference image hash changed; recalibration required')
    try:from PIL import Image
    except ImportError as exc:raise ValueError('Pillow is required for image preparation; use the figures extra') from exc
    with Image.open(io.BytesIO(raw)) as im:
        if im.size!=tuple(p['image_size']) or im.mode not in ('RGB','P') or 'transparency' in im.info:raise ValueError('unexpected source dimensions or mode')
        rgb=np.asarray(im.convert('RGB'))
    return digitize_rgb(rgb,p)


def prepare_reference(store,image_path,spec):
    p=calibration(spec);path=Path(image_path)
    if path.stat().st_size>MAX_BYTES:raise ValueError('reference image byte budget exceeded')
    raw=path.read_bytes()
    curves=_decode(raw,p)
    metrics={name:features(rows,prominence=p['series'][name]['prominence'],minimum_separation=p['series'][name]['minimum_separation']) for name,rows in curves.items()}
    blob=store.put_blob(raw)
    return store.put('paper_figure_reference',dict(version=1,image_blob_ref=blob,calibration=p,curves=curves,features=metrics,
        excluded=p['excluded'],source=p['source'],scientific_support='unknown',independent_review='pending'),refs=(blob,))


def validate_reference(store,ref):
    obj=store.get(ref);p=obj['payload']
    if obj['kind']!='paper_figure_reference' or p['version']!=1 or p['image_blob_ref'] not in obj['refs']:raise ValueError('invalid figure reference binding')
    if sha256(store.get_blob(p['image_blob_ref'])).hexdigest()!=p['calibration']['image_sha256']:raise ValueError('source image binding changed')
    calibration(p['calibration'])
    if canonical_bytes(_decode(store.get_blob(p['image_blob_ref']),p['calibration']))!=canonical_bytes(p['curves']):raise ValueError('saved digitization differs from original image pixels')
    if set(p['curves'])!=set(p['calibration']['series']):raise ValueError('reference curve set changed')
    for name,rows in p['curves'].items():
        s=p['calibration']['series'][name]
        if canonical_bytes(features(rows,prominence=s['prominence'],minimum_separation=s['minimum_separation']))!=canonical_bytes(p['features'][name]):raise ValueError('reference features differ from saved samples')
    return p


def protocol(store,reference_ref,model_ref,spec):
    validate_reference(store,reference_ref);obj=store.get(model_ref)
    if obj['kind']!='model':raise ValueError('paper comparison requires a saved ODE model')
    required={'version','registration','prior_exposure','initials_origin','pointwise','phase_alignment','windows','stability_relative_tolerance','experiment','solver','nonnegative'}
    if not isinstance(spec,dict) or set(spec)!=required or type(spec['version']) is not int or spec['version']!=1:raise ValueError('invalid paper comparison protocol')
    if (spec['registration']!='retrospective_development' or not spec['prior_exposure'] or spec['initials_origin']!='analyst_exploration'
        or spec['pointwise']!='not_run_unknown_initials_and_phase' or spec['phase_alignment']!='none'):
        raise ValueError('this protocol cannot claim blind registration, known initials or fitted phase')
    windows=spec['windows']
    if not isinstance(windows,list) or len(windows)!=2 or any(not isinstance(w,list) or len(w)!=2 or any(type(x) not in (int,float) or not math.isfinite(x) for x in w) or w[0]>=w[1] for w in windows) or windows[0][1]!=windows[1][0]:raise ValueError('two adjacent diagnostic windows are required')
    if type(spec['stability_relative_tolerance']) not in (int,float) or not 0<spec['stability_relative_tolerance']<=.02:raise ValueError('invalid stability diagnostic limit')
    from .domain.contracts import ExperimentSpec,SolverConfig
    from .domain.serialization import from_dict,to_dict
    exp=from_dict(ExperimentSpec,spec['experiment']);solver=from_dict(SolverConfig,spec['solver'])
    if exp.initial_overrides or exp.parameter_overrides or exp.segments or exp.times[0]>windows[0][0] or exp.times[-1]<windows[-1][1]:raise ValueError('scientific overrides or uncovered windows are unsupported')
    if not isinstance(spec['nonnegative'],list) or any(not isinstance(s,str) for s in spec['nonnegative']):raise ValueError('invalid nonnegative targets')
    payload=dict(version=1,reference_ref=reference_ref,model_ref=model_ref,model_sha256=sha256(obj['payload']['sbml'].encode()).hexdigest(),
                 spec={**deepcopy(spec),'experiment':to_dict(exp),'solver':to_dict(solver)})
    return store.put('paper_comparison_protocol',payload,refs=(reference_ref,model_ref))


def compare(store,protocol_ref,run_ref):
    proto=store.get(protocol_ref)
    if proto['kind']!='paper_comparison_protocol':raise ValueError('a frozen paper comparison protocol is required')
    p=proto['payload'];s=p['spec'];reference=validate_reference(store,p['reference_ref'])
    if set(proto['refs'])!={p['reference_ref'],p['model_ref']} or protocol(store,p['reference_ref'],p['model_ref'],s)!=protocol_ref:raise ValueError('invalid frozen protocol binding')
    model=store.get(p['model_ref']);run=store.get(run_ref)
    if run['kind']!='run':raise ValueError('paper comparison requires an actual ODE run')
    r=run['payload'];inputs=store.get(r['input_ref'])
    expected=dict(artifact=model['payload'],experiment=s['experiment'],solver=s['solver'],reference=None,scales=None,
                  observations=[],strict=True,threshold=1e-3,nonnegative=s['nonnegative'])
    if (inputs['kind']!='simulation_input' or r['input_ref'] not in run['refs']
        or p['model_sha256']!=sha256(model['payload']['sbml'].encode()).hexdigest()
        or any(canonical_bytes(inputs['payload'].get(k))!=canonical_bytes(v) for k,v in expected.items())):
        raise ValueError('run differs from frozen paper protocol; no model, time, solver or threshold adjustment allowed')
    if r['status']!='succeeded' or any(c['status']=='fail' for k in ('static_validation','numeric_validation') for c in r[k]['checks']):raise ValueError('failed numerical run cannot support a paper comparison')
    if any(not any(c['code']==code and c['status']=='pass' for c in r['numeric_validation']['checks']) for code in ('finite','convergence')):raise ValueError('finite and convergence checks must actually pass')
    sim=r['simulation'];t=np.array(sim['times']);measurements={};stability=[];comparisons=[]
    for name,settings in reference['calibration']['series'].items():
        if name not in sim['values']:raise ValueError('required variable is missing')
        v=np.array(sim['values'][name])
        if len(v)!=len(t) or not np.isfinite(v).all():raise ValueError('invalid model series')
        by_window=[]
        for start,end in s['windows']:
            rows=[dict(time=float(x),time_low=float(x),time_high=float(x),value=float(y),low=float(y),high=float(y)) for x,y in zip(t,v) if start<=x<=end]
            f=features(rows,prominence=settings['prominence'],minimum_separation=settings['minimum_separation']);by_window.append(f)
        measurements[name]=by_window
        for feature in ('minimum','maximum','period'):
            a,b=[f[feature] for f in by_window];ref=reference['features'][name][feature]
            enough=bool(a and b and ref and a['n']>=2 and b['n']>=2 and ref['n']>=2)
            relative=abs(a['value']-b['value'])/max(abs(a['value']),abs(b['value']),1e-12) if enough else None
            stable=enough and relative<=s['stability_relative_tolerance']
            stability.append(dict(target=name+'/'+feature,status='stable_diagnostic' if stable else 'not_established',relative_change=relative))
            comparisons.append(dict(target=name+'/'+feature,model=b['value'] if b else None,reference=ref,
                status='not_comparable' if not stable else 'within_digitization_band' if ref['low']<=b['value']<=ref['high'] else 'outside_digitization_band'))
    status=('not_comparable' if any(c['status']=='not_comparable' for c in comparisons) else
            'feature_band_disagreement' if any(c['status']=='outside_digitization_band' for c in comparisons) else 'feature_band_agreement')
    payload=dict(version=1,protocol_ref=protocol_ref,run_ref=run_ref,reference_ref=p['reference_ref'],status=status,
        comparisons=comparisons,stability=stability,model_features=measurements,pointwise=s['pointwise'],phase_alignment='none',
        initials_origin=s['initials_origin'],registration=s['registration'],prior_exposure=s['prior_exposure'],
        independent_review='pending',scientific_support='unknown',
        note='Retrospective image-feature diagnostic only. Neither tool agreement nor image bands certify original transient reproduction or scientific validity.')
    return store.put('paper_comparison',payload,refs=(protocol_ref,run_ref,p['reference_ref']))
