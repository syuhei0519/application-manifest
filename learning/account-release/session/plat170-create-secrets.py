# Historical approved session helper. Reapproval and new ownership guards are required for another session.
import json
import secrets
import subprocess
from pathlib import Path

root = Path(__file__).resolve().parents[4]/"evidence"
k = ['kubectl', '--kubeconfig', str(root/'plat170-kubeconfig'), '--context', 'kind-plat170-account']
def call(args, data=None):
    r = subprocess.run(k+args, input=None if data is None else json.dumps(data), capture_output=True, text=True)
    if r.returncode:
        raise SystemExit('Dedicated credential operation failed; inspect metadata before retry (no values logged)')
    return r.stdout
assert call(['get','namespace','kube-system','-o','jsonpath={.metadata.uid}']) == '2552b4bc-7402-483f-9b20-25f4d851d829'
# Only the DB and Redis operations already explicitly approved. The two Core
# settings/registration Secrets were rejected by auto-review and are excluded.
targets = [('learning-account-release', 'learning-account-db'), ('learning-account-control', 'argocd-redis')]
for ns, name in targets:
    assert not call(['get','secret',name,'-n',ns,'--ignore-not-found','-o','name']).strip(), 'existing Secret; refuse overwrite'
payloads = [dict(**{'admin-password': secrets.token_urlsafe(36), 'app-password': secrets.token_urlsafe(36)}),
            dict(auth=secrets.token_urlsafe(36))]
records = []
for (ns, name), data in zip(targets, payloads):
    labels = {'learning.local/owner':'plat170-account'}
    obj = dict(apiVersion='v1', kind='Secret', type='Opaque', metadata=dict(name=name, namespace=ns, labels=labels), stringData=data)
    call(['create','-f','-'], obj)
    uid = call(['get','secret',name,'-n',ns,'-o','jsonpath={.metadata.uid}'])
    records.append(dict(namespace=ns,name=name,uid=uid))
    (root/'plat170-secret-metadata.json').write_text(json.dumps(records, indent=2))
    print(ns+'/'+name+' created (value omitted)')
