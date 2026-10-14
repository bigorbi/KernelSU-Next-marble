#!/usr/bin/env python3
"""Runner-only staged baseline build. Local --dry-run never downloads or compiles."""
import argparse
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from audit import audit, compare
from cfi_pointers import pointer_inventory
from install_toolchains import install, verify_manifest, LLVM_SHA
from fetch_android_manifest import COMMIT, ROOT_TREE

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent
WORK = REPO / 'camera-work'
OUT = REPO / 'camera-output'
LOGS = OUT / 'logs'
REPORTS = OUT / 'reports'
KERNEL_SHA = '5c59cd9a314704d30f3f66004183426da23410e1'
MODULES_SHA = '83ff8a6f43d2fd897a6ee262f5965d4a9d75f00a'
MANAGER_SHA = '9a8d3f03db9bd6d51410a6fd732d1db273692ca6'
BUILDER_SHA = '3577f1d72a7c00c9248882ccac70ac3f15a97baa'
RELEASE = '5.10.269-Zois-17-5c59cd'
ORIGINAL_SHA = 'a7ae49bcfb2332b267550cf0433f605bbb28cf66cef1378deb443ebe4983c4c1'
KERNEL = WORK / 'kernel'
MODULES = WORK / 'modules-baseline'
KOUT = WORK / 'kernel-out'


def digest(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(4*1024*1024), b''):
            value.update(block)
    return value.hexdigest()


def write(name, value):
    REPORTS.mkdir(parents=True, exist_ok=True)
    (REPORTS / name).write_text(json.dumps(value, indent=2) + '\n')


def read(name):
    return json.loads((REPORTS / name).read_text())


def run(command, name, env=None, cwd=None, stdin=None):
    LOGS.mkdir(parents=True, exist_ok=True)
    print('Run:', shlex.join(map(str, command)), flush=True)
    with (LOGS / (name + '.log')).open('a') as stream:
        stream.write('COMMAND: ' + shlex.join(map(str, command)) + '\n')
        stream.flush()
        subprocess.run(list(map(str, command)), check=True, stdout=stream, stderr=subprocess.STDOUT,
                       env=env, cwd=cwd, stdin=stdin)


def git(path, *args):
    return subprocess.check_output(['git', '-C', str(path), *args], text=True).strip()


def mode():
    return os.environ.get('CAMERA_EXPLORATORY', 'true').lower() == 'true'


def policy():
    return json.loads((ROOT / 'compatibility-policy.json').read_text())


def gate(checks_pass, source, configuration, reference_ok):
    p = policy()
    reasons = []
    if not checks_pass: reasons.append('Baseline ABI/CFI/source-equivalence checks did not pass')
    if mode(): reasons.append('Exploratory mode cannot establish release compatibility')
    if not p['historical_susfs_sha'] or source.get('susfs_sha') != p['historical_susfs_sha'] or not p['historical_susfs_evidence']:
        reasons.append('Historical SUSFS revision is not independently verified')
    if not p['lifetime_review_complete'] or not p['lifetime_review_evidence']:
        reasons.append('Previously identified request error-path lifetime review remains unresolved')
    if not configuration.get('abi_options_match') or not configuration.get('release_match'):
        reasons.append('Kernel configuration/release is unverified')
    if not reference_ok: reasons.append('Original reference inventory provenance mismatch')
    return not reasons, reasons


def init():
    for directory in [LOGS, REPORTS, WORK]: directory.mkdir(parents=True, exist_ok=True)
    write('compatibility.json', {'status': 'NOT_RUN', 'gate_passed': False, 'installation_approved': False})
    write('build-status.json', {'baseline': 'NOT_BUILT', 'patched': 'NOT_BUILT', 'exploratory': mode()})
    write('sources.json', {'kernel_sha': KERNEL_SHA, 'modules_sha': MODULES_SHA, 'manager_sha': MANAGER_SHA,
                          'builder_sha': BUILDER_SHA, 'susfs_sha': None, 'exploratory': mode(),
                          'kernel_release_expected': RELEASE, 'workflow_sha': os.environ.get('GITHUB_SHA'),
                          'workflow_run': os.environ.get('GITHUB_RUN_ID'), 'make_jobs': 2, 'thinlto_jobs': 2,
                          'manager_compiled_version': 33321, 'manager_git_count': 3321,
                          'manager_compiled_tag': 'v3.4.0', 'historical_packaging_version': 33329})
    ref = os.environ.get('CAMERA_SUSFS_REF', 'gki-android12-5.10')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._/-]*', ref):
        raise ValueError('Invalid SUSFS ref')
    if not mode() and (not policy()['historical_susfs_sha'] or ref != policy()['historical_susfs_sha']):
        raise ValueError('Non-exploratory runs require the historically verified SHA committed in compatibility-policy.json')
    reference = json.loads((ROOT / 'original-abi.json').read_text())
    if reference['sha256'] != ORIGINAL_SHA:
        raise ValueError('Wrong original metadata provenance')


