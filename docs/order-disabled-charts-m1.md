# Order disabled charts (M1)

Related issue: https://github.com/syuhei0519/backend-app/issues/2

`charts/order` and `charts/order-postgresql` render no resources by default or with
local values. Order tag/digest and release evidence remain empty. No real image is
selected. Existing Account/frontend/PostgreSQL charts and local values are unchanged.
At platform-gitops `b989e29b8addbd6396c5c2b4833b969bc15fc8fa`, only the existing three
chart/value paths are registered. M1 therefore proposes deployment effect `none`
for that fixed configuration; Manager must recheck the current configuration.
No Namespace, AppProject, Secret or GitOps Application is added here.

Order uses independent names/selectors, ClusterIP 8080, UID/GID 10001, read-only
root, dropped capabilities, disabled token automount and a writable `/tmp` emptyDir.
ConfigMap/ServiceAccount wave -20 precedes the Sync migration hook (-10) and
Deployment (0). The hook uses `migrate`, Never, deadline 180 and backoff 1 with
BeforeHookCreation/HookSucceeded. Both workloads use the identical tag@digest and
GitHub v2 release annotations from the same values. Startup/liveness use `/livez`;
readiness uses `/readyz`. Schema fixes safety settings and rejects unknown fields;
enabled rendering also requires matching source/tag and release URL/run/attempt.
These structural checks do not authenticate a published release.

The dedicated database uses the existing fixed PostgreSQL 17.10 image and security,
CPU/memory and 2Gi standard PVC settings. Resource names are `order-postgresql`,
`order-postgresql-init`, and `order-postgresql-data`; image filesystem paths remain
`/var/lib/postgresql/data` and `/var/run/postgresql`. The initialization script
requires `app-username=order_app`, then creates the non-superuser/non-createdb/
non-createrole/noinherit role owning `orders`. Runtime and migration reference only
app keys of `order-db`. The database references its existing admin-password key.
No credential value or Secret is generated. Init scripts run only on empty PGDATA;
changing a Secret does not rotate an existing DB role. Runtime retains the lab
DB-owner/DDL residual risk; a separate DML role is future work. Same namespace is
not a security isolation guarantee.

PVC has `Prune=false,Delete=false`; StatefulSet has `Prune=confirm,Delete=confirm`.
These protect against Argo deletion, not administrator deletion or storage loss.
No volumeClaimTemplates or existing Account data operations are added.

## Reproduction

Use the repository CI tool versions (Go 1.27.1, Helm 4.2.4 and pinned Docker images).

```sh
bash ci/github/test.sh
sh ci/validate.sh
(cd tools/checksum && go test ./...)
SECURITY_RENDER_REQUIRED=true bash ci/github/source-scan.sh
```

The test entrypoint additionally runs `ci/order-chart-test.sh`: default/disabled and
enabled lint/render, unchanged M0 full PodSpec inventory and structural security,
strict Kubeconform 1.36.0, and ten malicious render mutations. Go checksum tests
exercise release/schema negatives and workload/DB path contracts. Enabled render
files remain under `.rendered/synthetic-order` so recursive rendered security scans
inspect them. They are excluded from the live `all.yaml` aggregate and never used
by the trusted live renderer. `ci/fixtures/order` is explicitly synthetic sample
data: its fake SHA/digest/run/release is not a real release or pullable image.
M0 inventory, provenance, security and PVC validators are unchanged.

## Remaining gates and ownership

All product ACs remain unverified here, earliest premerge, deferred=null; Issue
integration remains aggregate_required. Self-tests do not replace independent
Test/Review or Manager Readiness. Preparation conditions M1-S1 through M1-S4 are
engineering evidence only; they cannot satisfy Synced/Healthy/runtime digest.

B2 owner must publish and verify the regular image/release. M2 owner must select
that fixed digest through the protected trusted consumer, then independent Test
must test candidate M/P revisions in the authorized isolated kind/Argo environment:
Order normal/invalid API, process/DB restart persistence, Account CRUD preservation,
migration success/failure and recovery, Synced/Healthy and actual imageID binding.
Deployment owner confirms Secret metadata, capacity, anonymous pull, target context,
backup/restore and authorization before P1. After final integration, T7 repeats the
actual deployed revision/digest and API observations. None was executed by M1.

On any failure, stop related merge/enabling/deploy actions, preserve Account data
and Order PVC, retain failed Job and nonsecret status, and fix/retest with forward
migration. No known-good Order rollback target exists yet. Only an authorized
operator may suspend/disable Order sync; retry the whole Application after repair.
Do not mark migration history successful by hand or reuse stale release evidence.
