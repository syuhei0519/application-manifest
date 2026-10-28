-- The only source for the generated account-role-bootstrap ConfigMap.
-- Values are psql variables from the separately approved G3 bootstrap.
\set ON_ERROR_STOP on

-- The migration Job invokes this second phase only after migration 1 commits.
-- Keep the retained /server migrate binary and its embedded SQL unchanged.
\if :{?grant_runtime}
BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '15s';
SELECT pg_advisory_xact_lock(7742340081);
DO $guard$
BEGIN
  IF current_database() <> 'account' OR current_user <> 'account_migrator'
     OR (SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname = current_database()) <> 'postgres'
     OR (SELECT nspowner FROM pg_namespace WHERE nspname = 'account') IS DISTINCT FROM 'account_migrator'::regrole
     OR (SELECT relowner FROM pg_class WHERE oid = 'account.accounts'::regclass) <> 'account_migrator'::regrole
     OR (SELECT relowner FROM pg_class WHERE oid = 'account.schema_migrations'::regclass) <> 'account_migrator'::regrole
     OR (SELECT array_agg(version ORDER BY version) FROM account.schema_migrations) IS DISTINCT FROM ARRAY[1]
  THEN RAISE EXCEPTION 'fresh Account grant identity/history rejected'; END IF;
END
$guard$;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE account.accounts TO account_runtime;
-- Store.Ready -> schemahistory.Inspect reads version; SELECT must remain.
GRANT SELECT ON TABLE account.schema_migrations TO account_runtime;
-- GENERATED ALWAYS AS IDENTITY requires no direct sequence grant for INSERT.
DO $guard$
BEGIN
  IF has_table_privilege('account_runtime', 'account.schema_migrations', 'INSERT,UPDATE,DELETE')
     OR has_sequence_privilege('account_runtime', pg_get_serial_sequence('account.accounts', 'id'), 'USAGE,SELECT,UPDATE')
     OR EXISTS (SELECT FROM pg_default_acl d, LATERAL aclexplode(d.defaclacl) a
                WHERE d.defaclrole = 'account_migrator'::regrole
                  AND d.defaclobjtype IN ('r', 'S')
                  AND d.defaclnamespace IN (0, 'account'::regnamespace)
                  AND a.grantee IN (0, 'account_runtime'::regrole))
  THEN RAISE EXCEPTION 'existing broad privileges require reviewed remediation'; END IF;
END
$guard$;
COMMIT;
\else
SELECT format('CREATE ROLE %I LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT PASSWORD %L',
              :'migration_role', :'migration_password')
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = :'migration_role') \gexec
SELECT format('CREATE ROLE %I LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT PASSWORD %L',
              :'runtime_role', :'runtime_password')
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = :'runtime_role') \gexec
SELECT format('CREATE DATABASE %I OWNER postgres', :'database_name')
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = :'database_name') \gexec

\connect :"database_name"

REVOKE ALL ON DATABASE :"database_name" FROM PUBLIC;
GRANT CONNECT ON DATABASE :"database_name" TO :"migration_role", :"runtime_role";
REVOKE CREATE ON SCHEMA public FROM PUBLIC, :"migration_role", :"runtime_role";

SELECT format('CREATE SCHEMA IF NOT EXISTS %I AUTHORIZATION %I',
              :'service_schema', :'migration_role') \gexec
GRANT USAGE ON SCHEMA :"service_schema" TO :"runtime_role";
-- No all-table or future-object grants. The migration Job grants the two
-- reviewed relations after they exist. Bootstrap alone is not remediation.

SELECT format('ALTER ROLE %I IN DATABASE %I SET search_path = %I, pg_catalog',
              :'migration_role', :'database_name', :'service_schema') \gexec
SELECT format('ALTER ROLE %I IN DATABASE %I SET search_path = %I, pg_catalog',
              :'runtime_role', :'database_name', :'service_schema') \gexec
\endif