def git_entries(repo, tree):
    result = []
    raw = subprocess.check_output(['git', '-C', str(repo), 'ls-tree', '-z', tree])
    for row in raw.split(b'\0'):
        if not row: continue
        header, name = row.split(b'\t', 1)
        mode_value, kind, sha = header.decode().split()
        result.append({'mode': int(mode_value, 8), 'type': kind, 'id': sha, 'name': name.decode()})
    return result


def toolchains():
    downloads = ROOT / 'downloads'
    tools = ROOT / 'toolchains'
    downloads.mkdir(exist_ok=True)
    tools.mkdir(exist_ok=True)
    archive = downloads / 'LLVM-22.1.8-Linux-X64.tar.xz'
    run(['curl', '-fL', '--retry', '3', '-o', archive,
         'https://github.com/llvm/llvm-project/releases/download/llvmorg-22.1.8/LLVM-22.1.8-Linux-X64.tar.xz'], 'llvm-download')
    install('llvm-22.1.8', archive, ['22.1.8', 'ca7933e47d3a3451d81e72ac174dcb5aa28b59d1'], expected=LLVM_SHA)
    archive.unlink()
    prebuilt = WORK / 'android-prebuilt'
    run(['git', 'init', prebuilt], 'android-download')
    run(['git', '-C', prebuilt, 'remote', 'add', 'origin',
         'https://android.googlesource.com/platform/prebuilts/clang/host/linux-x86'], 'android-download')
    run(['git', '-C', prebuilt, 'fetch', '--depth=1', '--filter=blob:none', 'origin', COMMIT], 'android-download')
    run(['git', '-C', prebuilt, 'sparse-checkout', 'init', '--cone'], 'android-download')
    run(['git', '-C', prebuilt, 'sparse-checkout', 'set', 'clang-r563880c'], 'android-download')
    run(['git', '-C', prebuilt, 'checkout', '--detach', 'FETCH_HEAD'], 'android-download')
    if git(prebuilt, 'rev-parse', 'HEAD') != COMMIT or git(prebuilt, 'rev-parse', 'HEAD^{tree}') != ROOT_TREE:
        raise ValueError('Official Android Git tree mismatch')
    rows = subprocess.check_output(['git', '-C', str(prebuilt), 'ls-tree', '-r', '-z', 'HEAD:clang-r563880c'])
    files = []
    for row in rows.split(b'\0'):
        if not row: continue
        header, name = row.split(b'\t', 1)
        permission, kind, sha = header.decode().split()
        if kind != 'blob': raise ValueError('Unexpected prebuilt object')
        files.append({'path': name.decode(), 'mode': permission, 'sha1': sha})
    manifest = {'commit': COMMIT, 'root_tree': ROOT_TREE, 'root_entries': git_entries(prebuilt, 'HEAD'), 'files': files}
    verify_manifest(manifest, prebuilt / 'clang-r563880c')
    destination = tools / 'android-r563880c'
    shutil.move(str(prebuilt / 'clang-r563880c'), destination)
    version = subprocess.check_output([str(destination / 'bin/clang'), '--version'], text=True)
    if not all(value in version for value in ['21.0.0', '14054515', 'r563880c', '5e96669f06077099aa41290cdb4c5e6fa0f59349']):
        raise ValueError('Android compiler identity mismatch')
    for name in ['clang', 'ld.lld', 'llvm-ar', 'llvm-nm', 'llvm-objcopy', 'llvm-objdump', 'llvm-readelf', 'llvm-strip', 'llvm-ranlib']:
        if not os.access(destination / 'bin' / name, os.X_OK): raise ValueError('Android tool missing: ' + name)
    shutil.rmtree(prebuilt)
    write('toolchains.json', {'llvm': json.loads((tools / 'llvm-22.1.8.verified.json').read_text()),
                             'android': {'commit': COMMIT, 'root_tree': ROOT_TREE, 'compiler': version,
                                         'blob_count': len(files), 'verification': 'All pinned Git tree/blob hashes checked'}})


