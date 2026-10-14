#!/usr/bin/env python3
"""Local YAML/syntax/input/gate checks; no downloads, compiler calls or dispatches."""
import ast
import json
import re
import subprocess
import sys
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parent


def main():
    workflow = yaml.load((ROOT.parent / '.github/workflows/camera-fix-build.yml').read_text(), Loader=yaml.BaseLoader)
    assert set(workflow['on']) == {'workflow_dispatch'}, 'Only manual dispatch is allowed'
    assert workflow['permissions'] == {'contents': 'read'}
    assert len(workflow['jobs']) == 1 and 'strategy' not in workflow['jobs']['camera']
    job = workflow['jobs']['camera']
    assert job['runs-on'] == 'ubuntu-24.04'
    assert int(job['timeout-minutes']) <= 360
    assert 'CAMERA_REFERENCE_TOKEN' not in job['env']
    steps = job['steps']
    stages = []
    for step in steps:
        if 'uses' in step:
            assert re.fullmatch(r'actions/(checkout|upload-artifact)@[a-f0-9]{40}', step['uses'])
            if step['uses'].startswith('actions/checkout'):
                assert step['with']['persist-credentials'] == 'false'
        if 'run' in step:
            assert '${{ inputs.' not in step['run'], 'User input must be passed through environment'
            subprocess.run(['bash', '-n'], input=step['run'], text=True, check=True)
            if step['run'].startswith('python3 camera-build/ci.py '): stages.append(step['run'].split()[-1])
    assert stages == ['init', 'toolchains', 'sources', 'config', 'kernel', 'mmrm', 'baseline', 'compare', 'patched', 'finalize']
    patch_step = next(step for step in steps if step.get('run') == 'python3 camera-build/ci.py patched')
    assert "steps.compatibility.outputs.passed == 'true'" in patch_step['if']
    for step in steps:
        if step.get('with', {}).get('name') in ['kernel-build-logs', 'baseline-compatibility-report']:
            assert 'always()' in step['if']
    artifact = next(step for step in steps if step.get('with', {}).get('name') == 'patched-camera')
    assert "steps.compatibility.outputs.passed == 'true'" in artifact['if']
    for path in ROOT.rglob('*.py'):
        if 'toolchains' not in path.parts: ast.parse(path.read_text(), filename=str(path))
    abi = json.loads((ROOT / 'original-abi.json').read_text())
    audit = json.loads((ROOT / 'original-audit.json').read_text())
    assert 'path' not in abi
    assert abi['sha256'] == audit['module_sha256'] == 'a7ae49bcfb2332b267550cf0433f605bbb28cf66cef1378deb443ebe4983c4c1'
    assert len(abi['imports_crc']) == 331 and len(abi['exports']) == 88
    assert audit['cfi_fingerprints'].get('__cfi_check')
    assert not list(ROOT.rglob('*.ko')), 'Never add proprietary or generated modules to the repository'
    for stage in stages:
        result = subprocess.check_output([sys.executable, str(ROOT / 'ci.py'), stage, '--dry-run'], text=True)
        assert not json.loads(result)['network_or_compilation']
    print('PASS: YAML structure, manual trigger, pinned read-only actions, gate/artifact conditions, shell/Python syntax, sanitized reference inventory, all-stage dry runs')


if __name__ == '__main__':
    main()
