#!/usr/bin/env python3
"""Run pinned official correction/merge functions and Kubernetes three-way apply."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import yaml

SHA='e258ee23c3e52266d407572f4bcdfe7d9ed36cb5'
ROOT=Path(__file__).resolve().parent

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--argocd',type=Path,required=True)
    a=p.parse_args()
    assert subprocess.check_output(['git','-C',str(a.argocd),'rev-parse','HEAD'],text=True).strip()==SHA
    source=subprocess.check_output(['git','-C',str(a.argocd),'show',SHA+':controller/sync.go'],text=True)
    # Use exact function bodies from the pinned commit, never a hand-written substitute.
    functions=[]
    for name in ['getMergePatch','applyMergePatch','restoreNonIgnoredFields']:
        start=source.index('func '+name+'(')
        stop=source.index('\nfunc ',start)
        functions.append(source[start:stop])
    rendered=subprocess.check_output(['helm','template','mesh-rollout',str(ROOT/'rollout-chart'),'-n','learning-istio-demo','--kube-version','1.36.4'],text=True)
    fixtures=[d for d in yaml.safe_load_all(rendered) if d['kind'] in ['VirtualService','DestinationRule']]
    with tempfile.TemporaryDirectory() as directory:
        target=Path(directory)
        archive=subprocess.check_output(['git','-C',str(a.argocd),'archive',SHA,'go.mod','go.sum','gitops-engine/go.mod'])
        subprocess.run(['tar','-xf','-','-C',str(target)],input=archive,check=True)
        (target/'upstream.go').write_text('package controller\nimport ("reflect"; jsonpatch "github.com/evanphx/json-patch"; "k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"; "k8s.io/apimachinery/pkg/util/strategicpatch")\n'+'\n'.join(functions))
        shutil.copyfile(ROOT/'argocd-sync-test/sync_test.go',target/'sync_test.go')
        (target/'fixtures.json').write_text(json.dumps(fixtures))
        env=dict(os.environ,GOFLAGS='-mod=readonly -buildvcs=false')
        subprocess.run(['go','test','.','-run','^TestIstioSyncOwnership$','-count=1','-timeout','60s','-v'],cwd=target,env=env,check=True)