def fetch(repo_url, path, ref, log):
    run(['git', 'init', path], log)
    run(['git', '-C', path, 'remote', 'add', 'origin', repo_url], log)
    run(['git', '-C', path, 'fetch', '--depth=1', 'origin', ref], log)
    run(['git', '-C', path, 'checkout', '--detach', 'FETCH_HEAD'], log)
    return git(path, 'rev-parse', 'HEAD')


def sources():
    for repo, expected in [(KERNEL, KERNEL_SHA), (MODULES, MODULES_SHA), (WORK / 'builder', BUILDER_SHA)]:
        if git(repo, 'rev-parse', 'HEAD') != expected: raise ValueError('Source checkout mismatch')
    run(['git', '-C', KERNEL, 'checkout', '-B', '17', KERNEL_SHA], 'source-integration')
    manager = KERNEL / 'KernelSU-Next'
    if fetch('https://github.com/pershoot/KernelSU-Next.git', manager, MANAGER_SHA, 'manager-fetch') != MANAGER_SHA:
        raise ValueError('Manager SHA mismatch')
    susfs = WORK / 'susfs'
    sha = fetch('https://gitlab.com/simonpunk/susfs4ksu.git', susfs,
                os.environ.get('CAMERA_SUSFS_REF', 'gki-android12-5.10'), 'susfs-fetch')
    manifest = read('sources.json')
    manifest['susfs_sha'] = sha
    manifest['susfs_ref_requested'] = os.environ.get('CAMERA_SUSFS_REF', 'gki-android12-5.10')
    write('sources.json', manifest)
    if not mode() and sha != policy()['historical_susfs_sha']: raise ValueError('Historical SUSFS mismatch')
    (KERNEL / 'drivers/kernelsu').symlink_to('../KernelSU-Next/kernel')
    makefile = KERNEL / 'drivers/Makefile'
    makefile.write_text(makefile.read_text() + '\nobj-$(CONFIG_KSU) += kernelsu/\n')
    kconfig = KERNEL / 'drivers/Kconfig'
    kconfig.write_text(kconfig.read_text().replace('endmenu', 'source "drivers/kernelsu/Kconfig"\nendmenu'))
    patch = susfs / 'kernel_patches/50_add_susfs_in_gki-android12-5.10.patch'
    manifest['susfs_patch_sha256'] = digest(patch)
    with patch.open('rb') as stream: run(['patch', '--batch', '-p1'], 'susfs-apply', cwd=KERNEL, stdin=stream)
    for name in ['fs', 'include']:
        run(['rsync', '-a', str(susfs / 'kernel_patches' / name) + '/', str(KERNEL / name) + '/'], 'susfs-apply')
    if not re.search(r'^config KSU_SUSFS$', (manager / 'kernel/Kconfig').read_text(), re.M):
        raise ValueError('Pinned manager lacks required SUSFS integration')
    version = re.search(r'SUSFS_VERSION\s+"([^"]+)"', (KERNEL / 'include/linux/susfs.h').read_text())
    manifest['susfs_version'] = version.group(1) if version else 'UNKNOWN'
    manifest['camera_patch_sha256'] = digest(ROOT / 'camera-fix.patch')
    write('sources.json', manifest)
    # Header-install rules in this kernel reference the sibling sm8450-modules directory.
    (WORK / 'sm8450-modules').symlink_to('modules-baseline')
    (REPORTS / 'kernel-integration.patch').write_text(subprocess.check_output(['git', '-C', str(KERNEL), 'diff'], text=True))


