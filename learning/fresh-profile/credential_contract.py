"""Finite offline checks for the PLAT-214 credential and database role contract."""

from dataclasses import dataclass
import json
from pathlib import Path
import re


class Rejected(ValueError):
    pass


ROOT = Path(__file__).resolve().parent
CONTRACT = ROOT / "credential-contract.json"
SQL = ROOT / "account-role-bootstrap.sql"


def _exact(value, expected, label):
    if value != expected:
        raise Rejected(f"{label}-outside-contract")


def load_contract(path=CONTRACT):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    _exact(set(data), {"schemaVersion", "profile", "namespace", "secret", "database", "rotation"}, "root")
    _exact(data["schemaVersion"], "platform-lab/credential-contract/v1", "schema")
    _exact(data["profile"], "fresh-account", "profile")
    _exact(data["namespace"], "learning-fresh-account", "namespace")

    secret = data["secret"]
    _exact(set(secret), {"name", "owner", "purpose", "destination", "expiry", "storageAndRecovery", "keys", "missingDeniedOrUnavailable"}, "secret")
    for key in ("name", "owner", "purpose", "destination"):
        if not isinstance(secret[key], str) or not secret[key].strip():
            raise Rejected(f"secret-{key}-missing")
    _exact(secret["name"], "fresh-account-db", "secret-name")
    expiry = secret["expiry"]
    if not isinstance(expiry.get("maxAgeDays"), int) or not 1 <= expiry["maxAgeDays"] <= 90:
        raise Rejected("expiry-window")
    _exact(expiry.get("expiredBehavior"), "fail-closed", "expiry-behavior")

    storage = secret["storageAndRecovery"]
    _exact(storage.get("provider"), "manual-bootstrap", "provider")
    for field in ("gitStorage", "ciLogStorage"):
        _exact(storage.get(field), "forbidden", field)
    _exact(storage.get("firstInput"), {
        "method": "approved-manual-input", "generatedByRepository": False,
        "readBack": "metadata-and-key-names-only"}, "first-input")
    if not storage.get("recoverySource"):
        raise Rejected("recovery-source-missing")

    keys = secret["keys"]
    _exact(set(keys), {"admin", "migration", "runtime"}, "secret-key-groups")
    names = []
    consumers = {"admin": "postgresql-bootstrap-only", "migration": "account-migration-job-only", "runtime": "account-api-only"}
    for group, consumer in consumers.items():
        _exact(set(keys[group]), {"username", "password", "consumer"}, f"{group}-keys")
        _exact(keys[group]["consumer"], consumer, f"{group}-consumer")
        names.extend((keys[group]["username"], keys[group]["password"]))
    if len(set(names)) != len(names) or any(not re.fullmatch(r"[a-z][a-z0-9-]{2,63}", name) for name in names):
        raise Rejected("secret-key-alias")
    _exact(secret["missingDeniedOrUnavailable"], {
        "podStart": "reject", "migration": "reject", "readiness": "not-ready",
        "fallbackCredential": "forbidden"}, "secret-outage")

    database = data["database"]
    _exact(set(database), {"name", "schema", "admin", "migration", "runtime"}, "database")
    _exact((database["name"], database["schema"]), ("account", "account"), "database-identity")
    _exact(database["admin"], {"role": "postgres", "use": "bootstrap-and-recovery-only", "applicationAccess": False}, "admin-role")
    migration = database["migration"]
    runtime = database["runtime"]
    _exact(migration, {"role": "account_migrator", "databaseOwner": False, "ownedSchemas": ["account"],
        "ddlSchemas": ["account"], "canCreateDatabase": False, "canCreateRole": False, "superuser": False}, "migration-role")
    _exact(runtime, {"role": "account_runtime", "databaseOwner": False, "schemaOwner": False,
        "dmlSchemas": ["account"], "ddlSchemas": [], "crossServiceWrite": False,
        "canCreateDatabase": False, "canCreateRole": False, "superuser": False}, "runtime-role")

    rotation = data["rotation"]
    _exact(rotation.get("processReload"), "pod-restart-required", "reload")
    _exact(rotation.get("steps"), ["new-database-auth-verified", "application-process-restarted",
        "application-ready", "new-process-auth-verified", "old-credential-revoked",
        "old-credential-rejected"], "rotation-steps")
    _exact(rotation.get("liveEvidence"), "required-unrun", "live-evidence")
    if not rotation.get("rollbackBeforeRevocation"):
        raise Rejected("rollback-missing")
    return data


def validate_sql(path=SQL):
    sql = Path(path).read_text(encoding="utf-8")
    required = [
        "NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT",
        "CREATE DATABASE %I OWNER postgres",
        'REVOKE ALL ON DATABASE :"database_name" FROM PUBLIC',
        'REVOKE CREATE ON SCHEMA public FROM PUBLIC, :"migration_role", :"runtime_role"',
        "AUTHORIZATION %I",
        'GRANT USAGE ON SCHEMA :"service_schema" TO :"runtime_role"',
        'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA :"service_schema" TO :"runtime_role"',
        'ALTER DEFAULT PRIVILEGES FOR ROLE :"migration_role" IN SCHEMA :"service_schema"',
    ]
    if any(fragment not in sql for fragment in required):
        raise Rejected("role-sql-incomplete")
    forbidden = [r'CREATE DATABASE %I OWNER\s+:"?runtime_role"?', r'AUTHORIZATION\s+:"?runtime_role"?',
                 r'GRANT\s+(?:ALL|CREATE).*TO\s+:"?runtime_role"?', r'GRANT.*ON ALL TABLES.*TO\s+:"?migration_role"?']
    if any(re.search(pattern, sql, re.IGNORECASE) for pattern in forbidden):
        raise Rejected("role-sql-overprivileged")
    return sql


@dataclass(frozen=True)
class RotationEvidence:
    new_database_auth: bool
    process_restarted: bool
    application_ready: bool
    new_process_auth: bool
    old_credential_revoked: bool
    old_credential_rejected: bool


def accept_rotation(evidence):
    """Fail closed unless every ordered live check is explicitly true.

    This models the evidence gate only. It does not claim that a real process or
    credential was changed; PLAT-217 must record those live observations.
    """
    if not isinstance(evidence, RotationEvidence):
        raise Rejected("rotation-evidence-type")
    checks = tuple(evidence.__dict__.values())
    if any(type(value) is not bool for value in checks) or not all(checks):
        raise Rejected("rotation-evidence-incomplete")
    return True


if __name__ == "__main__":
    load_contract()
    validate_sql()
