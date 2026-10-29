#!/usr/bin/env python3
"""Publish one approved artifact, or fail closed; never retry a write."""
import base64
import hashlib
import json
import os
import pathlib
import subprocess
import sys
import tarfile

REPOSITORY='ghcr.io/syuhei0519/argo-rollouts-account-policy'
REFERENCE=REPOSITORY+':src-c879ac072bf7-bin-02cdb9d92d55'
MANIFEST='sha256:3db562b99a4b200285eaf756a03dc8d90c5d00c7579e2802ab6fccb50d65cdec'
CONFIG='sha256:08dd8a1ea8d94fde98319f3d3e40bb3ed4a3565b2724b8e08a150e09662e448e'
BINARY='02cdb9d92d5570c1fbb9cdebf4b354222c05be053ebbf7b84b80b443d68beacb'

def crane(args, secret=None):
    return subprocess.run(['crane',*args],input=secret,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,check=False)

def checked(args):
    p=crane(args)
    if p.returncode: raise ValueError('registry operation rejected or uncertain')
    return p.stdout.strip()

def has_scope(token):
    try:
        part=token.split('.')[1]; part+='='*(-len(part)%4)
        claims=json.loads(base64.urlsafe_b64decode(part))
        return any(a.get('type')=='repository' and a.get('name')=='syuhei0519/argo-rollouts-account-policy'
                   and {'pull','push'}.issubset(set(a.get('actions',[]))) for a in claims.get('access',[]))
    except (ValueError,IndexError,TypeError,KeyError): return False

def state(result):
    if result.returncode==0:
        if result.stdout.strip()!=MANIFEST: raise ValueError('existing tag has different digest')
        return 'same'
    err=result.stderr
    if 'MANIFEST_UNKNOWN' in err and not any(s in err.lower() for s in ('unauthorized','denied','forbidden')):
        return 'absent'
    raise ValueError('tag identity unavailable')

def verify_remote():
    if checked(['digest',REFERENCE]) != MANIFEST: raise ValueError('remote manifest mismatch')
    raw=crane(['manifest',REFERENCE])
    if raw.returncode or 'sha256:'+hashlib.sha256(raw.stdout.encode()).hexdigest()!=MANIFEST:
        raise ValueError('remote manifest bytes mismatch')
    manifest=json.loads(raw.stdout)
    if manifest.get('config',{}).get('digest')!=CONFIG: raise ValueError('remote config mismatch')
    checked(['validate','--remote',REFERENCE])

def verify_rootfs(path):
    with tarfile.open(path,'r:*') as t:
        matches=[m for m in t if m.name.lstrip('./')=='opt/account-policy']
        if len(matches)!=1 or not matches[0].isfile() or matches[0].mode!=0o555:
            raise ValueError('remote plugin missing')
        if hashlib.sha256(t.extractfile(matches[0]).read()).hexdigest()!=BINARY:
            raise ValueError('remote plugin mismatch')

def publish(layout, exported):
    login=crane(['auth','login','ghcr.io','-u',os.environ['GITHUB_ACTOR'],'--password-stdin'],os.environ['GITHUB_TOKEN'])
    if login.returncode: raise ValueError('registry authentication rejected')
    token=checked(['auth','token','--push',REPOSITORY])
    if not has_scope(token): raise ValueError('registry scope unknown or insufficient')
    before=state(crane(['digest',REFERENCE]))
    if before=='absent':
        # Single operator and workflow concurrency are assumed; this is not atomic tag creation.
        if state(crane(['digest',REFERENCE]))!='absent': raise ValueError('tag changed')
        write=subprocess.run(['account-controller-push',str(layout)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,check=False)
        if write.returncode: raise ValueError('registry write failed or uncertain')
    verify_remote()
    checked(['export',REFERENCE,str(exported)])
    verify_rootfs(exported)
    print(json.dumps({'reference':REFERENCE,'manifest':MANIFEST,'config':CONFIG,'binary':BINARY,'action':'verified-existing' if before=='same' else 'published'}))

if __name__ == '__main__':
    try: publish(pathlib.Path(sys.argv[1]),pathlib.Path(sys.argv[2]))
    except Exception: raise SystemExit('publication stopped; registry state requires inspection')