def environment(toolchain):
    env = dict(os.environ)
    env['PATH'] = str(ROOT / 'toolchains' / toolchain / 'bin') + os.pathsep + env['PATH']
    env['KBUILD_BUILD_USER'] = 'marble'
    env['KBUILD_BUILD_HOST'] = 'github-actions'
    # Do not let repository/reference credentials enter build subprocess environments.
    env.pop('CAMERA_REFERENCE_TOKEN', None)
    return env


def make_args(external=False):
    common = ['make', '-C', KERNEL, 'O=' + str(KOUT), 'ARCH=arm64', 'LLVM=1', 'LLVM_IAS=1',
              'CC=clang', 'HOSTCC=clang', '-j2']
    toolchain = 'android-r563880c' if external else 'llvm-22.1.8'
    wrapper = WORK / ('ld-thinlto-' + toolchain)
    wrapper.write_text('#!/bin/sh\nexec ' + shlex.quote(str(ROOT / 'toolchains' / toolchain / 'bin/ld.lld')) +
                       ' "$@" --thinlto-jobs=2\n')
    wrapper.chmod(0o755)
    if external: return common + ['LD=' + str(wrapper), 'HOSTLD=' + str(wrapper)]
    # 30000 + 3321 = 33321, matching COMPILED metadata, not packaging's 33329.
    return common + ['LD=' + str(wrapper), 'HOSTLD=' + str(wrapper), 'KSU_GIT_VERSION_VALID=1',
                     'KSU_GIT_VERSION=3321', 'KSU_GIT_TAG=v3.4.0']


def config_values(path):
    result = {}
    for line in path.read_text().splitlines():
        if line.startswith('CONFIG_'):
            key, value = line.split('=', 1); result[key] = value
        elif line.startswith('# CONFIG_') and line.endswith(' is not set'): result[line[2:-11]] = 'n'
    return result


def config():
    KOUT.mkdir(exist_ok=True)
    shutil.copyfile(ROOT / 'running-config.txt', KOUT / '.config')
    env = environment('llvm-22.1.8')
    run(make_args() + ['olddefconfig'], 'kernel-config', env)
    expected, actual = config_values(ROOT / 'running-config.txt'), config_values(KOUT / '.config')
    differences = {key: {'original': expected.get(key, 'n'), 'rebuilt': actual.get(key, 'n')}
                   for key in expected.keys() | actual.keys() if expected.get(key, 'n') != actual.get(key, 'n')}
    run(make_args() + ['kernelrelease'], 'kernel-release', env)
    release_match = RELEASE in (LOGS / 'kernel-release.log').read_text().splitlines()
    write('configuration.json', {'differences': differences, 'abi_options_match': not differences,
                                 'release_match': release_match, 'config_sha256': digest(KOUT / '.config')})
    shutil.copyfile(KOUT / '.config', REPORTS / 'rebuilt-config.txt')
    if differences or not release_match: raise ValueError('Configuration or naturally generated release differs; inspect reports')
    for key in ['CONFIG_KSU', 'CONFIG_KSU_SUSFS', 'CONFIG_MODVERSIONS', 'CONFIG_CFI_CLANG', 'CONFIG_LTO_CLANG_THIN']:
        if actual.get(key) != 'y': raise ValueError('Required option not enabled: ' + key)


def kernel():
    run(make_args() + ['vmlinux', 'modules'], 'kernel-build', environment('llvm-22.1.8'))
    log = (LOGS / 'kernel-build.log').read_text()
    if '-- KernelSU-Next version: 33321' not in log or '-- KernelSU-Next tag: v3.4.0' not in log:
        raise ValueError('Compiled KernelSU metadata does not match the original build log')
    for name in ['Module.symvers', 'vmlinux', 'include/generated/autoconf.h', 'include/config/kernel.release']:
        if not (KOUT / name).is_file() or not (KOUT / name).stat().st_size:
            raise ValueError('Required genuine kernel artifact missing: ' + name)
    if (KOUT / 'include/config/kernel.release').read_text().strip() != RELEASE: raise ValueError('Release mismatch')
    shutil.copyfile(KOUT / 'Module.symvers', REPORTS / 'kernel-Module.symvers')


