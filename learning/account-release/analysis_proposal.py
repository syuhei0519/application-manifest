"""Offline delta to the pinned PLAT-170 bootstrap. This does not authorize/apply it."""
from copy import deepcopy
import re

import yaml

from contract import require

NS = 'learning-account-release'
CONTROL = 'learning-account-control'
PLUGIN = 'learning-local/account-policy'
BASE_IMAGE = 'quay.io/argoproj/argo-rollouts:v1.10.1@sha256:fbcc4cf0a29da25d86230992b1dfdeadf30b6f1baa74ea7c9c98e3e580769396'


def analysis_overlay(outputs, controller_image, binary_sha256):
    require(isinstance(controller_image,str) and re.fullmatch(
        r'learning.local/account-rollouts@sha256:[a-f0-9]{64}',controller_image), 'reviewed-local-image-pin-required')
    require(isinstance(binary_sha256,str) and re.fullmatch(r'[a-f0-9]{64}',binary_sha256),'binary-checksum-required')
    result=deepcopy(outputs)
    def exact(kind,name,namespace):
        found=[obj for docs in result.values() for obj in docs if obj and obj['kind']==kind and
               obj['metadata']['name']==name and obj['metadata'].get('namespace')==namespace]
        require(len(found)==1,'exact-bootstrap-resource-required:'+name)
        return found[0]
    rollout_role=exact('Role','learning-rollouts',NS)
    argo_role=exact('Role','argocd-learning',NS)
    for role, resources, verbs in [
        (rollout_role,['analysisruns'],['create','update','patch','delete']),
        (rollout_role,['analysisruns/status','analysisruns/finalizers'],['update','patch']),
        (argo_role,['analysistemplates'],['create','update','patch','delete'])]:
        role['rules'].append(dict(apiGroups=['argoproj.io'],resources=resources,verbs=verbs))
    deployment=exact('Deployment','learning-rollouts',NS)
    pod=deployment['spec']['template']['spec']
    require(pod['serviceAccountName']=='learning-rollouts' and len(pod['containers'])==1,'controller-scope-required')
    container=pod['containers'][0]
    require(container['image']==BASE_IMAGE,'pinned-upstream-controller-required')
    require(container['securityContext']['readOnlyRootFilesystem'] is True,'readonly-controller-required')
    require(any(v.get('name')=='plugin-bin' and 'emptyDir' in v for v in pod['volumes']),'ephemeral-plugin-cache-required')
    container['image']=controller_image
    container['env']=container.get('env',[])+[dict(name='ACCOUNT_POLICY_SHA256',value=binary_sha256)]
    configs=[obj for docs in result.values() for obj in docs if obj and obj['kind']=='ConfigMap' and
             obj['metadata']['name']=='argo-rollouts-config' and obj['metadata'].get('namespace')==NS]
    require(len(configs)<=1,'duplicate-plugin-config')
    if configs:
        config=configs[0]
    else:
        config=dict(apiVersion='v1',kind='ConfigMap',metadata=dict(name='argo-rollouts-config',namespace=NS),data={})
        result['target.yaml'].append(config)
    require(not config.get('data',{}).get('metricProviderPlugins'),'existing-metric-plugins-refuse-overwrite')
    config.setdefault('data',{})['metricProviderPlugins']=yaml.safe_dump([
        dict(name=PLUGIN,location='file:///opt/account-policy',sha256=binary_sha256)],sort_keys=False)
    # Core must include the declarative AnalysisTemplate, not only read it.
    cm=exact('ConfigMap','argocd-cm',CONTROL)
    inclusions=yaml.safe_load(cm['data']['resource.inclusions'])
    if not any('argoproj.io' in item['apiGroups'] and 'AnalysisTemplate' in item['kinds'] for item in inclusions):
        inclusions.append(dict(apiGroups=['argoproj.io'],kinds=['AnalysisTemplate'],clusters=['*']))
    cm['data']['resource.inclusions']=yaml.safe_dump(inclusions)
    require(all(o['kind']!='Secret' for docs in result.values() for o in docs if o),'unexpected-secret')
    return result
