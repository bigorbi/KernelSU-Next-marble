#!/usr/bin/env python3
"""Run on an internet-connected host. Authenticate Gitiles trees against the pinned root."""
import hashlib
import json
import urllib.request
from pathlib import Path

REPO = 'https://android.googlesource.com/platform/prebuilts/clang/host/linux-x86'
COMMIT = '531a564ba5426707dee226eccb19f104a7af5a5f'
ROOT_TREE = '47e43d373251d428b6e0a179595c5c8e426624f1'
SUBDIR = 'clang-r563880c'


def object_hash(kind, data):
    return hashlib.sha1(kind.encode() + b' ' + str(len(data)).encode() + b'\0' + data).hexdigest()


def tree_hash(entries):
    ordered = sorted(entries, key=lambda entry: (entry['name'] + ('/' if entry['type'] == 'tree' else '')).encode())
    data = b''.join((format(entry['mode'], 'o') + ' ' + entry['name']).encode() + b'\0' +
                    bytes.fromhex(entry['id']) for entry in ordered)
    return object_hash('tree', data)


def fetch(path, expected):
    url = REPO + '/+/' + COMMIT + '/' + path + '?format=JSON'
    with urllib.request.urlopen(url, timeout=90) as response:
        raw = response.read().decode()
    doc = json.loads(raw[raw.index('\n') + 1:] if raw.startswith(")]}'") else raw)
    if doc['id'] != expected or tree_hash(doc['entries']) != expected:
        raise ValueError('Git tree checksum mismatch: ' + path)
    return doc


def main():
    root = fetch('', ROOT_TREE)
    entry = next(x for x in root['entries'] if x['name'] == SUBDIR)
    records = []

    def walk(prefix, sha):
        doc = fetch(SUBDIR + '/' + prefix, sha)
        for item in doc['entries']:
            path = prefix + item['name']
            if item['type'] == 'tree':
                walk(path + '/', item['id'])
            elif item['type'] == 'blob':
                records.append({'path': path, 'mode': format(item['mode'], 'o'), 'sha1': item['id']})
            else:
                raise ValueError('Unexpected Git entry: ' + path)

    walk('', entry['id'])
    doc = {'repo': REPO, 'commit': COMMIT, 'root_tree': ROOT_TREE,
           'subdir': SUBDIR, 'subtree': entry['id'], 'root_entries': root['entries'],
           'files': records}
    path = Path(__file__).resolve().parent / 'downloads/android-r563880c-14054515.manifest.json'
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(doc, indent=2) + '\n')
    print('Saved authenticated manifest:', path, 'files:', len(records))


if __name__ == '__main__':
    main()
