#!/usr/bin/env python3
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
from sdk_queries import conservation


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
    for role, image in IMAGES.items():
        appdocs = application('c'*40, CATALOG, image)
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
    # Export every dynamic count case, plus raw traffic/freshness selectors, for the pinned PromQL parser.
    expressions = [TRAFFIC_QUERY, TRAFFIC_TIMESTAMP_QUERY]
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
    (a.output/'conservation-tests.yaml').write_text(yaml.safe_dump({
        'rule_files': [], 'tests': [{'interval': '1m', 'input_series': [], 'promql_expr_test': expressions}]}))
    print('PASS: fixed upstream Rollout/Istio/Application/AppProject schemas, bounded target Roles, 17 PromQL expressions exported')


if __name__ == '__main__':
    main()
