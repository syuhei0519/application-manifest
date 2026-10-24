# Existing kind adoption declarations (not deployed)

Target: `kind-learning-20261018-69c763`, namespace
`learning-services-20261018`; cluster UID
`bf8f9b86-9624-4ee6-a1e4-509aa63ce318`.

`services/resources.yaml` adopts the 24 existing API/DB parent declarations,
preserving image, selector, Service IP/ports, PVC binding and Secret references.
No Pod, ReplicaSet, status, Secret value or completed migration Job is declared.
Do not replay historical migrations merely to obtain Argo tracking. Initial
adoption disables prune/delete; compare the exact rendered/live immutable fields
and runtime baseline before each sync. These files do not create a manual apply
entrypoint. The protected Application/project wiring is still in preparation.

`account-foundation/database.yaml` is a separate new Account acceptance database
in `learning-account-release`, with a 1Gi RWO standard PVC instead of the old
dedicated-trial emptyDir. It references a new externally provisioned Secret; no
credential has been issued. Initialization/migration/API/restart acceptance has
not run. It neither moves nor replaces any existing DB/PVC.

The user's revised condition permits disposable learning data loss after actual
reconstruction is demonstrated. In-place adoption is selected because it requires
fewer changes and no data deletion. No Retain patch or old-PVC deletion is needed.
Secret/CA/connection information and Sonar state remain distinct from test rows.

The consolidated authority, certificate, data and session proposal is maintained
in platform-gitops `learning/existing-kind/execution-approval.md`. It remains a
draft until the root/Account Application source ownership and adapter targets
agree. Do not revive the deleted dedicated kind or count historical trial results
as acceptance of this target.
