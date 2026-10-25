"""Finite offline checks for the PLAT-214 credential and database role contract."""

from dataclasses import dataclass
import json
from pathlib import Path
import re


class Rejected(ValueError):
    pass


ROOT = Path(__file__).resolve().parent
CONTRACT = ROOT / "credential-contract.json"
SQL = ROOT / "workload" / "account-role-bootstrap.sql"


def _exact(value, expected, label):
    if value != expected:
        raise Rejected(f"{label}-outside-contract")


def load_contract(path=CONTRACT):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    _exact(set(data), {"schemaVersion", "profile", "namespace", "credentialSet", "secrets", "transportTrust", "database", "rotation"}, "root")
    _exact(data["schemaVersion"], "platform-lab/credential-contract/v2", "schema")
    _exact(data["profile"], "fresh-account", "profile")
    _exact(data["namespace"], "fresh-account", "namespace")

    credential_set = data["credentialSet"]
    _exact(set(credential_set), {"owner", "purpose", "expiry", "storageAndRecovery", "missingDeniedOrUnavailable"}, "credential-set")
    for key in ("owner", "purpose"):
        if not isinstance(credential_set[key], str) or not credential_set[key].strip():
            raise Rejected(f"credential-set-{key}-missing")
    expiry = credential_set["expiry"]
    if not isinstance(expiry.get("maxAgeDays"), int) or not 1 <= expiry["maxAgeDays"] <= 90:
        raise Rejected("expiry-window")
    _exact(expiry.get("expiredBehavior"), "fail-closed", "expiry-behavior")

    storage = credential_set["storageAndRecovery"]
    _exact(storage.get("provider"), "manual-bootstrap", "provider")
    for field in ("gitStorage", "ciLogStorage"):
        _exact(storage.get(field), "forbidden", field)
    _exact(storage.get("firstInput"), {
        "method": "approved-manual-input", "generatedByRepository": False,
        "readBack": "metadata-and-key-names-only"}, "first-input")
    if not storage.get("recoverySource"):
        raise Rejected("recovery-source-missing")
    _exact(storage.get("existingCredentialTransfer"), "separate-approval-required", "existing-transfer")
    _exact(storage.get("freshIsolatedCredentialsPreferred"), True, "fresh-isolated")

    secrets = data["secrets"]
    expected = {
        "bootstrap": ("account-db-bootstrap", ["admin-password"],
                      ["StatefulSet/account-postgresql", "Job/account-db-role-bootstrap"]),
        "migration": ("account-db-migration", ["username", "password", "uri"],
                      ["Job/account-db-role-bootstrap", "Job/account-migration"]),
        "runtime": ("account-db-runtime", ["username", "password", "uri"],
                    ["Job/account-db-role-bootstrap", "Rollout/account-api"]),
    }
    _exact(set(secrets), set(expected), "secret-groups")
    names = []
    for group, (name, keys, consumers) in expected.items():
        _exact(set(secrets[group]), {"name", "purpose", "keys", "consumers"}, f"{group}-secret")
        _exact(secrets[group]["name"], name, f"{group}-name")
        _exact(secrets[group]["keys"], keys, f"{group}-keys")
        _exact(secrets[group]["consumers"], consumers, f"{group}-consumers")
        names.append(name)
    if len(set(names)) != len(names) or any(not re.fullmatch(r"[a-z][a-z0-9-]{2,63}", name) for name in names):
        raise Rejected("secret-name-alias")
    _exact(credential_set["missingDeniedOrUnavailable"], {
        "podStart": "reject", "migration": "reject", "readiness": "not-ready",
        "fallbackCredential": "forbidden"}, "secret-outage")

    _exact(data["transportTrust"], {
        "sandboxCA": "generate-in-fresh-sandbox-after-G3-approval",
        "gitMaterial": "forbidden", "existingCAReadOrTransfer": "separate-approval-required",
        "metadataEvidenceOnly": True}, "transport-trust")

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
