-- Fresh-profile fixture only. Values are supplied as psql variables by an
-- approved manual bootstrap; no credential value belongs in Git or output.
-- The PostgreSQL bootstrap administrator retains database ownership. The
-- migration role owns only the Account schema, and runtime receives DML only.

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
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA :"service_schema" TO :"runtime_role";
GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA :"service_schema" TO :"runtime_role";
ALTER DEFAULT PRIVILEGES FOR ROLE :"migration_role" IN SCHEMA :"service_schema"
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO :"runtime_role";
ALTER DEFAULT PRIVILEGES FOR ROLE :"migration_role" IN SCHEMA :"service_schema"
  GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO :"runtime_role";

SELECT format('ALTER ROLE %I IN DATABASE %I SET search_path = %I, pg_catalog',
              :'migration_role', :'database_name', :'service_schema') \gexec
SELECT format('ALTER ROLE %I IN DATABASE %I SET search_path = %I, pg_catalog',
              :'runtime_role', :'database_name', :'service_schema') \gexec
