#!/usr/bin/env python3
# 上流の schema・権限境界・計測クエリと、Account リリース用設定の整合性を静的に確認します。
"""Offline integration schemas, access boundary and PromQL export for promtool."""
import argparse
import json
from pathlib import Path
import subprocess

import yaml
from jsonschema import Draft7Validator

from bootstrap_proposal import PLATFORM_SHA
from integration import TRAFFIC_QUERY, TRAFFIC_TIMESTAMP_QUERY, application, sdk_query_set
from test_integration import CATALOG, IMAGES
from sdk_queries import conservation, policy_query_templates
from release_modes import desired_documents
from analysis_proposal import analysis_overlay


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--platform', type=Path, required=True)
    p.add_argument('--proposal', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--helm', default='helm')
    a = p.parse_args()
    assert subprocess.check_output(['git', '-C', str(a.platform), 'rev-parse', 'HEAD'], text=True).strip() == PLATFORM_SHA
    crds = list(yaml.safe_load_all((a.proposal/'crds.yaml').read_text()))
    crds += list(yaml.safe_load_all((a.platform/'learning/istio/vendor/base/files/crd-all.gen.yaml').read_text()))
    schemas = {o['spec']['names']['kind']: next(v['schema']['openAPIV3Schema'] for v in o['spec']['versions'] if v['storage'])
               for o in crds if o and o['kind'] == 'CustomResourceDefinition'}
    a.output.mkdir(parents=True, exist_ok=False)
    profiles = [(role, application('c'*40, CATALOG, image)) for role,image in IMAGES.items()]
    profiles += [(name, desired_documents('c'*40,CATALOG,IMAGES['candidate'],strategy,mode,seed,enabled))
                 for name,strategy,mode,seed,enabled in [
                     ('manual-analysis','Canary','manual','',True),('auto','Canary','auto','',True),
                     ('rolling','Rolling',None,'',False),('rolling-seed','Rolling',None,'abc123',False),
                     ('canary-seed','Canary','manual','abc123',True)]]
    for role, appdocs in profiles:
        values = appdocs[1]['spec']['source']['helm']['valuesObject']
        values_path = a.output/(role+'-values.json')
        values_path.write_text(json.dumps(values))
        rendered = subprocess.check_output([a.helm, 'template', 'account-release', str(Path(__file__).parent/'chart'),
                    '-n', 'learning-account-release', '--kube-version', '1.36.4', '-f', str(values_path)], text=True)
        docs = list(yaml.safe_load_all(rendered))
        for obj in docs + appdocs:
            if obj['kind'] in schemas:
                Draft7Validator(schemas[obj['kind']]).validate(obj)
        (a.output/(role+'.yaml')).write_text(rendered)
        (a.output/(role+'-application.yaml')).write_text(yaml.safe_dump_all(appdocs, sort_keys=False))
    access = json.loads((a.proposal/'access-inventory.json').read_text())
    target_roles = [o for o in access if o['kind'] == 'Role' and o['metadata']['namespace'] == 'learning-account-release']
    assert {o['metadata']['name'] for o in target_roles} == {'learning-rollouts', 'argocd-learning', 'learning-collector-pods'}
    for obj in target_roles:
        for rule in obj['rules']:
            if 'secrets' in rule['resources']:
                assert rule.get('resourceNames') == ['argo-rollouts-notification-secret']
            if 'analysistemplates' in rule['resources'] or 'analysisruns' in rule['resources']:
                assert set(rule['verbs']) <= {'get', 'list', 'watch'}
    # Review the additive analysis permissions against the actual pinned bootstrap.
    baseline={name:list(yaml.safe_load_all((a.proposal/name).read_text())) for name in ['control.yaml','target.yaml']}
    overlay=analysis_overlay(baseline,'learning.local/account-rollouts@sha256:'+'a'*64,'b'*64)
    (a.output/'analysis-bootstrap-fixture.yaml').write_text(yaml.safe_dump_all(
        [obj for docs in overlay.values() for obj in docs],sort_keys=False))
    # Export every dynamic count case, plus raw traffic/freshness selectors, for the pinned PromQL parser.
    expressions = [TRAFFIC_QUERY, TRAFFIC_TIMESTAMP_QUERY]
    policy={name:expression.replace('{{args.namespace}}','learning-account-release').replace('{{args.service}}','account')
            .replace('{{args.stable-hash}}','aaaaa').replace('{{args.latest-hash}}','bbbbb')
            .replace('{{stable-count}}','1').replace('{{latest-count}}','1')
            for name,expression in policy_query_templates().items()}
    expressions.extend(policy.values())
    for counts in [(2, 1), (1, 1), (0, 2)]:
        expressions.extend(sdk_query_set('aaaaa', 'bbbbb', *counts).values())
    rules = {'groups': [{'name': 'account-release-static', 'rules': [
        {'record': 'fixture:account_release:'+str(i), 'expr': e} for i, e in enumerate(expressions)]}]}
    (a.output/'queries.yaml').write_text(yaml.safe_dump(rules))
    # Replay the actual 1ms OTLP clock-span case through the pinned PromQL engine.
    cases = [
        ('observed-clock-skew', '1396', '1396', '1396', '285.57459386181296', '285.5756516199288', True),
        ('boundary', '1396', '1396', '1396', '0', '0.01', False),
        ('raw-one-missing', '1396', '1395', '1395', '257', '257', False),
        ('bucket-one-missing', '1396', '1396', '1395', '257', '257', False),
        ('window-one-missing', '1396', '1396', '1396', '257', '256', False),
        ('nan', 'NaN', 'NaN', 'NaN', '257', '257', False),
        ('inf', 'Inf', 'Inf', 'Inf', '257', '257', False),
    ]
    expressions = []
    for name, r, c, inf, ri, ci, passed in cases:
        gate = conservation(*(f'vector({x})' for x in (r,c,inf,ri,ci)))
        expressions.append({'expr': 'vector(1) and on() ('+gate+')', 'eval_time': '0s',
                            'exp_samples': [{'labels': '{}', 'value': 1}] if passed else []})
    absent = '(vector(1) unless vector(1))'
    gate = conservation('vector(1396)', absent, 'vector(1396)', 'vector(257)', 'vector(257)')
    expressions.append({'expr': 'vector(1) and on() ('+gate+')', 'eval_time': '0s', 'exp_samples': []})
    labels='job="account-backend",account_namespace="learning-account-release",learning_service="account",rollout_revision="bbbbb",instance="current-pod"'
    mixed_missing=dict(interval='30s',input_series=[
        dict(series='pe_http_server_requests_total{'+labels+',method="GET",route="/api/accounts",status_class="2xx"}',values='0+9x20'),
        dict(series='pe_http_server_requests_total{'+labels+',method="GET",route="/api/accounts",status_class="5xx"}',values='0+1x20'),
        dict(series='pe_go_goroutines{'+labels+'}',values='10+0x20'),
        dict(series='up{job="otel-application",account_namespace="learning-account-release"}',values='1+0x20')],
        promql_expr_test=[dict(expr=policy['latest-'+metric],eval_time='10m',
            exp_samples=[] if expected is None else [dict(labels='{}',value=expected)])
            for metric,expected in [('requests',100),('availability',.9),('latency',None)]])
    (a.output/'conservation-tests.yaml').write_text(yaml.safe_dump({
        'rule_files': [], 'tests': [{'interval': '1m', 'input_series': [], 'promql_expr_test': expressions},mixed_missing]}))
    print(f"PASS: fixed upstream schemas, bounded Roles, {len(rules['groups'][0]['rules'])} PromQL expressions exported")


if __name__ == '__main__':
    main()
