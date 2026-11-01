"""Reports copy stored facts; scientific unknowns are preserved."""
import hashlib
import json


def _model_summary(artifact):
    """Display formulas read from the executable SBML, never from LLM prose."""
    import libsbml
    document = libsbml.readSBMLFromString(artifact['sbml'])
    model = document.getModel()
    lines = ['','## Executable model','',
             f'Model ID: `{artifact["id"]}`',
             f'SBML SHA-256: `{hashlib.sha256(artifact["sbml"].encode()).hexdigest()}`',
             '', '| Symbol | Initial/value | Representation |', '|---|---|---|']
    if model is None:
        return lines + ['', 'SBML could not be parsed; see static validation diagnostics.']
    for species in model.getListOfSpecies():
        value = species.getInitialAmount() if species.isSetInitialAmount() else species.getInitialConcentration() if species.isSetInitialConcentration() else 'unset'
        lines.append(f'| {species.getId()} | {value} | species |')
    for parameter in model.getListOfParameters():
        value = parameter.getValue() if parameter.isSetValue() else 'unset / assigned'
        lines.append(f'| {parameter.getId()} | {value} | parameter; constant={parameter.getConstant()} |')
    lines.extend(['', 'Rate rules and reaction fluxes use native SBML amount/concentration semantics. Experiment overrides are stored in the frozen input.', ''])
    for rule in model.getListOfRules():
        target = f'd({rule.getVariable()})/dt' if rule.isRate() else rule.getVariable() if not rule.isAlgebraic() else 'algebraic constraint'
        lines.append(f'- `{target} = {libsbml.formulaToL3String(rule.getMath())}`')
    for reaction in model.getListOfReactions():
        law = reaction.getKineticLaw()
        lines.append(f'- Reaction `{reaction.getId()}`: `{libsbml.formulaToL3String(law.getMath()) if law else "unset"}`')
    for assignment in model.getListOfInitialAssignments():
        lines.append(f'- Initial assignment `{assignment.getSymbol()} = {libsbml.formulaToL3String(assignment.getMath())}`')
    return lines


