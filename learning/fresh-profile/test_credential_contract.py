import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("credential_contract", ROOT / "credential_contract.py")
contract = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(contract)
CONTRACT = contract.CONTRACT
SQL = contract.SQL
Rejected = contract.Rejected
RotationEvidence = contract.RotationEvidence
accept_rotation = contract.accept_rotation
load_contract = contract.load_contract
validate_sql = contract.validate_sql


class CredentialContractTests(unittest.TestCase):
    def mutate(self, change):
        data = json.loads(CONTRACT.read_text(encoding="utf-8"))
        change(data)
        with tempfile.NamedTemporaryFile("w", suffix=".json", encoding="utf-8", delete=False) as stream:
            json.dump(data, stream)
            path = Path(stream.name)
        self.addCleanup(path.unlink, missing_ok=True)
        return path

    def test_repository_contract_and_sql(self):
        contract = load_contract()
        self.assertEqual(contract["database"]["migration"]["ownedSchemas"], ["account"])
        self.assertEqual(contract["database"]["runtime"]["dmlSchemas"], ["account"])
        self.assertEqual(contract["database"]["runtime"]["ddlSchemas"], [])
        self.assertIn("OWNER postgres", validate_sql())

    def test_unknown_profile_and_unbounded_expiry_reject(self):
        for change in (
            lambda data: data.update(profile="arbitrary"),
            lambda data: data["credentialSet"]["expiry"].update(maxAgeDays=0),
            lambda data: data["credentialSet"]["expiry"].update(maxAgeDays=365),
            lambda data: data["credentialSet"]["expiry"].update(expiredBehavior="warn"),
        ):
            with self.subTest(change=change), self.assertRaises(Rejected):
                load_contract(self.mutate(change))

    def test_secret_storage_and_outage_must_fail_closed(self):
        changes = (
            lambda data: data["credentialSet"]["storageAndRecovery"].update(gitStorage="allowed"),
            lambda data: data["credentialSet"]["storageAndRecovery"].update(recoverySource=""),
            lambda data: data["credentialSet"]["storageAndRecovery"]["firstInput"].update(generatedByRepository=True),
            lambda data: data["credentialSet"]["storageAndRecovery"].update(existingCredentialTransfer="allowed"),
            lambda data: data["credentialSet"]["missingDeniedOrUnavailable"].update(fallbackCredential="default"),
            lambda data: data["credentialSet"]["missingDeniedOrUnavailable"].update(readiness="ready"),
        )
        for change in changes:
            with self.subTest(change=change), self.assertRaises(Rejected):
                load_contract(self.mutate(change))

    def test_secret_objects_are_distinct_and_finite(self):
        def reused(data):
            data["secrets"]["runtime"]["name"] = data["secrets"]["migration"]["name"]
        def extra(data):
            data["secrets"]["runtime"]["keys"].append("root-token")
        for change in (reused, extra):
            with self.subTest(change=change), self.assertRaises(Rejected):
                load_contract(self.mutate(change))

    def test_runtime_cannot_gain_ddl_or_cross_service_write(self):
        changes = (
            lambda data: data["database"]["runtime"].update(ddlSchemas=["account"]),
            lambda data: data["database"]["runtime"].update(crossServiceWrite=True),
            lambda data: data["database"]["runtime"].update(schemaOwner=True),
            lambda data: data["database"]["migration"].update(ownedSchemas=["public"]),
            lambda data: data["database"]["migration"].update(databaseOwner=True),
        )
        for change in changes:
            with self.subTest(change=change), self.assertRaises(Rejected):
                load_contract(self.mutate(change))

    def test_sql_rejects_runtime_owner_and_create(self):
        source = SQL.read_text(encoding="utf-8")
        mutations = (
            source.replace("OWNER postgres", "OWNER :runtime_role"),
            source + "\nGRANT CREATE ON SCHEMA account TO :runtime_role;\n",
            source.replace("AUTHORIZATION %I", "AUTHORIZATION :runtime_role"),
        )
        for sql in mutations:
            with tempfile.NamedTemporaryFile("w", suffix=".sql", encoding="utf-8", delete=False) as stream:
                stream.write(sql)
                path = Path(stream.name)
            self.addCleanup(path.unlink, missing_ok=True)
            with self.subTest(sql=sql[-80:]), self.assertRaises(Rejected):
                validate_sql(path)

    def test_rotation_requires_every_real_process_observation(self):
        complete = RotationEvidence(True, True, True, True, True, True)
        self.assertTrue(accept_rotation(complete))
        for field in complete.__dict__:
            values = copy.copy(complete.__dict__)
            values[field] = False
            with self.subTest(field=field), self.assertRaises(Rejected):
                accept_rotation(RotationEvidence(**values))
        with self.assertRaises(Rejected):
            accept_rotation({"all": True})


if __name__ == "__main__":
    unittest.main()
