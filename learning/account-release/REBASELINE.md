# Account release: safe no-op rebaseline

This procedure applies only when `application-manifest/main` advances after a release request observed its base revision and before the adapter creates a proposal branch.

1. The adapter reads current `main` and both exact `runtime-profile.yaml` revisions before its first mutating Git call.
2. If the profiles differ, the request terminates as `git-profile-changed`, with a proven zero-write result. It must not be retried or rebased.
3. If the profiles are byte-equivalent as parsed values, the request terminates as `git-base-advanced-profile-unchanged`, also with a proven zero-write result. This is the no-op: no branch, commit, PR, reservation, or runtime write is carried forward.
4. Re-observe the runtime after it has compared the fresh `main`. A human may submit a **new request id** with that fresh Git identity and the same desired change. The terminal old id remains immutable for audit and idempotency.
5. If either profile read fails, terminate as the fixed safe `git-pre-write-read-failed`; do not expose the upstream error or guess equivalence.

After the first POST, this procedure no longer applies. Response loss, a partially created branch, a missing PR, or a process crash stays `outcome-unknown`; read-only reconciliation owns recovery and the reservation remains held. PR absence by itself never proves zero writes.

This procedure never force-pushes, deletes a branch, changes a stored SQLite row by hand, or treats a different request payload under the same id as a retry.