def render_report(store,ref):
    record = store.get(ref)
    p = record['payload']
    lines = [f'# BioModel research report', '', f'Artifact: `{ref}`', f'Kind: `{record["kind"]}`','']
    if record['kind'] in ('research_project','research_project_snapshot'):
        from .research_projects import report_lines
        lines.extend(report_lines(store,ref))
    elif record['kind'] in ('research_reproduction_batch','research_batch_snapshot'):
        from .research_pipeline import report_lines
        lines.extend(report_lines(store,ref))
    elif record['kind']=='research_task_proposal':
        from .task_proposals import report_lines
        lines.extend(report_lines(store,ref))
    elif record['kind'] in ('research_task','research_task_run'):
        from .research_tasks import report_lines
        lines.extend(report_lines(store,ref))
    elif record['kind'] in ('modeling_plan', 'modeling_plan_compilation', 'modeling_plan_run'):
        from .modeling_workflow import report_lines
        lines.extend(report_lines(store, ref))
    elif record['kind'] in ('typed_model', 'typed_run', 'typed_agent_run'):
        from .typed_models import report_lines
        lines.extend(report_lines(store, ref))
    elif record['kind']=='estimated_exploration_run':
        from .exploration import report_lines
        lines.extend(report_lines(store, ref))
    elif record['kind']=='paper_comparison':
        lines.extend([f"Diagnostic: **{p['status']}**", 'Scientific support: **unknown**',
                      'Independent review: **pending**', f"Registration: {p['registration']}",
                      f"Prior exposure: {p['prior_exposure']}", f"Initials: {p['initials_origin']}",
                      f"Pointwise: {p['pointwise']}; phase alignment: none", '',
                      '| Feature | Model | Image interval | Diagnostic |', '|---|---|---|---|'])
        for c in p['comparisons']:
            r=c['reference'];band=f"[{r['low']:.8g}, {r['high']:.8g}] (n={r['n']})" if r else 'missing'
            lines.append(f"| {c['target']} | {c['model']} | {band} | {c['status']} |")
        lines.extend(['', 'Intervals describe stroke and declared pixel calibration uncertainty, not confidence intervals.',
                      'No smoothing, interpolation or phase fitting. Missing/legend-covered samples remain missing.',
                      'X excluded because its amplitude is close to pixel/stroke resolution.', '',
                      'Adjacent-window stability is a numerical diagnostic, not a proof of a limit cycle.',p['note'],
                      '', f"Protocol: `{p['protocol_ref']}`",f"Reference: `{p['reference_ref']}`",f"Run: `{p['run_ref']}`"])
    elif record['kind']=='run':
        frozen = store.get(p['input_ref'])['payload']
        sim = p['simulation']
        lines.extend([f'Execution: **{p["status"]}**',f'Interpretation: **{p["scientific_status"]}**',f'Mode: `{p["mode"]}`',f'Backend: `{sim["backend"]}`',f'Input: `{p["input_ref"]}`',f'Solver: `{json.dumps(frozen["solver"],sort_keys=True)}`',f'Cached from: `{p.get("cached_from")}`',f'Replay of: `{p.get("replay_of")}`',''])
        lines.extend(_model_summary(frozen['artifact']))
        comp = p['comparison']
        lines.extend(['## Frozen comparison','',f'Status: **{comp["status"]}**'])
        for key in ('max_scaled_error','error_overflow','threshold','scales','time_unit','time_unit_status','reason'):
            if key in comp:
                lines.append(f'- {key}: `{json.dumps(comp[key],ensure_ascii=False)}`')
        for field in ('static_validation','numeric_validation'):
            lines.extend(['',f'## {field}',''])
            for check in p[field]['checks']:
                message = check['message'].replace('\n',' ')[:700]
                lines.append(f'- **{check["status"]}** ({check["severity"]}) `{check["code"]}`: {message}')
        source = p['source_validation']
        lines.extend(['', f'Target binding: **{source.get("binding_status", "unknown")}**',
                      f'Binding coverage: {source.get("binding_coverage", "unknown")}',
                      'A matched binding verifies only a declaration in the supported grammar. Scientific and semantic support remain separate.'])
        lines.extend(['','## Evidence and limits','',f'Citation coverage: {source["coverage"]}',f'Semantic support: **{source["semantic_support"]}**',f'Missing targets: `{json.dumps(source["missing"])}`',f'Invalid refs: `{json.dumps(source["invalid"])}`',f'Units: **{source["units_status"]}**','- A matching trajectory does not establish paper fidelity or biological truth.','- Citation presence is not a semantic support proof.','- Unrequested biological invariants are not assumed; conservation checking is not_run.','- passed_structured_reference denotes numerical agreement on the frozen execution benchmark only.'])
        lines.extend(['','## Environment','',f'Python: `{p["environment"]["python"]}`',f'Source hash: `{p["environment"]["code_sha256"]}`' if 'code_sha256' in p['environment'] else f'Environment: `{json.dumps(p["environment"].get("packages",{}),sort_keys=True)}`'])
    elif record['kind']=='hypothesis_study':
        from .hypotheses import study_verdict
        verdict = study_verdict(p)
        comparison = dict(p['comparison'])
        if not verdict['completion_eligible']:
            comparison.update(ranking='incomplete_computation', winner=None)
        lines.extend([f'Study execution: **{verdict["status"]}**',
                      f'Completion eligible: **{verdict["completion_eligible"]}**',
                      f'Failure reasons: `{json.dumps(verdict["failure_reasons"])}`',
                      f'Total scientific budget: {p.get("protocol", {}).get("total_timeout_s", "unknown")} seconds',
                      '', '## Frozen candidate comparison','',json.dumps(comparison,ensure_ascii=False,indent=2),'','| Candidate | Fit | Training weighted RMSE | Held-out scaled RMSE | Cross-backend check |','|---|---|---|---|---|'])
        for name,r in p['candidates'].items():
            lines.append(f'| {name} | {r["fit_status"]} | {r["training_weighted_rmse"]} | {r["heldout_scaled_rmse"]} | {r["crosscheck"]["ok"]} |')
        suggestion = p['experiment_suggestion']
        lines.extend(['','## Conditional intervention','',f'Intervention: `{suggestion["intervention"]}`',f'Nominal prediction gap: {suggestion["max_nominal_prediction_gap"]}',suggestion['limitation'],'','## Limits',''])
        lines.extend('- '+item for item in p['limitations'])
    elif record['kind'] in ('fba_run','fba_agent_run','fba_model'):
        from . import fba
        if record['kind']=='fba_model':
            model=fba.load(store,ref)
            lines.extend(['Model type: **FBA steady-state LP**',f"Flux unit: {model['flux_unit']}",
                          f"Metabolites: {len(model['metabolites'])}; reactions: {len(model['reactions'])}",
                          f"Objective: `{json.dumps(model['objective'])}`",'Scientific support: **unknown**'])
        else:
            run_ref=ref if record['kind']=='fba_run' else p.get('run_ref')
            if record['kind']=='fba_agent_run':
                lines.extend([f"Task state: **{p['state']}**",f"Provider calls: {p['call_count']}; reserved scientific runs: {p['run_count']}"])
                if p.get('fba_protocol') == 'fba_agent_v2':
                    task = store.get(p['task_ref'])['payload']
                    lines.extend(['', 'FBA continuation protocol: **fba_agent_v2**',
                        f"Task: `{p['task_ref']}`", f"Execution: `{p['execution_ref']}`",
                        f"Checkpoint: `{p['checkpoint_ref']}`",f"Remaining provider calls: {max(0,task['max_calls']-p['call_count'])}",
                        f"Verified recovery receipts: {len(p['recovery_refs'])}",
                        'Replies preserve the frozen LP and scientific budget; unknown outcomes are never automatically retried.'])
                    for event in p['events']:
                        if event.get('type') == 'awaiting_input':
                            lines.extend(['', 'Pending/recorded question: '+event['question'].replace('\n',' ')])
                    for clarification_ref in p['clarification_refs']:
                        clarification = store.get(clarification_ref)['payload']
                        lines.extend(['',f"Attributed execution reply by {clarification['respondent'].replace(chr(10),' ')}: `{clarification_ref}`",''])
                        lines.extend('> '+line for line in clarification['reply'].splitlines())
                    lines.extend(['', 'Attribution is not identity verification, scientific review or a model/boundary correction.'])
            if run_ref:
                if record['kind']=='fba_agent_run' and p.get('fba_protocol')=='fba_agent_v2':
                    from . import fba_agent_runtime
                    actual=fba_agent_runtime.bound_result(store,fba_agent_runtime.validate_task(store,p['task_ref']),run_ref)
                else:
                    actual=fba.summary(store,run_ref)
                run_payload=store.get(run_ref)['payload']
                model=fba.load(store,run_payload['model_ref'])
                settings=store.get(run_payload['input_ref'])['payload']['settings']
                lines.extend([f"Flux unit (declared): {model['flux_unit']}",
                    f"Active objective: `{json.dumps(model['objective'])}`",
                    f"Solver method: {settings['method']}; worker wall-clock budget: {settings['timeout_s']} s",
                    f"Balanced metabolites: {sum(not m['boundary_condition'] for m in model['metabolites'])}; explicitly excluded boundary metabolites: {sum(m['boundary_condition'] for m in model['metabolites'])}"])
                lines.extend(['Model type: **FBA steady-state LP**',f"Solver outcome: **{actual['status']}**",
                    f"Completion eligible: {actual['completion_eligible']}",f"Objective value: {actual['objective']}",
                    f"Reference comparison: `{json.dumps(actual['comparison'])}`",'Scientific support: **unknown**','',
                    'Feasibility checks:',*['- '+json.dumps(c) for c in actual['checks']], '',
                    'A matched scalar objective does not validate a unique flux vector or biological correctness.'])
                if actual['fluxes']:
                    selected=sorted(actual['fluxes'].items(),key=lambda item:(-abs(item[1]),item[0]))[:40]
                    lines.extend(['','Largest absolute fluxes (up to 40; full vector is saved in the run artifact):','','| Reaction | Flux |','|---|---:|'])
                    lines.extend(f'| {rid} | {value:.12g} |' for rid,value in selected)
            else:lines.append('No confirmed FBA result. Inspect the invocation receipts; unknown effects are not retried.')
        lines.extend(['','Steady-state S·v=0 with explicit bounds. No ODE time trajectory, gene edits or biological certification.'])
    elif record['kind']=='agent_run':
        lines.extend([f'Task state: **{p["state"]}**',f'LLM calls: {p["call_count"]}',f'Transcription repairs: {p["repair_count"]}',f'Model: `{p["model_ref"]}`',f'Run: `{p["run_ref"]}`'])
        decision_events = [e for e in p['events'] if e.get('tool') == 'submit_plan']
        if decision_events:
            lines.extend(['', '## Native Agent modeling decisions', '',
                          'Decision plans and compiler receipts are proposals, not independent scientific approval.'])
            for event in decision_events:
                lines.append(f'- Call {event["call"]}: {event["state"]}; plan `{event.get("modeling_plan_ref")}`; submission `{event.get("submission_ref")}`')
            current = decision_events[-1].get('modeling_plan_ref')
            if current:
                from .modeling_workflow import report_lines
                lines.extend(report_lines(store, current))
        if p.get('progress'):
            progress = p['progress']
            lines.extend(['', '## Task progress', '', '| Step | Status |', '|---|---|'])
            lines.extend(f"| {step['id']} | {step['status']} |" for step in progress['steps'])
            if progress.get('task_plan'):
                plan = progress['task_plan']
                lines.extend(['', '## Versioned task plan', '',
                              f"Revision: {plan['revision']}; plan: `{plan['plan_ref']}`", '',
                              'Revision reason: '+plan['reason'], '',
                              '| Step | Depends on | Status | Attempts / limit |', '|---|---|---|---|'])
                lines.extend(f"| {s['id']} | {', '.join(s['depends_on'])} | {s['status']} | {s['attempts']} / {s['attempt_limit']} |"
                             for s in plan['steps'])
                lines.extend(['', 'Reference comparison: '+str(next(s for s in plan['steps'] if s['id'] == 'compare')['outcome']),
                              'Plan completion does not establish scientific support.'])
            lines.extend(['', 'Next action: ' + progress['next_action'],
                          '', 'Scientific support: **not_verified**', '', 'Unresolved issues:', ''])
            lines.extend('- ' + item['message'] for item in progress['unresolved'])
            if not progress['unresolved']:
                lines.append('- None recorded in the current workflow state; scientific unknowns remain.')
            lines.extend(['', 'Parameter provenance (conditions and scientific applicability remain unverified):', '',
                          '| Target | Proposed value | Declared unit | Source claims | Declared context |', '|---|---|---|---|---|'])
            lines.extend(f"| {row['target']} | {row['value']} | {row['unit']} | {len(row['citations'])} | {(row.get('declared_context') or {}).get('status', 'not_checked')} |"
                         for row in progress['parameters'])
            for row in progress['parameters']:
                for citation in row['citations']:
                    derivation = citation.get('parameter_derivation')
                    if derivation:
                        c = derivation['conversion']
                        lines.append('')
                        lines.append(f"Native parameter derivation for {row['target']}: SABIO-RK {derivation['record_id']}, "
                                     f"parameter[{derivation['parameter_index']}] ({derivation['native_parameter_name']}); "
                                     f"{c['original_value']} {c['original_unit']} × {c['factor_numerator']}/{c['factor_denominator']} "
                                     f"= {c['value']} {c['unit']}. Original source: {derivation['original_source_ref']}. "
                                     "Database-reported; scientific support remains unknown.")
            if progress.get('scientific_skills'):
                lines.extend(['', 'Scientific procedure checks (not efficacy or scientific approval):', ''])
                for procedure in progress['scientific_skills']['procedures']:
                    lines.append(f"- {procedure['name']} v{procedure['version']}: {procedure['status']}; artifacts: {', '.join(procedure['artifact_refs']) or 'none'}; scientific support: unknown")
            if progress.get('failure_handling'):
                handling = progress['failure_handling']
                lines.extend(['', 'Bounded failure decisions:', '',
                    f"Automatic local investigations: {handling['auto_investigations']} (remaining {handling['remaining_auto_investigations']})."])
                lines.extend(f"- {d['route']}: {d['reason']} Decision: {d['decision_ref']}"
                             for d in handling['decisions'])
            if progress['failures']:
                lines.extend(['', 'Failure handling:', ''])
                lines.extend(f"- {item['category']} / {item['code']}: {item['suggestion']}" for item in progress['failures'])
        lines.extend(['', '## Limits', ''])
        lines.extend('- '+item for item in p['limitations'])
        lines.extend(['', f"Budgeted retries: {p.get('retry_count',0)}", '', '## Diagnostics', ''])
        for event in p['events']:
            if event.get('type') == 'provider_error':
                lines.append(f"- Call {event['call']}: {event.get('kind','provider_error')}: {event['error']}")
            elif event.get('type') == 'awaiting_input':
                lines.append('- Recorded input request ('+event.get('origin','model')+'): '+event['question'])
            elif event.get('type') == 'context_budget_exceeded':
                lines.append(f"- Context budget exceeded: essential {event['required_chars']} vs limit {event['max_chars']} characters; inference stopped.")
            elif event.get('type') == 'llm_call' and event.get('context', {}).get('compacted'):
                audit = event['context']
                lines.append(f"- Call {event['call']}: context {audit['original_chars']} → {audit['sent_chars']} message-JSON characters; {audit['omitted_messages']} archived messages omitted.")
            elif event.get('type') == 'retry_deferred':
                lines.append(f"- Retry deferred for {event['retry_after_s']} seconds; interactive wait limit exceeded.")
        if p.get('task_ref'):
            task = store.get(p['task_ref'])['payload']
            lines.extend(['', '## Continuation', '',
                          f"Task: `{p['task_ref']}`",
                          f"Parent run: `{p.get('parent_run_ref')}`",
                          f"Checkpoint: `{p.get('checkpoint_ref')}`",
                          f"Remaining calls: {max(0, task['max_calls'] - p['call_count'])}"])
            if task.get('study_plan_ref'):
                lines.extend(['', '## Frozen study', '', f"Plan: `{task['study_plan_ref']}`"])
                if p.get('run_ref'):
                    study_record = store.get(p['run_ref'])
                    if study_record['kind'] == 'hypothesis_study':
                        study = study_record['payload']
                        lines.extend([f"Ranking: **{study['comparison']['ranking']}**",
                                      f"Selected candidate: `{study['comparison']['winner']}`",
                                      f"Usable candidates: {study.get('usable_candidates', 0)}",
                                      'Scientific status: **not_verified**',
                                      'Training-only fitting; frozen holdout comparison; no mechanism proof.'])
            for clarification_ref in p.get('clarification_refs', []):
                reply = store.get(clarification_ref)['payload']
                lines.extend(['', f"Researcher assertion from {reply['respondent']} (`{clarification_ref}`)",
                              '', 'Question: ' + reply['question']['question'], '', 'Reply:', ''])
                lines.extend('> ' + line for line in reply['reply'].splitlines())
            lines.extend(['', 'Researcher replies are attributed assertions, not independently verified paper facts.'])
        lines.extend(['','Full visible messages, actions, evidence and tool outcomes are retained in this artifact.'])
    else:
        raise ValueError('unsupported report artifact kind')
    lines.extend(['','## Direct provenance refs',''])
    lines.extend(f'- `{r}`' for r in record['refs'])
    return '\n'.join(lines)+'\n'
