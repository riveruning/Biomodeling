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
    if record['kind']=='run':
        frozen = store.get(p['input_ref'])['payload']
        sim = p['simulation']
        lines.extend([f'Execution: **{p["status"]}**',f'Interpretation: **{p["scientific_status"]}**',f'Mode: `{p["mode"]}`',f'Backend: `{sim["backend"]}`',f'Input: `{p["input_ref"]}`',f'Solver: `{json.dumps(frozen["solver"],sort_keys=True)}`',f'Cached from: `{p.get("cached_from")}`',f'Replay of: `{p.get("replay_of")}`',''])
        lines.extend(_model_summary(frozen['artifact']))
        comp = p['comparison']
        lines.extend(['## Frozen comparison','',f'Status: **{comp["status"]}**'])
        for key in ('max_scaled_error','threshold','scales','time_unit','time_unit_status','reason'):
            if key in comp:
                lines.append(f'- {key}: `{json.dumps(comp[key],ensure_ascii=False)}`')
        for field in ('static_validation','numeric_validation'):
            lines.extend(['',f'## {field}',''])
            for check in p[field]['checks']:
                message = check['message'].replace('\n',' ')[:700]
                lines.append(f'- **{check["status"]}** ({check["severity"]}) `{check["code"]}`: {message}')
        source = p['source_validation']
        lines.extend(['','## Evidence and limits','',f'Citation coverage: {source["coverage"]}',f'Semantic support: **{source["semantic_support"]}**',f'Missing targets: `{json.dumps(source["missing"])}`',f'Invalid refs: `{json.dumps(source["invalid"])}`',f'Units: **{source["units_status"]}**','- A matching trajectory does not establish paper fidelity or biological truth.','- Citation presence is not a semantic support proof.','- Unrequested biological invariants are not assumed; conservation checking is not_run.','- passed_structured_reference denotes numerical agreement on the frozen execution benchmark only.'])
        lines.extend(['','## Environment','',f'Python: `{p["environment"]["python"]}`',f'Source hash: `{p["environment"]["code_sha256"]}`' if 'code_sha256' in p['environment'] else f'Environment: `{json.dumps(p["environment"].get("packages",{}),sort_keys=True)}`'])
    elif record['kind']=='hypothesis_study':
        lines.extend([f'Study execution: **{p["status"]}**','', '## Frozen candidate comparison','',json.dumps(p['comparison'],ensure_ascii=False,indent=2),'','| Candidate | Fit | Training weighted RMSE | Held-out scaled RMSE | Cross-backend check |','|---|---|---|---|---|'])
        for name,r in p['candidates'].items():
            lines.append(f'| {name} | {r["fit_status"]} | {r["training_weighted_rmse"]} | {r["heldout_scaled_rmse"]} | {r["crosscheck"]["ok"]} |')
        suggestion = p['experiment_suggestion']
        lines.extend(['','## Conditional intervention','',f'Intervention: `{suggestion["intervention"]}`',f'Nominal prediction gap: {suggestion["max_nominal_prediction_gap"]}',suggestion['limitation'],'','## Limits',''])
        lines.extend('- '+item for item in p['limitations'])
    elif record['kind']=='agent_run':
        lines.extend([f'Task state: **{p["state"]}**',f'LLM calls: {p["call_count"]}',f'Transcription repairs: {p["repair_count"]}',f'Model: `{p["model_ref"]}`',f'Run: `{p["run_ref"]}`','','## Limits',''])
        lines.extend('- '+item for item in p['limitations'])
        lines.extend(['', f"Budgeted retries: {p.get('retry_count',0)}", '', '## Diagnostics', ''])
        for event in p['events']:
            if event.get('type') == 'provider_error':
                lines.append(f"- Call {event['call']}: {event.get('kind','provider_error')}: {event['error']}")
            elif event.get('type') == 'awaiting_input':
                lines.append('- Needs input ('+event.get('origin','model')+'): '+event['question'])
            elif event.get('type') == 'retry_deferred':
                lines.append(f"- Retry deferred for {event['retry_after_s']} seconds; interactive wait limit exceeded.")
        if p.get('task_ref'):
            task = store.get(p['task_ref'])['payload']
            lines.extend(['', '## Continuation', '',
                          f"Task: `{p['task_ref']}`",
                          f"Parent run: `{p.get('parent_run_ref')}`",
                          f"Checkpoint: `{p.get('checkpoint_ref')}`",
                          f"Remaining calls: {max(0, task['max_calls'] - p['call_count'])}"])
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
