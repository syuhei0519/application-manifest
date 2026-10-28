# PLAT-214 fresh credential and database-role contract

This directory defines an offline contract only. It creates no Secret, role,
database, credential, or cluster resource. The current cluster, existing
`account-db` Secret, PVC, and PLAT-170 session remain unchanged.

`credential-contract.json` records non-secret ownership, purpose, destination,
maximum age, recovery source, the approved manual first-input path, and three
distinct Secret references (`account-db-bootstrap`, `account-db-migration`, and
`account-db-runtime`) with finite key names and consumers. Secret values must
never be stored in Git, CI output, or evidence. The finite choice for this
profile is a separately approved manual-bootstrap process using fresh-isolated
credentials. Reading or transferring an existing credential or CA requires a
different explicit approval; introducing SOPS, ESO, or a new secret store is
not implied.

The Account Rollout and migration Job both select the one finite application
identity profile `fresh-account`. The `server` operation resolves only
`account_runtime`; `migrate` resolves only `account_migrator`. Both use the
exact `account-postgresql.fresh-account.svc:5432/account` database identity,
schema `account`, database owner `postgres`, schema owner
`account_migrator`, and the fixed fresh Collector endpoint. The manifest uses
explicit `PG*` inputs plus `POD_NAMESPACE` and `POD_UID` from Downward API.
`DATABASE_URL`, profile aliases, arbitrary `PGOPTIONS`, and fallback
credentials are not part of this profile. Migration and runtime Secrets expose
only `username` and `password`; no connection URI is required.

`account-role-bootstrap.sql` is a review fixture for a fresh empty database. The
PostgreSQL administrator owns the database, `account_migrator` owns only the
`account` schema, and `account_runtime` receives schema usage. After the retained
Account migrator commits version 1, a second regular container in the same
`account-migration` Job invokes the `grant_runtime` phase of that SQL as
`account_migrator`: CRUD on `account.accounts` and SELECT on
`account.schema_migrations`. The Job succeeds only when both containers succeed;
its 260-second deadline covers a 190-second history wait and a separate 30-second
grant phase, with 5-second kill grace per phase and 30 seconds for startup/exit.
The unchanged migrator allows 170 seconds; the history wait therefore tolerates a
late successful commit and lock release. Connection/query time is included in the
wall-time limits. A stalled phase fails; no grant SQL runs after a failed wait.
No additional
Secret, Job identity, image revision, or Secret-backed init container is needed.
The default history policy stays version 1. Runtime readiness reads migration
history, so its SELECT privilege is required. The identity column generates IDs
without a direct sequence privilege; runtime cannot call `setval` or `nextval`.
There are no broad current-table or future-table/sequence grants.
Runtime cannot create a database, role, schema, or table and is not
granted access to another service schema. This fixture is not a production DDL
or migration and must not be run against the existing database.

Changing the fixture does not repair an already-bootstrapped database or rerun
a retained completed Job. See [ACL remediation v1](remediation/README.md) for
the separately reviewed existing-database transaction and isolated regression.

Rotation order is fixed: verify the new database credential, restart the actual
application process, observe Ready, verify the restarted process uses the new
credential, revoke the old credential, then prove the old credential is
rejected. Missing, denied, expired, or unavailable inputs keep migration
rejected and the application NotReady; there is no default credential.

The unit test validates the contract and the evidence gate with synthetic data.
It does **not** satisfy the live acceptance requirement. A separately approved
isolated run must still record actual process restart/re-authentication and old
credential rejection without printing values. Until then `liveEvidence` stays
`required-unrun`.

Run the offline checks with:

```text
python -I learning/fresh-profile/test_credential_contract.py -v
python -I learning/fresh-profile/credential_contract.py
```
