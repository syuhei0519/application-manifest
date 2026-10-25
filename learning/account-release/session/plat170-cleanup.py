# PLAT-170 の試験後に専用資源を片付けた際の補助スクリプトです。現在の配備入口には使いません。
# Historical approved session helper. Reapproval and new ownership guards are required for another session.
"""Execute only after the approved trial evidence is saved; exact dedicated cluster only."""
import json
from pathlib import Path
import subprocess
import time

ROOT=Path(__file__).resolve().parents[4]/"evidence"
K=['kubectl','--kubeconfig',str(ROOT/'plat170-kubeconfig'),'--context','kind-plat170-account']
def run(*args):
    return subprocess.check_output(args,text=True,stderr=subprocess.PIPE).strip()
def context():
    p=subprocess.run(['kubectl','config','current-context'],capture_output=True,text=True)
    return {'returncode':p.returncode,'stdout':p.stdout.strip()}
uid=run(*K,'get','namespace','kube-system','-o','jsonpath={.metadata.uid}')
assert uid=='2552b4bc-7402-483f-9b20-25f4d851d829'
node=run('docker','inspect','--format','{{.Id}}','plat170-account-control-plane')
assert node.startswith('01f61a9ae51f')
existing=run('docker','inspect','--format','{{.Id}}','95b23e7f5ff5')
before_context=context()
before_clusters=run('kind','get','clusters').splitlines()
assert 'plat170-account' in before_clusters
record=dict(start=time.time(),owned_node_id=node,owned_namespace_uid=uid,
    existing_node_id=existing,context_before=before_context,clusters_before=before_clusters)
(ROOT/'plat170-cleanup-started.json').write_text(json.dumps(record,indent=2))
p=subprocess.run(['kind','delete','cluster','--name','plat170-account','--kubeconfig',str(ROOT/'plat170-kubeconfig')],
    capture_output=True,text=True)
(ROOT/'plat170-kind-delete.log').write_text(p.stdout+p.stderr)
if p.returncode:
    raise SystemExit('kind cleanup returned failure; inspect status before any retry')
after_clusters=run('kind','get','clusters').splitlines()
assert 'plat170-account' not in after_clusters
missing=subprocess.run(['docker','inspect','--format','{{.Id}}','plat170-account-control-plane'],capture_output=True,text=True)
assert missing.returncode!=0
assert run('docker','inspect','--format','{{.Id}}','95b23e7f5ff5')==existing
assert context()==before_context
assert set(after_clusters)==set(before_clusters)-{'plat170-account'}
record.update(end=time.time(),clusters_after=after_clusters,owned_node_absent=True,
    existing_node_unchanged=True,default_context_unchanged=True,kubeconfig_file_removed=False,
    note='Dedicated DB/Redis/SA/Istio credentials, RBAC, webhooks and emptyDir test data removed with cluster')
(ROOT/'plat170-cleanup.json').write_text(json.dumps(record,indent=2))
print('Dedicated kind removed; existing kind and default context unchanged. Remove the exact dedicated kubeconfig next.')
