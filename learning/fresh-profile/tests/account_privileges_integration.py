#!/usr/bin/env python3
"""Disposable PG18 + unchanged retained-source binary regression; never uses a live DB.

python3 account_privileges_integration.py --server /path/to/exact-source/server
Requires Docker, PyYAML, and a local build of backend commit 635814f... .
All passwords below are synthetic test literals; no existing credentials are read.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor

import yaml

ROOT = Path(__file__).resolve().parents[1]
BASE = "3707f73581da5d04db0e4771dda7c1e82bd0f47c"
SOURCE = "635814f5354f0fe238694d35137b283576a9127f"
IMAGE = "postgres@sha256:979bccfde35445db618c65107d27d08c6c21339d8dc3ad4c830c6633947be0e4"
PASSWORDS = {"postgres": "synthetic-admin-only", "account_migrator": "synthetic-migrator-only",
             "account_runtime": "synthetic-runtime-only"}


class Regression:
    def __init__(self, args):
        self.args = args
        self.network = f"account-acl-review-{os.getpid()}"
        self.containers = []
        self.results = []
        self.db = ""
        self.staging = tempfile.TemporaryDirectory(prefix="account-acl-inputs-")
        self.server = Path(self.staging.name) / "server"
        self.sql_source = Path(self.staging.name) / "bootstrap.sql"
        shutil.copyfile(args.server, self.server)
        shutil.copyfile(ROOT / "workload/account-role-bootstrap.sql", self.sql_source)
        self.server.chmod(0o555)
        self.sql_source.chmod(0o444)

    def run(self, argv, sql=None, ok=True):
        result = subprocess.run(argv, input=sql, text=True, capture_output=True, timeout=200)
        if ok and result.returncode:
            raise AssertionError(f"command failed ({argv[0]}): {result.stdout}\n{result.stderr}")
        return result

    def passed(self, label):
        self.results.append(label)
        print(f"PASS {label}", flush=True)

    def psql(self, sql, role="postgres", ok=True, variables=()):
        return self.run(["docker", "exec", "-i", "-e", f"PGPASSWORD={PASSWORDS[role]}", self.db,
                         "psql", "-X", "-h", "127.0.0.1", "-U", role, "-d", "account",
                         "-v", "ON_ERROR_STOP=1", "-At", *variables], sql, ok)

    def scalar(self, sql, role="postgres"):
        return self.psql(sql, role).stdout.strip()

    def denied(self, sql, role="account_runtime"):
        result = self.psql(sql, role, ok=False)
        assert result.returncode != 0 and ("permission denied" in result.stderr or
                "must be owner" in result.stderr), result.stderr

    def bootstrap(self, old=False):
        sql = (self.run(["git", "-C", str(ROOT.parents[1]), "show",
                        f"{BASE}:learning/fresh-profile/workload/account-role-bootstrap.sql"]).stdout
               if old else (ROOT / "workload/account-role-bootstrap.sql").read_text())
        self.psql(sql, variables=["-v", "migration_role=account_migrator", "-v",
            f"migration_password={PASSWORDS['account_migrator']}", "-v", "runtime_role=account_runtime",
            "-v", f"runtime_password={PASSWORDS['account_runtime']}", "-v", "database_name=account",
            "-v", "service_schema=account"])

    def remediate(self, ok=True):
        return self.psql((ROOT / "remediation/0001_account_runtime_privileges.sql").read_text(), ok=ok)

    def grants(self, ok=True):
        return self.psql((ROOT / "workload/account-role-bootstrap.sql").read_text(),
                         "account_migrator", ok=ok, variables=["-v", "grant_runtime=1"])

    def profile_env(self, role):
        # The test carrier is postgres, not the retained distroless runtime image.
        # Empty its image-only PG variables so the exact finite loader sees only
        # the same PG inputs accepted by the real Account process.
        return {"PG_MAJOR": "", "PG_VERSION": "", "PGDATA": "",
                "ACCOUNT_RUNTIME_PROFILE": "fresh-account", "PGHOST": "account-postgresql.fresh-account.svc",
                "PGPORT": "5432", "PGDATABASE": "account", "PGUSER": role, "PGPASSWORD": PASSWORDS[role],
                "PGSSLMODE": "disable", "POD_NAMESPACE": "fresh-account", "POD_UID": "synthetic-review-pod",
                "DEPLOYMENT_ENVIRONMENT": "local", "ACCOUNT_HTTP_ADDRESS": ":8080",
                "OTEL_RESOURCE_ATTRIBUTES": "service.name=account-api,deployment.environment=local,platform.profile=fresh-account,k8s.namespace.name=fresh-account,k8s.pod.uid=synthetic-review-pod",
                "OTEL_EXPORTER_OTLP_ENDPOINT": "http://otel-collector.fresh-observability.svc.cluster.local:4318"}

    def source_container(self, name, migration=False, ok=True):
        role = "account_migrator" if migration else "account_runtime"
        command = ["docker", "run", "--name", name, "--network", self.network,
                   "--user", "10001:10001", "--cap-drop=ALL", "--security-opt=no-new-privileges",
                   "--mount", f"type=bind,src={self.server},dst=/review/server,readonly",
                   "--entrypoint", "/review/server"]
        if not migration:
            command += ["-d"]
        for key, value in self.profile_env(role).items():
            command += ["-e", f"{key}={value}"]
        command += [self.args.image] + (["migrate"] if migration else [])
        self.containers.append(name)
        return self.run(command, ok=ok)

    def migrate(self, ok=True):
        return self.source_container(f"{self.network}-migrate-{len(self.containers)}", True, ok)

    def fresh_database(self, scenario):
        if self.db:
            self.run(["docker", "rm", "-f", self.db])
            self.containers.remove(self.db)
        self.db = f"{self.network}-{scenario}"
        self.containers.append(self.db)
        self.run(["docker", "run", "-d", "--name", self.db, "--network", self.network,
                  "--network-alias", "account-postgresql.fresh-account.svc", "--tmpfs", "/var/lib/postgresql",
                  "-e", f"POSTGRES_PASSWORD={PASSWORDS['postgres']}", "-e", "POSTGRES_DB=account", self.args.image])
        for _ in range(100):
            if self.psql("SELECT 1", ok=False).returncode == 0:
                break
            time.sleep(.2)
        else:
            raise AssertionError("isolated PostgreSQL did not start")
        assert self.scalar("SHOW server_version_num").startswith("18")

    def history(self):
        return self.scalar("SELECT json_agg(h ORDER BY version)::text FROM account.schema_migrations h")

    def assert_acl(self, scenario):
        for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE"):
            assert self.scalar(f"SELECT has_table_privilege('account_runtime', 'account.accounts', '{privilege}')") == "t"
            expected = "t" if privilege == "SELECT" else "f"
            assert self.scalar(f"SELECT has_table_privilege('account_runtime', 'account.schema_migrations', '{privilege}')") == expected
        assert self.scalar("SELECT has_sequence_privilege('account_runtime', pg_get_serial_sequence('account.accounts','id'), 'USAGE,SELECT,UPDATE')") == "f"
        assert self.scalar("SELECT version FROM account.schema_migrations", "account_runtime") == "1"
        for sql in ("INSERT INTO account.schema_migrations(version) VALUES(2)",
                    "UPDATE account.schema_migrations SET version=2 WHERE version=1",
                    "DELETE FROM account.schema_migrations WHERE version=1",
                    "TRUNCATE account.schema_migrations", "TRUNCATE account.accounts",
                    "CREATE TABLE account.forbidden(id int)", "CREATE TABLE public.forbidden(id int)",
                    "CREATE TEMP TABLE forbidden(id int)", "CREATE SCHEMA forbidden", "CREATE DATABASE forbidden",
                    "CREATE ROLE forbidden", "SET ROLE account_migrator", "DROP TABLE account.accounts",
                    "SELECT nextval('account.accounts_id_seq')", "SELECT currval('account.accounts_id_seq')",
                    "SELECT setval('account.accounts_id_seq', 100)"):
            self.denied(sql)
        self.psql("CREATE SCHEMA other_service; CREATE TABLE other_service.private(id int)")
        self.denied("SELECT * FROM other_service.private")
        self.denied("INSERT INTO other_service.private VALUES(1)")
        self.denied("CREATE TABLE other_service.forbidden(id int)", "account_migrator")
        self.psql("CREATE TABLE account.future_private(id int); CREATE SEQUENCE account.future_sequence", "account_migrator")
        for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE"):
            assert self.scalar(f"SELECT has_table_privilege('account_runtime','account.future_private','{privilege}')") == "f"
        self.denied("SELECT * FROM account.future_private")
        self.denied("INSERT INTO account.future_private VALUES(1)")
        self.denied("UPDATE account.future_private SET id=2")
        self.denied("DELETE FROM account.future_private")
        self.denied("SELECT nextval('account.future_sequence')")
        self.passed(f"{scenario}: effective ACL matrix, history IUD/DDL/other-schema/future objects denied; sequence direct privileges none")

    def http(self, name):
        self.source_container(name)
        def request(method, path, data=None):
            # Real HTTP over the API container's loopback; the internal Docker
            # network publishes no host port and provides no external egress.
            body = json.dumps(data) if data is not None else ""
            result = self.run(["docker", "exec", name, "timeout", "5", "bash", "-ceu",
                'exec 3<>/dev/tcp/127.0.0.1/8080; '
                'printf "%s %s HTTP/1.0\\r\\nHost: localhost\\r\\nContent-Type: application/json\\r\\nContent-Length: %s\\r\\nConnection: close\\r\\n\\r\\n%s" "$1" "$2" "${#3}" "$3" >&3; cat <&3',
                "http-probe", method, path, body], ok=False)
            if result.returncode:
                return 0, result.stderr
            header, _, response_body = result.stdout.partition("\n\n")
            return int(header.splitlines()[0].split()[1]), response_body
        for _ in range(50):
            try:
                if request("GET", "/readyz")[0] == 200:
                    break
            except OSError:
                pass
            time.sleep(.2)
        else:
            raise AssertionError(f"exact-source /readyz did not return 200: {request('GET', '/readyz')}; "
                                 + self.run(["docker", "logs", name], ok=False).stdout)
        code, body = request("POST", "/api/accounts", {"name": "Synthetic ACL", "email": "acl@example.test"})
        assert code == 201, (code, body)
        account_id = json.loads(body)["id"]
        path = f"/api/accounts/{account_id}"
        assert request("GET", path)[0] == 200
        assert request("GET", "/api/accounts")[0] == 200
        assert request("PUT", path, {"name": "Synthetic Updated", "email": "acl-update@example.test"})[0] == 200
        assert request("DELETE", path)[0] == 204
        assert request("GET", path)[0] == 404
        self.psql("SET ROLE account_migrator; REVOKE SELECT ON account.schema_migrations FROM account_runtime")
        assert request("GET", "/readyz")[0] == 503
        self.psql("SET ROLE account_migrator; GRANT SELECT ON account.schema_migrations TO account_runtime")
        assert request("GET", "/readyz")[0] == 200
        self.passed(f"{name.rsplit('-',1)[1]}: exact-source HTTP readyz200, CRUD201/200/200/204/404; history SELECT loss503/restore200")
        self.run(["docker", "rm", "-f", name])
        self.containers.remove(name)

    def upgrade(self, scenario):
        original = self.history()
        self.psql("BEGIN; CREATE TABLE account.synthetic_upgrade(id int); INSERT INTO account.schema_migrations(version) VALUES(2); ROLLBACK", "account_migrator")
        assert self.history() == original
        assert self.scalar("SELECT to_regclass('account.synthetic_upgrade') IS NULL") == "t"
        upgrade = "BEGIN; CREATE TABLE IF NOT EXISTS account.synthetic_upgrade(id int); INSERT INTO account.schema_migrations(version) VALUES(2) ON CONFLICT DO NOTHING; COMMIT"
        self.psql(upgrade, "account_migrator")
        upgraded = self.history()
        self.psql(upgrade, "account_migrator")
        assert self.history() == upgraded
        self.denied("SELECT * FROM account.synthetic_upgrade")
        assert self.migrate(ok=False).returncode != 0
        assert self.history() == upgraded
        self.psql("BEGIN; DROP TABLE account.synthetic_upgrade; DELETE FROM account.schema_migrations WHERE version=2; COMMIT", "account_migrator")
        assert self.history() == original
        self.migrate()
        assert self.history() == original
        self.passed(f"{scenario}: migrator synthetic upgrade/history INSERT, rollback/rerun; retained C0 migrate rejects v2 without changes")

    def start_grants(self):
        # Execute the actual manifest command and environment with synthetic auth.
        job = next(d for d in yaml.safe_load_all((ROOT / "workload/account.yaml").read_text())
                   if d["kind"] == "Job" and d["metadata"]["name"] == "account-migration")
        sidecar = job["spec"]["template"]["spec"]["containers"][1]
        name = f"{self.network}-grants-{len(self.containers)}"
        self.containers.append(name)
        command = ["docker", "run", "-d", "--name", name, "--network", self.network,
                   "--user", "10001:10001", "--mount", f"type=bind,src={self.sql_source},dst=/contract/bootstrap.sql,readonly"]
        for item in sidecar["env"]:
            value = item.get("value", "account_migrator" if item["name"] == "PGUSER" else PASSWORDS["account_migrator"])
            command += ["-e", f"{item['name']}={value}"]
        self.run(command + ["--entrypoint", "sh", self.args.image, "-ceu", sidecar["args"][0]])
        assert self.run(["docker", "inspect", "-f", "{{.State.Running}}", name]).stdout.strip() == "true"
        return name

    def delayed_commit(self):
        self.fresh_database("delayed")
        self.bootstrap()
        # Hold the real migrator advisory lock in a separate bounded session.
        # No source/binary edits or time virtualization in this PG18 scenario.
        with ThreadPoolExecutor(max_workers=1) as pool:
            blocker = pool.submit(self.psql, "SET statement_timeout='175s'; SELECT pg_advisory_lock(7742340081); "
                                  "SELECT pg_sleep(165); SELECT pg_advisory_unlock(7742340081)")
            for _ in range(100):
                if self.scalar("SELECT EXISTS(SELECT FROM pg_locks WHERE locktype='advisory' AND granted)") == "t":
                    break
                time.sleep(.05)
            else:
                raise AssertionError("delayed-commit blocker did not acquire lock")
            started = time.monotonic()
            grants = self.start_grants()
            self.migrate()
            committed_after = time.monotonic() - started
            blocker.result(timeout=10)
        assert 160 <= committed_after < 170, committed_after
        assert self.run(["docker", "wait", grants]).stdout.strip() == "0", self.run(["docker", "logs", grants]).stdout
        finished_after = time.monotonic() - started
        assert finished_after < 220, finished_after
        assert self.scalar("SELECT version FROM account.schema_migrations", "account_runtime") == "1"
        assert self.scalar("SELECT has_table_privilege('account_runtime','account.schema_migrations','INSERT,UPDATE,DELETE')") == "f"
        assert self.scalar("SELECT has_table_privilege('account_runtime','account.accounts','SELECT,INSERT,UPDATE,DELETE')") == "t"
        self.passed(f"delayed: real advisory-lock wait; retained-source migrate completed at {committed_after:.3f}s; "
                    f"actual manifest grants completed at {finished_after:.3f}s")
        self.http(f"{self.network}-api-delayed")

    def execute(self):
        self.run(["docker", "network", "create", "--internal", self.network])
        try:
            self.fresh_database("clean")
            self.bootstrap(); self.bootstrap()
            assert self.grants(ok=False).returncode != 0
            name = self.start_grants()
            self.migrate()
            assert self.run(["docker", "wait", name]).stdout.strip() == "0", self.run(["docker", "logs", name]).stdout
            before = self.history()
            self.grants(); self.grants(); self.bootstrap(); self.migrate()
            assert self.history() == before
            self.passed("clean: bootstrap -> concurrent retained-source migration/grants -> idempotent reruns; history unchanged")
            self.assert_acl("clean"); self.http(f"{self.network}-api-clean"); self.upgrade("clean")

            self.fresh_database("existing")
            self.bootstrap(old=True); self.migrate()
            assert self.scalar("SELECT has_table_privilege('account_runtime','account.schema_migrations','INSERT')") == "t"
            assert self.grants(ok=False).returncode != 0
            before = self.history()
            # Existing rows must survive remediation.
            self.psql("INSERT INTO account.accounts(name,email) VALUES('Sentinel','sentinel@example.test')", "account_runtime")
            self.psql("GRANT INSERT(version), UPDATE(version) ON account.schema_migrations TO account_runtime")
            rows = self.scalar("SELECT json_agg(a ORDER BY id)::text FROM account.accounts a")
            self.remediate(); self.remediate(); self.bootstrap(); self.grants(); self.migrate()
            assert self.history() == before
            assert self.scalar("SELECT json_agg(a ORDER BY id)::text FROM account.accounts a") == rows
            assert self.scalar("SELECT has_any_column_privilege('account_runtime','account.schema_migrations','INSERT,UPDATE')") == "f"
            self.passed("existing: original broad bootstrap reproduced, remediation twice preserves history/data; bootstrap cannot regrant broad ACL")
            self.assert_acl("existing"); self.http(f"{self.network}-api-existing"); self.upgrade("existing")

            # Deliberate drift must fail without partially committing revocations.
            for label, introduce, remove in (
                ("owner", "ALTER TABLE account.schema_migrations OWNER TO postgres", "ALTER TABLE account.schema_migrations OWNER TO account_migrator"),
                ("global-default", "ALTER DEFAULT PRIVILEGES FOR ROLE account_migrator GRANT SELECT ON TABLES TO account_runtime", "ALTER DEFAULT PRIVILEGES FOR ROLE account_migrator REVOKE SELECT ON TABLES FROM account_runtime"),
                ("PUBLIC-column-insert", "GRANT INSERT(version) ON account.schema_migrations TO PUBLIC", "REVOKE INSERT(version) ON account.schema_migrations FROM PUBLIC"),
                ("role-membership", "GRANT account_migrator TO account_runtime", "REVOKE account_migrator FROM account_runtime"),
            ):
                self.psql("GRANT INSERT, UPDATE, DELETE ON account.schema_migrations TO account_runtime; " + introduce)
                result = self.remediate(ok=False)
                assert result.returncode != 0 and "ACL v1:" in result.stderr, result.stderr
                assert self.scalar("SELECT has_table_privilege('account_runtime','account.schema_migrations','INSERT')") == "t"
                assert self.history() == before
                self.psql(remove); self.remediate()
                self.passed(f"remediation rejects {label} drift and rolls back all ACL edits")
            if self.args.delayed_commit:
                self.delayed_commit()
            return {"status": "PASS", "backendSource": SOURCE, "postgresImage": self.args.image,
                    "postgresVersion": self.scalar("SHOW server_version"), "checks": self.results,
                    "scope": "Executed isolated PostgreSQL and exact-source binary; not retained runtime image or live cluster"}
        finally:
            for name in reversed(self.containers):
                self.run(["docker", "rm", "-f", name], ok=False)
            self.run(["docker", "network", "rm", self.network], ok=False)
            self.staging.cleanup()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server", type=Path, required=True)
    parser.add_argument("--image", default=IMAGE)
    parser.add_argument("--results", type=Path)
    parser.add_argument("--delayed-commit", action="store_true",
                        help="also hold the real migration lock for 165s to test late commit")
    args = parser.parse_args()
    result = Regression(args).execute()
    if args.results:
        args.results.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
