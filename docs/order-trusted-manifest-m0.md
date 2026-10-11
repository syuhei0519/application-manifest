# Order trusted manifest M0

This change implements the AC-3 evidence-verification infrastructure only. It does
not implement Order charts, select an Order image, enable a workload, or establish
Synced/Healthy/runtime digest observations. All product ACs remain unverified.
Related issue: https://github.com/syuhei0519/backend-app/issues/2

## Producer / consumer contract for B2

`ci/github/release.py` uses an explicit service/source/image table. For the current
owner, the mappings are:

| Service | Source | Image | Record / manifest version |
| --- | --- | --- | --- |
| backend | syuhei0519/backend-app | ghcr.io/syuhei0519/backend-app | github-release/v1 / github-v1 |
| frontend | syuhei0519/frontend-app | ghcr.io/syuhei0519/frontend-app | github-release/v1 / github-v1 |
| order | syuhei0519/backend-app | ghcr.io/syuhei0519/backend-app-order | github-release/v2 / github-v2 |

The full record schema prefix is `core-platform/`. Only v2 Order is accepted;
`service: order` is required. Order uses `account.lab/service: order` alongside the
existing annotation prefix. That prefix does not grant access to Account data.
Source repository is never inferred by stripping an image suffix. All v1 mandatory
bindings remain; Order additionally has a closed published-record field set in
`ci/github/order-release.schema.json`. The runtime validator enforces dynamic
cross-field, source/run, freshness and registry bindings beyond the JSON schema.

B2 must produce these names for a single successful protected-main CI run/attempt:

| Item | Account | Order |
| --- | --- | --- |
| Successful image job display name | image (backend) | image (order) |
| Checked OCI artifact | checked-image-ATTEMPT | checked-image-order-ATTEMPT |
| Canonical single-record artifact | published-evidence-ATTEMPT | published-evidence-order-ATTEMPT |
| Release tag | gha-SHA-RUN-ATTEMPT | gha-order-SHA-RUN-ATTEMPT |

`validate` and `ci-result` must also succeed. The consumer reads jobs from the
specific attempt endpoint, requires each exact name once, and checks run/head.
Both services' checked and canonical artifacts must exist, be unexpired and bound
to the same run/head. Canonical ZIPs contain exactly one regular
`release-record.json`; duplicate members, paths, symlinks, encrypted members,
invalid JSON and checksum mismatches fail. Order and Account published records
must share source-scan checksum but have distinct archive checksums/image digests.
The archive checksum is attested by the protected producer's canonical record;
M0 does not independently rebuild or download the complete checked OCI archive.
B2 must validate/publish the exact previously checked archive and preserve that
checksum. A replaced release record fails its canonical artifact comparison.

Order release record, SBOM and image-scan asset bytes/checksums, immutable Git tag
commit, latest main/run/attempt, successful workflow, GHCR SHA tag, config checksum
and OCI source/revision labels must agree. Order must be a single Linux/amd64
manifest with `/order-server` entrypoint; an image index is refused. Public registry
subprocesses receive no GitHub/read token and an empty Docker credential directory.
A report remains valid for at most 15 minutes and never beyond release expiry.

`RELEASE_SERVICE=order` selects the Order proposal helper: fixed
`environments/local/order.yaml`, branch `github/order-SHA-RUN-ATTEMPT`, PR only.
There is no fallback to backend values if Order values are absent. M0's local
`record`/`publish` helpers remain v1; implementing the B2 producer is a separate
backend-app change. Neither fixtures nor schema success authorize a release.

## Trusted inventory and platform policy

The trusted renderer's executed policy contains active `frontend/backend/postgresql`
and future `order/order-postgresql` names. Both absent is accepted only while a
future chart is absent from baseline. Chart/values mismatch, baseline removal,
unknown chart/values, unknown resource types and containers outside classified
PodSpec paths fail. PR-supplied lists cannot remove charts from inspection. Every
present optional chart is rendered, even if its default output is empty.

M1 must add disabled-by-default Order charts/values and separate enabled synthetic
CI fixtures, keeping placeholders out of live enabled values. Enabled fixtures
must go through the same inventory, security and PVC checks; their results are
local CI evidence, never authenticated release approval. No such live chart or
fixture workload is installed by M0.

PostgreSQL uses the separate platform allowlist:
`docker.io/library/postgres:17.10-bookworm@sha256:9b18b78397054fce88a9552e9d5a3ad5bb7fd258c5b3cc1c5028e46373d6ea8f`.
Both DB names use StatefulSets, UID/GID/fsGroup 999, non-root, RuntimeDefault,
read-only root filesystem, no privilege escalation, ALL capabilities dropped,
no host namespaces/hostPath, and no service-account token automount. A same-render
fixed PVC is mandatory; PVC retains `Prune=false,Delete=false` and StatefulSet
retains `Prune=confirm,Delete=confirm`, with no volumeClaimTemplates. New DB images
are never treated as app GHCR releases. Existing scan-gate checks remain in force.

The fixed Pod defaults remain required. Container overrides of `runAsUser`,
`runAsGroup`, `runAsNonRoot` and `seccompProfile` must preserve effective UID/GID
999, non-root and RuntimeDefault. Missing overrides inherit; equivalent explicit
overrides are accepted. Null, malformed or conflicting overrides fail closed.
PostgreSQL init/ephemeral containers and extra PostgreSQL sidecars remain outside
the fixed image/container identity allowlist, even with safe security settings.
Other application containers retain their existing inventory and scan checks.
The generic render-only scan-gate is not the PostgreSQL identity/security gate;
the trusted renderer must also run inventory before returning any provenance
decision. Duplicating that policy in scan-gate was rejected to avoid divergent
allowlists. A synthetic test-only render never authorizes a real release.

## Verification and remaining dependencies

Synthetic fixture bytes under `ci/github/fixtures` use a fixed 2030 clock and fake
commits/digests/run IDs. Tests explicitly inject that clock and fake API/registry
responses. They never contact a cluster or publish anything. B2 must reuse these
fixtures and reconcile actual job/artifact names before producer integration.
Actual protected B2 publication/consumer interoperability is not yet verified.

Reproduce from this repository with Go 1.27.1, Helm 4.2.4 and Docker:

```sh
bash ci/github/test.sh
python3 -I ci/github/release_test.py
python3 -I ci/trusted_render_test.py
sh ci/validate.sh
(cd tools/checksum && go test ./...)
(cd tools/inventory && go run . < ../../.rendered/all.yaml)
.security/scan-gate -check-render-only .rendered -out .security/public/render-check.json
```

For the full trusted sandbox, commit the candidate and run `ci/trusted-render.py`
with full base/source/verifier SHAs and `--producer-job-id 0 --test-only`.
This tests isolation and data handling; it is not protected-main verification.
No workflow/Secret/approval/rules changes are part of M0.

Rejected alternatives: infer source from image name (ambiguous for Order), reuse
backend v1 records for Order (service spoofing), accept only aggregate CI status
(hides a missing matrix leg), allow a PR-defined chart list (unscanned inputs),
reuse application-release proofs for PostgreSQL (different trust contract), or
extend expired evidence (invalid freshness). Platform policy is deliberately
explicit rather than broad image-family matching.

Implementation self-tests are separate from Manager-dispatched independent
Test/Review. AC-3 retains its earliest `premerge` phase; unavailable real evidence
does not move it to postdeploy. Issue completion is `aggregate_required`, and
Deployment/Merge readiness remains the Manager's separate decision.
