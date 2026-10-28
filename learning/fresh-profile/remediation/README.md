# Account runtime ACL remediation v1

`0001_account_runtime_privileges.sql` is an operator-reviewed ACL transaction,
not backend migration 2. The retained Account image embeds only migration 1;
neither that migration nor the retained runtime image is changed by this fix.
Do not apply the clean-install Job template to an existing completed Job: its
Pod template is immutable, and the live runner deliberately forbids recreation.

The known old bootstrap gave runtime CRUD on history and every future migrator
table, and USAGE/SELECT/UPDATE on sequences. The transaction removes history
INSERT/UPDATE/DELETE, restores required history SELECT and accounts CRUD, removes
direct access to the accounts identity sequence, and revokes migrator defaults
for runtime on tables and sequences in `account`. Identity generation through an
accounts INSERT needs no direct sequence ACL, verified on PostgreSQL 18.6.

It checks database/schema/relation/sequence ownership, exact history `{1}`,
restricted login roles with no memberships, and identity-column shape before
changing privileges. It uses the migrator's advisory lock `7742340081`, bounded
lock/statement timeouts, and a single transaction. Object revocations run as the
owning `account_migrator`, including its default ACLs. Effective-privilege checks
catch PUBLIC/column grants and unintended rights on other account objects;
any failure rolls back. Unexpected global/PUBLIC defaults fail before edits.
No history row, accounts row, owner, credential, or role definition is changed.

PostgreSQL [default ACLs](https://www.postgresql.org/docs/18/sql-alterdefaultprivileges.html)
affect future objects only; schema-local revokes cannot override global grants.
Existing object ACLs therefore need the separate explicit revocations here.
See also [GRANT](https://www.postgresql.org/docs/18/sql-grant.html) and
[identity columns](https://www.postgresql.org/docs/18/ddl-identity-columns.html).

Minimal later live approval: inspect the approved fresh Account database's
non-secret ownership/role/ACL/history metadata; execute this exact reviewed SQL
as `postgres` in database `account` through an already-approved administrator
connection; verify effective ACLs, unchanged history, `/readyz`, and one synthetic
create/read/update/delete with cleanup. Bind approval to SQL SHA-256 and the
existing sandbox/DB identity. No credential issuance/readback, image publication,
image replacement, rollout, Job recreation, or other schema/database operation
is required. The database name alone does not prove cluster identity: the caller
must verify the connection target. The SQL itself does not connect to a host.

Do not automatically restore broad grants on rollback. Before COMMIT, any error
rolls back atomically. After COMMIT, unexpected application failures need separate
review; preserve the mandatory history SELECT. Existing sessions observe updated
ACLs on subsequent statements; no password change or restart is part of this fix.
Do not run the old bootstrap again because it would restore the vulnerability.
Changing the source prevents that on later clean installations; it does not
replace the old ConfigMap in a running sandbox.

## Isolated reproduction

Archive backend source commit `635814f5354f0fe238694d35137b283576a9127f` separately,
then build its unchanged `./cmd/server` with Go 1.27 using the source's required
commit/version stamps (an unstamped binary deliberately rejects telemetry startup):

```sh
CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -buildvcs=false -trimpath \
  -ldflags='-X main.buildCommit=635814f5354f0fe238694d35137b283576a9127f -X main.version=0.0.0+git.635814f5354f0fe238694d35137b283576a9127f' \
  -o /absolute/path/server ./cmd/server
```
Run from application-manifest:

```sh
python3 -I learning/fresh-profile/tests/account_privileges_integration.py \
  --server /absolute/path/server --results /absolute/path/results.json
python3 -I learning/fresh-profile/test_credential_contract.py -v
python3 -I learning/fresh-profile/test_validate_fresh_profile.py -v
```

The regression uses only synthetic password literals and disposable Docker
containers on an internal network with tmpfs PostgreSQL data. HTTP probes run
over loopback inside the API container; no host port is published. Cleanup removes
only resources created by this test. The original
bootstrap is read from application-manifest commit
`3707f73581da5d04db0e4771dda7c1e82bd0f47c` to reproduce the old grants.
The source binary uses the exact finite fresh-account profile and actual HTTP
routes; the manifest's grant-container shell also executes. The test's synthetic
version-2 SQL verifies migrator ownership and history INSERT/rollback/rerun, then
proves the unchanged C0 migrator rejects version 2 safely. It does not imply that
the retained binary ships or supports a version-2 upgrade.

This is executed PostgreSQL/source integration, not retained OCI-image or live
Kubernetes acceptance. The test carrier is official PostgreSQL 18.6 on Debian
trixie, pinned by digest; the live manifest retains its existing bookworm digest.
The exact retained Account OCI binary is not present in this Cloud environment;
the exact digest pull returned `manifest unknown`. Its source revision is archived
and tested using Go 1.27.1 (the Dockerfile specifies Go 1.27.3); image-build equivalence, Kubernetes
Job scheduling/deadline behavior and live admission remain separate checks.
The grant container adds a 25m CPU / 32Mi memory request and 64Mi limit only to the
short-lived migration Job. Future application tables require explicitly reviewed
grants. Schema changes must use the same advisory lock to avoid concurrent drift.
