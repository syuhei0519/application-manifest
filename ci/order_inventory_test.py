# Order の描画結果から、許可したイメージだけが inventory に取り出されることを確認します。
"""Mutate real rendered synthetic charts against the unchanged M0 inventory gate."""
import json
from pathlib import Path
import subprocess

root = Path(__file__).resolve().parent.parent
raw = (root / '.rendered/synthetic-order/order.yaml').read_text() + '\n---\n' + (root / '.rendered/synthetic-order/order-postgresql.yaml').read_text()

def check(text, success):
    result = subprocess.run([str(root / '.build/order-inventory')], input=text, text=True, capture_output=True)
    assert (result.returncode == 0) == success, 'inventory did not enforce synthetic fixture boundary'
    return result

entries = json.loads(check(raw, True).stdout)['entries']
assert len(entries) == 3
assert {(e['kind'], e['name'], e['containerType']) for e in entries} == {
    ('Deployment', 'order', 'containers'), ('Job', 'order-migration', 'containers'),
    ('StatefulSet', 'order-postgresql', 'containers')}
for old, new in [
    ('Prune=false,Delete=false', 'Prune=true,Delete=true'),
    ('Prune=confirm,Delete=confirm', 'Prune=true,Delete=true'),
    ('claimName: order-postgresql-data', 'claimName: postgresql-data'),
    ('runAsUser: 999', 'runAsUser: 0'),
    ('readOnlyRootFilesystem: true', 'readOnlyRootFilesystem: false'),
    ('automountServiceAccountToken: false', 'automountServiceAccountToken: true'),
    ('docker.io/library/postgres:', 'ghcr.io/syuhei0519/backend-app-order:'),
    ('kind: StatefulSet', 'kind: Deployment'),
    ('name: order-postgresql-data', 'name: missing-pvc'),
]:
    assert old in raw
    check(raw.replace(old, new), False)
# A PodSpec hidden in a ConfigMap cannot escape complete inventory classification.
check(raw + '\n---\napiVersion: v1\nkind: ConfigMap\nmetadata: {name: hidden}\nspec:\n  containers:\n  - {name: hidden, image: bad}\n', False)
print('PASS: synthetic inventory has all 3 PodSpecs; 10 malicious render mutations rejected')
