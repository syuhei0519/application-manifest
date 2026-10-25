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

`account-role-bootstrap.sql` is a review fixture for a fresh empty database. The
PostgreSQL administrator owns the database, `account_migrator` owns only the
`account` schema, and `account_runtime` receives only schema usage plus table and
sequence DML. Runtime cannot create a database, role, schema, or table and is not
granted access to another service schema. This fixture is not a production DDL
or migration and must not be run against the existing database.

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