def mmrm():
    path = MODULES / 'qcom/opensource/mmrm-driver'
    run(make_args(True) + ['M=' + str(path), 'MMRM_ROOT=' + str(path), 'modules'], 'mmrm-build', environment('android-r563880c'))
    if not (path / 'Module.symvers').is_file() or not (path / 'Module.symvers').stat().st_size:
        raise ValueError('MMRM produced no authentic symbol table')
    shutil.copyfile(path / 'Module.symvers', REPORTS / 'mmrm-Module.symvers')


def camera(source, name):
    path = source / 'qcom/opensource/camera-kernel'
    mmrm_symbols = MODULES / 'qcom/opensource/mmrm-driver/Module.symvers'
    before = digest(KOUT / '.config')
    run(make_args(True) + ['M=' + str(path), 'CAMERA_KERNEL_ROOT=' + str(path), 'KERNEL_ROOT=' + str(KERNEL),
                          'MODNAME=camera', 'TARGET_PRODUCT=marble', 'KBUILD_EXTRA_SYMBOLS=' + str(mmrm_symbols), 'V=1', 'modules'],
        name + '-build', environment('android-r563880c'))
    if digest(KOUT / '.config') != before: raise ValueError('External compilation changed kernel configuration')
    return path / 'camera.ko'


def baseline():
    if git(MODULES, 'diff', '--name-only', 'HEAD'): raise ValueError('Baseline source has tracked changes')
    file = camera(MODULES, 'baseline')
    directory = OUT / 'baseline'; directory.mkdir(exist_ok=True)
    shutil.copyfile(file, directory / 'camera.ko')
    shutil.copyfile(readable_sources(), directory / 'sources.json')
    (directory / 'CLASSIFICATION.txt').write_text('UNVERIFIED BASELINE FOR ABI COMPARISON. NOT APPROVED FOR INSTALLATION.\n')
    status = read('build-status.json'); status['baseline'] = 'BUILT_UNVERIFIED'; write('build-status.json', status)


def readable_sources():
    return REPORTS / 'sources.json'


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl): return None


