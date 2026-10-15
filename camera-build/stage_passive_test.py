#!/usr/bin/env python3
"""Stage a passive KernelSU directory on the host; no ZIP/device operations."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
from device_candidate import BASELINE_SHA, PATCHED_REQUIRED


def stage(source, target):
    report = json.loads((source / 'audit.json').read_text())
    digest = hashlib.sha256((source / 'camera.ko').read_bytes()).hexdigest()
    if (report.get('binary_safety_checks_pass') is not True or set(report.get('checks', {})) != set(PATCHED_REQUIRED)
            or not all(value is True for value in report['checks'].values()) or report.get('baseline_sha256') != BASELINE_SHA
            or report.get('patched_sha256') != digest):
        raise ValueError('Candidate has not passed the required binary safety audit')
    names = set()
    for line in (source / 'SHA256SUMS').read_text().splitlines():
        expected, name = line.split('  ', 1)
        if (Path(name).name != name or (source / name).is_symlink()
                or hashlib.sha256((source / name).read_bytes()).hexdigest() != expected):
            raise ValueError('Artifact integrity/path mismatch')
        names.add(name)
    if names != {p.name for p in source.iterdir() if p.name != 'SHA256SUMS'}:
        raise ValueError('Unlisted artifact content')
    if not (source / 'ABI-CFI-PASS').is_file():
        raise ValueError('Missing candidate safety marker')
    target.mkdir()  # Refuse to overwrite an existing staged test.
    files = Path(__file__).resolve().parent / 'device-test'
    for name in ['module.prop', 'skip_mount', 'manual-load.sh', 'rollback-recovery.sh',
                 'arm-once.sh', 'one-shot-guard.rc.example', 'preflight-readonly.sh']:
        shutil.copyfile(files / name, target / name)
        (target / name).chmod(0o755 if name.endswith('.sh') else 0o644)
    shutil.copytree(source, target / 'payload')
    print('Passive host directory staged. No boot script, overlay, ZIP or device operation was created.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('candidate_artifact', type=Path)
    parser.add_argument('new_host_directory', type=Path)
    args = parser.parse_args()
    stage(args.candidate_artifact, args.new_host_directory)
