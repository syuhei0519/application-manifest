# PLAT-170 で Argo CD Core への登録を準備した際の補助スクリプトです。過去の実行手順として保持します。
# Historical approved session helper. Reapproval and new ownership guards are required for another session.
import json
import subprocess
from pathlib import Path

root = Path(__file__).resolve().parents[4]/"evidence"
k = ['kubectl','--kubeconfig',str(root/'plat170-kubeconfig'),'--context','kind-plat170-account']
def call(args, obj=None):
    r = subprocess.run(k+args,input=None if obj is None else json.dumps(obj),capture_output=True,text=True)
    if r.returncode:
        raise SystemExit('Core registration failed; stop and inspect state before retry')
    return r.stdout
assert call(['get','namespace','kube-system','-o','jsonpath={.metadata.uid}']) == '2552b4bc-7402-483f-9b20-25f4d851d829'
for name in ('argocd-secret','learning-in-cluster'):
    assert not call(['get','secret',name,'-n','learning-account-control','--ignore-not-found','-o','name']).strip()
objects = [
    dict(apiVersion='v1',kind='Secret',type='Opaque',metadata=dict(name='argocd-secret',namespace='learning-account-control',
         labels={'learning.local/owner':'plat170-account','app.kubernetes.io/part-of':'argocd'})),
    dict(apiVersion='v1',kind='Secret',type='Opaque',metadata=dict(name='learning-in-cluster',namespace='learning-account-control',
         labels={'learning.local/owner':'plat170-account','argocd.argoproj.io/secret-type':'cluster'}),
         stringData=dict(name='in-cluster',server='https://kubernetes.default.svc',namespaces='learning-account-release',
                         clusterResources='false',config='{}'))]
records=[]
for obj in objects:
    name=obj['metadata']['name']
    call(['create','-f','-'],obj)
    records.append(dict(name=name,uid=call(['get','secret',name,'-n','learning-account-control','-o','jsonpath={.metadata.uid}'])))
    (root/'plat170-core-registration-metadata.json').write_text(json.dumps(records,indent=2))
    print(name+' created (no credential values)')
