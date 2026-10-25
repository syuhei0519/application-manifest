#!/usr/bin/env python3
# Istio と Rollout を組み合わせた配備設定を描画し、経路と段階の整合性を確認します。
"""Offline schema/render checks; no controller or cluster success is inferred."""
import argparse
import copy
import hashlib
import json
import importlib.util
import tempfile
from types import SimpleNamespace
import subprocess
from pathlib import Path
import yaml
from jsonschema import Draft7Validator
from rollout_application import application, NS
from validate import injection_checks

ROOT = Path(__file__).resolve().parent
ROLLOUTS_SHA = '356117c8e9a21b74b58c2c01d640aa91994522b5'


def run(*args):
    return subprocess.check_output([str(a) for a in args], text=True, stderr=subprocess.PIPE)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--platform', type=Path, required=True)
    p.add_argument('--rollouts', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--istioctl', type=Path, required=True)
    a = p.parse_args()
    assert run('git','-C',a.rollouts,'rev-parse','HEAD').strip() == ROLLOUTS_SHA
    assert run('git','-C',a.platform,'rev-parse','HEAD').strip() == 'efd3a1f5a31eaf904bc787456034e4cebe932f7f'
    spec=importlib.util.spec_from_file_location('fixed_progressive', a.platform/'learning/progressive-delivery/render.py')
    fixed=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixed)
    with tempfile.TemporaryDirectory() as cache:
        core=fixed.download('argocd',Path(cache))
    crds = core + list(yaml.safe_load_all((a.rollouts/'manifests/install.yaml').read_text()))
    crds += list(yaml.safe_load_all((a.platform/'learning/istio/vendor/base/files/crd-all.gen.yaml').read_text()))
    schemas = {c['spec']['names']['kind']: next(v['schema']['openAPIV3Schema'] for v in c['spec']['versions'] if v['storage'])
               for c in crds if c and c['kind']=='CustomResourceDefinition'}
    lock=json.loads((a.platform/'learning/istio/versions.lock.json').read_text())
    assert hashlib.sha256(Path(run('which','helm').strip()).read_bytes()).hexdigest()==lock['helm']['binary']['sha256']
    assert hashlib.sha256(a.istioctl.read_bytes()).hexdigest()==lock['istio']['istioctl']['sha256']
    a.output.mkdir(parents=True, exist_ok=False)
    for revision in ['v1','v2','failed']:
        values = ROOT/f'rollout-profiles/{revision}.yaml'
        run('helm','lint','--strict',ROOT/'rollout-chart','-f',values)
        rendered = run('helm','template','mesh-rollout',ROOT/'rollout-chart','-n',NS,'--kube-version','1.36.4','-f',values)
        docs = list(yaml.safe_load_all(rendered))
        assert len(docs)==4 and {d['kind'] for d in docs}=={'Rollout','Service','VirtualService','DestinationRule'}
        for d in docs:
            assert d['metadata']=={'name':'mesh-rollout','namespace':NS}
            if d['kind'] in schemas:
                Draft7Validator(schemas[d['kind']]).validate(d)
        ro = next(d for d in docs if d['kind']=='Rollout')
        assert ro['spec']['strategy']['canary']['steps']==[{'setWeight':10},{'pause':{}},{'setWeight':50},{'pause':{}},{'setWeight':100},{'pause':{}}]
        assert ro['spec']['strategy']['canary']['trafficRouting']=={'istio':{'virtualService':{'name':'mesh-rollout','routes':['demo-only']},'destinationRule':{'name':'mesh-rollout','stableSubsetName':'stable','canarySubsetName':'canary'}}}
        vs=next(d for d in docs if d['kind']=='VirtualService')
        dr=next(d for d in docs if d['kind']=='DestinationRule')
        assert [r['weight'] for r in vs['spec']['http'][0]['route']]==[100,0]
        assert [r['destination']['subset'] for r in vs['spec']['http'][0]['route']]==['stable','canary']
        assert dr['spec']['subsets']==[{'name':n,'labels':{'app':'mesh-rollout','rollouts-pod-template-hash':'unassigned'}} for n in ['stable','canary']]
        assert ro['spec']['template']['spec']['containers'][0]['args']==['serve',revision]
        assert ro['spec']['template']['spec']['containers'][0]['readinessProbe']['httpGet']['path']=='/readyz'
        appdocs = application('learning-core-test','a'*40,revision)
        for obj in appdocs:
            Draft7Validator(schemas[obj['kind']]).validate(obj)
        allowed = {(k['group'],k['kind']) for k in appdocs[0]['spec']['namespaceResourceWhitelist']}
        assert allowed == {(d['apiVersion'].split('/')[0] if '/' in d['apiVersion'] else '',d['kind']) for d in docs}
        (a.output/f'{revision}.yaml').write_text(rendered)
        (a.output/f'{revision}-application.yaml').write_text(yaml.safe_dump_all(appdocs,sort_keys=False))
    # Istio's offline injector does not understand Rollout. Project only its identical
    # PodTemplate to a Deployment for injection/analyze; never distribute this projection.
    poddocs=copy.deepcopy(docs)
    for d in poddocs:
        if d['kind']=='Rollout':
            d['apiVersion']='apps/v1'
            d['kind']='Deployment'
            d['spec']={k:v for k,v in d['spec'].items() if k in ['replicas','selector','template']}
    with tempfile.TemporaryDirectory() as tmp:
        tmp=Path(tmp)
        for profile in ['default','90-10','50-50']:
            (tmp/f'{profile}.yaml').write_text(yaml.safe_dump_all(poddocs))
        injection_checks(SimpleNamespace(output=tmp,istioctl=a.istioctl),a.platform/'learning/istio',lock['images']['fixture']['ref'],lock)
    for opts in [['--set','revision=other'],['--set','image=other'],['--set','weight=50'],['-n','default']]:
        proc = subprocess.run(['helm','template','mesh-rollout',str(ROOT/'rollout-chart'),'-n',NS,*opts],capture_output=True)
        assert proc.returncode, 'unsafe chart values accepted'
    print('PASS: 3 profiles, fixed official Rollout/Istio schemas, 4-resource boundary, AppProject allowlist, 4 rejected inputs; offline analyze and regular/native injection of Rollout PodTemplate')


if __name__=='__main__':
    main()
