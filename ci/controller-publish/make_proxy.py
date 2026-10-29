#!/usr/bin/env python3
"""Build a local Go module proxy from one immutable published Git tree."""
import datetime
import hashlib
import io
import json
import pathlib
import subprocess
import sys
import tarfile
import zipfile

COMMIT = 'c879ac072bf78ba33142070932e86503cb7242b8'
MODULE = 'learning.local/account-release-metric'
VERSION = 'v0.0.0-20261029042856-c879ac072bf7'
SOURCE = 'learning/account-release/metric-plugin'

def main():
    repo, target = map(pathlib.Path, sys.argv[1:])
    head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=repo, text=True).strip()
    stamp = int(subprocess.check_output(['git', 'show', '-s', '--format=%ct', COMMIT], cwd=repo))
    if head != COMMIT or stamp != 1793248136:
        raise SystemExit('source identity mismatch')
    data = subprocess.check_output(['git', 'archive', '--format=tar', COMMIT + ':' + SOURCE], cwd=repo)
    files = {}
    with tarfile.open(fileobj=io.BytesIO(data), mode='r:') as archive:
        for item in archive:
            if item.isfile(): files[item.name] = archive.extractfile(item).read()
            elif not item.isdir(): raise SystemExit('unexpected module entry')
    if len(files) != 12 or files['go.mod'].splitlines()[1] != ('module ' + MODULE).encode():
        raise SystemExit('module identity mismatch')
    target = target / MODULE / '@v'
    target.mkdir(parents=True)
    (target / (VERSION+'.mod')).write_bytes(files['go.mod'])
    (target / (VERSION+'.info')).write_text(json.dumps({'Version': VERSION, 'Time': '2026-10-29T04:28:56Z'})+'\n')
    (target / 'list').write_text(VERSION+'\n')
    prefix = MODULE+'@'+VERSION+'/'
    with zipfile.ZipFile(target/(VERSION+'.zip'), 'w', zipfile.ZIP_DEFLATED) as z:
        for name, content in sorted(files.items()): z.writestr(prefix+name, content)
    with zipfile.ZipFile(target/(VERSION+'.zip')) as z:
        if {n[len(prefix):]: z.read(n) for n in z.namelist()} != files:
            raise SystemExit('module bytes mismatch')
    print(json.dumps({'commit': COMMIT, 'version': VERSION,
                      'zipSha256': hashlib.sha256((target/(VERSION+'.zip')).read_bytes()).hexdigest()}))

if __name__ == '__main__': main()
