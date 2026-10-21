"""Freeze the same controls under the changed product implementation."""
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[1]
PRIOR = ROOT/'artifacts/audit-t08b-workflow-adapters-20261019'
spec = importlib.util.spec_from_file_location('f1_driver', ROOT/'benchmarks/development/run_agent_comparison.py')
driver = importlib.util.module_from_spec(spec)
spec.loader.exec_module(driver)


def main():
    old = json.loads((PRIOR/'control-inputs/manifest.json').read_text())
    fresh = driver.protocol.development_manifest(ROOT)
    old_without_version, fresh_without_version = deepcopy(old), deepcopy(fresh)
    previous = old_without_version.pop('toolchain_sha256')
    current = fresh_without_version.pop('toolchain_sha256')
    assert previous != current
    assert old_without_version == fresh_without_version
    rejected_output = OUT/'stale-manifest-output-must-not-exist'
    assert not rejected_output.exists()
    def no_provider(_):
        raise AssertionError('stale manifest reached the provider')
    try:
        driver.run_comparison(old, no_provider, rejected_output, root=ROOT)
    except ValueError as exc:
        assert str(exc) == 'scientific tool implementation/environment changed'
        refusal = {'status': 'refused_before_provider_and_output', 'reason': str(exc)}
    else:
        raise AssertionError('stale manifest accepted')
    assert not rejected_output.exists()
    directory = OUT/'control-inputs'
    directory.mkdir(exist_ok=False)
    driver.adapters._write(directory/'manifest.json', fresh)
    shutil.copyfile(PRIOR/'control-inputs/scripts.json', directory/'scripts.json')
    old_script = (PRIOR/'control-inputs/scripts.json').read_bytes()
    new_script = (directory/'scripts.json').read_bytes()
    assert old_script == new_script
    driver.validate_scripts(fresh, json.loads(new_script))
    report = {'old_toolchain_sha256': previous, 'new_toolchain_sha256': current,
              'changed_manifest_fields': ['toolchain_sha256'], 'source_and_experiment_entries_identical': True,
              'scripts_byte_identical': True, 'scripts_sha256': hashlib.sha256(new_script).hexdigest(),
              'stale_manifest': refusal, 'planned_phases': len(driver.protocol.schedule(fresh)),
              'model_inference_calls': 0, 'evidence_kind': 'scripted_control'}
    driver.adapters._write(OUT/'control-freeze.json', report)
    print(json.dumps(report))


if __name__ == '__main__':
    main()
