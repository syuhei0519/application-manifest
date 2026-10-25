import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("fresh", ROOT / "validate_fresh_profile.py")
fresh = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fresh)


class FreshWorkloadTests(unittest.TestCase):
    def temp_json(self, data):
        stream = tempfile.NamedTemporaryFile("w", suffix=".json", encoding="utf-8", delete=False)
        json.dump(data, stream)
        stream.close()
        path = Path(stream.name)
        self.addCleanup(path.unlink, missing_ok=True)
        return path

    def temp_yaml(self, documents):
        stream = tempfile.NamedTemporaryFile("w", suffix=".yaml", encoding="utf-8", delete=False)
        yaml.safe_dump_all(documents, stream, sort_keys=False)
        stream.close()
        path = Path(stream.name)
        self.addCleanup(path.unlink, missing_ok=True)
        return path

    def test_checked_contract_is_coherent_and_blocked_on_g2(self):
        contract, documents, release = fresh.validate()
        self.assertFalse(contract["images"]["accountRuntime"]["ciArtifact"]["freshEligible"])
        self.assertEqual(release["spec"]["source"]["targetRevision"], fresh.TOKEN)
        self.assertEqual(len(documents), 13)

    def test_wrong_account_image_and_rollouts_image_are_rejected(self):
        data = json.loads(fresh.CONTRACT.read_text(encoding="utf-8"))
        for image in (
            "ghcr.io/syuhei0519/backend-app@sha256:" + "0" * 64,
            "learning.local/account-rollouts@sha256:966adba896524519bd1653b31ade97b624dd5cbf83623f7cdeb5c5708aaa69e0",
        ):
            changed = copy.deepcopy(data)
            changed["images"]["accountRuntime"]["ref"] = image
            with self.subTest(image=image), self.assertRaises(fresh.Rejected):
                fresh.validate(contract_path=self.temp_json(changed))

    def test_secret_object_or_extra_secret_key_is_rejected(self):
        documents = list(yaml.safe_load_all(fresh.WORKLOAD.read_text(encoding="utf-8")))
        secret = {"apiVersion": "v1", "kind": "Secret", "metadata": {"name": "bad", "namespace": "fresh-account"}}
        with self.assertRaises(fresh.Rejected):
            fresh.validate(workload_path=self.temp_yaml(documents + [secret]))
        changed = copy.deepcopy(documents)
        rollout = next(item for item in changed if item["kind"] == "Rollout")
        rollout["spec"]["template"]["spec"]["containers"][0]["env"].append(
            {"name": "BAD", "valueFrom": {"secretKeyRef": {"name": "account-db-runtime", "key": "root"}}})
        with self.assertRaises(fresh.Rejected):
            fresh.validate(workload_path=self.temp_yaml(changed))

    def test_variable_or_non_sha_revision_is_rejected(self):
        release = yaml.safe_load(fresh.RELEASE.read_text(encoding="utf-8"))
        release["spec"]["source"]["targetRevision"] = "main"
        with self.assertRaises(fresh.Rejected):
            fresh.validate(release_path=self.temp_yaml([release]))

    def test_workload_tokens_and_pvc_retention_are_fail_closed(self):
        documents = list(yaml.safe_load_all(fresh.WORKLOAD.read_text(encoding="utf-8")))
        rollout = next(item for item in documents if item["kind"] == "Rollout")
        rollout["spec"]["template"]["spec"]["automountServiceAccountToken"] = True
        with self.assertRaises(fresh.Rejected):
            fresh.validate(workload_path=self.temp_yaml(documents))


if __name__ == "__main__":
    unittest.main()