def private_reference():
    locator = os.environ.get('CAMERA_PRIVATE_REFERENCE', '')
    if not locator: return None
    match = re.fullmatch(r'([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+):(\d+)', locator)
    token = os.environ.get('CAMERA_REFERENCE_TOKEN', '')
    if not match or not token: raise ValueError('Private reference needs owner/repo:artifact_id and CAMERA_REFERENCE_TOKEN')
    repo, artifact = match.groups()
    headers = {'Authorization': 'Bearer ' + token, 'Accept': 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28'}
    def api(path):
        with urllib.request.urlopen(urllib.request.Request('https://api.github.com/repos/' + repo + path, headers=headers), timeout=60) as response:
            return json.load(response)
    if not api('')['private']: raise ValueError('Reference artifact repository must be private')
    metadata = api('/actions/artifacts/' + artifact)
    if metadata['expired'] or metadata['size_in_bytes'] > 32*1024*1024: raise ValueError('Private reference archive unavailable or too large')
    request = urllib.request.Request('https://api.github.com/repos/' + repo + '/actions/artifacts/' + artifact + '/zip', headers=headers)
    try:
        urllib.request.build_opener(NoRedirect).open(request, timeout=60)
        raise ValueError('Expected authenticated artifact redirect')
    except urllib.error.HTTPError as error:
        if error.code != 302: raise ValueError('Authenticated private artifact request failed') from None
        location = error.headers['Location']
    if not location.startswith('https://'): raise ValueError('Unsafe artifact redirect')
    # No authorization header is sent to the signed external download URL.
    private_dir = Path(os.environ['RUNNER_TEMP']) / 'camera-reference'; private_dir.mkdir(exist_ok=True)
    archive = private_dir / 'reference.zip'
    with urllib.request.urlopen(location, timeout=120) as response, archive.open('wb') as stream:
        total = 0
        for block in iter(lambda: response.read(1024*1024), b''):
            total += len(block)
            if total > 32*1024*1024: raise ValueError('Reference archive too large')
            stream.write(block)
    with zipfile.ZipFile(archive) as bundle:
        files = [item for item in bundle.infolist() if Path(item.filename).name == 'camera.ko' and not item.is_dir()]
        if len(files) != 1 or files[0].file_size > 16*1024*1024: raise ValueError('Private artifact must contain one camera.ko')
        path = private_dir / 'camera.ko'; path.write_bytes(bundle.read(files[0]))
    if digest(path) != ORIGINAL_SHA: raise ValueError('Private original module SHA mismatch')
    return path


def comparison():
    output = os.environ.get('GITHUB_OUTPUT')
    if output:
        with open(output, 'a') as stream: stream.write('passed=false\n')
    reference = json.loads((ROOT / 'original-abi.json').read_text())
    original_audit = json.loads((ROOT / 'original-audit.json').read_text())
    private = private_reference()
    if private and audit(private) != original_audit: raise ValueError('Private original disagrees with committed audit')
    report = compare(OUT / 'baseline/camera.ko', reference, original_audit)
    reference_ok = reference['sha256'] == original_audit['module_sha256'] == ORIGINAL_SHA
    passed, reasons = gate(report['necessary_abi_and_code_checks_pass'], read('sources.json'), read('configuration.json'), reference_ok)
    report.update(status='AUDIT_INCOMPLETE', gate_passed=False,
                  reasons=reasons, exploratory=mode(), installation_approved=False,
                  original_binary_published=False, private_reference_checked=bool(private))
    write('compatibility.json', report)
    tools = ROOT / 'toolchains/android-r563880c/bin'
    run([tools / 'llvm-readelf', '-h', '-S', '-s', '-r', '-p', '.comment', '-p', '.modinfo', OUT / 'baseline/camera.ko'], 'baseline-elf-audit')
    run([tools / 'llvm-objdump', '-dr', '--disassemble-symbols=__cfi_check', OUT / 'baseline/camera.ko'], 'baseline-cfi-audit')
    report.update(status='PASS' if passed else 'GATE_CLOSED', gate_passed=passed, binary_diagnostics_emitted=True)
    write('compatibility.json', report)
    (REPORTS / 'compatibility.md').write_text('# Camera compatibility\n\nStatus: ' + report['status'] +
                                            '\n\n' + '\n'.join('- ' + reason for reason in reasons) +
                                            '\n\nChecks:\n' + '\n'.join('- ' + key + ': ' + ('PASS' if value else 'FAIL') for key, value in report['checks'].items()) +
                                            '\n\nNo module is approved for installation.\n')
    if passed and output:
        with open(output, 'a') as stream: stream.write('passed=true\n')
    print('Compatibility gate:', 'PASS' if passed else 'CLOSED', '; '.join(reasons))
    if not report['necessary_abi_and_code_checks_pass']: raise ValueError('Baseline ABI/code audit did not pass; diagnostic report uploaded')


def patched():
    if not read('compatibility.json').get('gate_passed'): raise ValueError('Closed compatibility gate')
    source = WORK / 'modules-patched'
    run(['git', 'clone', '--shared', '--no-checkout', MODULES, source], 'patched-source')
    run(['git', '-C', source, 'checkout', '--detach', MODULES_SHA], 'patched-source')
    run(['git', '-C', source, 'apply', '--check', ROOT / 'camera-fix.patch'], 'patched-source')
    run(['git', '-C', source, 'apply', ROOT / 'camera-fix.patch'], 'patched-source')
    run(['python3', ROOT / 'tests/run_tests.py'], 'patched-offline-tests')
    file = camera(source, 'patched')
    # A source change must not change imports, exports, CRCs, dependencies or CFI symbol surface.
    from module_inventory import inventory
    baseline_info, patched_info = inventory(OUT / 'baseline/camera.ko'), inventory(file)
    checks = {key: baseline_info[key] == patched_info[key] for key in ['elf_type', 'machine', 'compiler', 'modinfo',
              'imports_crc', 'undefined', 'exports', 'absolute_export_crc', 'module_struct_size', 'signature_trailer']}
    checks['cfi_symbol_surface'] = sorted(map(lambda name: re.sub(r'\.llvm\.\d+', '.llvm', name), baseline_info['cfi_symbols'])) == sorted(map(lambda name: re.sub(r'\.llvm\.\d+', '.llvm', name), patched_info['cfi_symbols']))
    baseline_pointers, patched_pointers = pointer_inventory(OUT / 'baseline/camera.ko'), pointer_inventory(file)
    checks['cfi_published_targets'] = (not patched_pointers['invalid_published_pointers'] and
                                     not patched_pointers['unattributed_fields'] and
                                     patched_pointers['publications'] == baseline_pointers['publications'])
    write('patched-abi.json', {'checks': checks, 'pass': all(checks.values())})
    if not all(checks.values()): raise ValueError('Patched output changed ABI; no candidate artifact published')
    directory = OUT / 'patched'; directory.mkdir(exist_ok=True)
    shutil.copyfile(file, directory / 'camera.ko')
    shutil.copyfile(readable_sources(), directory / 'sources.json')
    (directory / 'CLASSIFICATION.txt').write_text('CANDIDATE ONLY. PHONE TEST AND INSTALLATION REVIEW STILL REQUIRED.\n')
    status = read('build-status.json'); status['patched'] = 'BUILT_GATED_CANDIDATE'; write('build-status.json', status)


def finalize():
    LOGS.mkdir(parents=True, exist_ok=True); REPORTS.mkdir(parents=True, exist_ok=True)
    if not (REPORTS / 'compatibility.json').exists(): write('compatibility.json', {'status': 'NOT_RUN', 'gate_passed': False})
    paths = {'kernel': KOUT / 'Module.symvers', 'mmrm': MODULES / 'qcom/opensource/mmrm-driver/Module.symvers',
             'camera': MODULES / 'qcom/opensource/camera-kernel/Module.symvers'}
    inventory = {}
    for name, path in paths.items():
        inventory[name] = {'generated': path.is_file(), 'sha256': digest(path) if path.is_file() else None}
        if path.is_file(): shutil.copyfile(path, REPORTS / (name + '-Module.symvers'))
    write('generated-symbol-tables.json', inventory)
    for directory in [LOGS, REPORTS, OUT / 'baseline', OUT / 'patched']:
        if not directory.exists(): continue
        files = [path for path in directory.rglob('*') if path.is_file() and path.name != 'SHA256SUMS']
        (directory / 'SHA256SUMS').write_text(''.join(digest(path) + '  ' + str(path.relative_to(directory)) + '\n' for path in sorted(files)))
    summary = os.environ.get('GITHUB_STEP_SUMMARY')
    if summary:
        result = read('compatibility.json')
        with open(summary, 'a') as stream:
            stream.write('## Camera build\n\nCompatibility: **' + result['status'] + '**.\n\n')
            stream.write('Exploratory: ' + str(mode()) + '. No installation approval or flashable ZIP. Inspect diagnostic artifacts.\n')
    # Private original is never inside uploaded output, and is deleted before uploads.
    if os.environ.get('RUNNER_TEMP'):
        shutil.rmtree(Path(os.environ['RUNNER_TEMP']) / 'camera-reference', ignore_errors=True)


STAGES = {'init': init, 'toolchains': toolchains, 'sources': sources, 'config': config,
          'kernel': kernel, 'mmrm': mmrm, 'baseline': baseline, 'compare': comparison,
          'patched': patched, 'finalize': finalize}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage', choices=STAGES)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    if args.dry_run:
        print(json.dumps({'stage': args.stage, 'network_or_compilation': False, 'make_jobs': 2,
                          'kernel': KERNEL_SHA, 'modules': MODULES_SHA, 'manager': MANAGER_SHA,
                          'release': RELEASE, 'patch_gate_required': args.stage == 'patched'}))
        return 0
    if os.environ.get('GITHUB_ACTIONS') != 'true':
        raise SystemExit('Build stages run only on GitHub Actions. Use --dry-run locally.')
    try:
        STAGES[args.stage]()
    except Exception as error:
        # Keep authenticated artifact redirect URLs and private repository/token details out of public logs.
        message = 'Authenticated private reference request failed' if isinstance(error, urllib.error.URLError) else str(error)
        write('failure.json', {'stage': args.stage, 'error': message, 'baseline_status': 'See build-status.json'})
        print('FAILED:', args.stage, message, flush=True)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
