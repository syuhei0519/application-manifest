import copy
import importlib.util
import json
from pathlib import Path
import shutil
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
        self.assertFalse(contract["images"]["accountRuntime"]["freshProfileCompatible"])
        self.assertEqual(contract["images"]["freshRuntimeRequirement"]["immutableImageRef"],
                         "ghcr.io/syuhei0519/backend-app@sha256:"
                         "fba8eae42982a0e818581b2ac6b41ba865920dafdbfd7d48c416f98c5b1eaebd")
        self.assertFalse(contract["images"]["freshRuntimeRequirement"]["freshEligible"])
        self.assertEqual(release["chart"]["name"], "fresh-account-release-definition")
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

    def test_release_chart_has_no_self_sha_token(self):
        release = fresh.validate_release_chart()
        self.assertNotIn("__APPLICATION_MANIFEST_SHA__", release["template"])
        self.assertIn("workloadRevision must be a 40-character lowercase Git SHA", release["template"])

    def test_release_token_or_variable_contract_is_rejected(self):
        temporary = Path(tempfile.mkdtemp(prefix="release-test-", dir=ROOT))
        self.addCleanup(shutil.rmtree, temporary, True)
        destination = temporary / "release-definition"
        shutil.copytree(fresh.RELEASE, destination)
        template = destination / "templates" / "application.yaml"
        template.write_text(template.read_text(encoding="utf-8") + "\n# __APPLICATION_MANIFEST_SHA__\n",
                            encoding="utf-8")
        with self.assertRaises(fresh.Rejected):
            fresh.validate_release_chart(destination)

    def test_workload_tokens_and_pvc_retention_are_fail_closed(self):
        documents = list(yaml.safe_load_all(fresh.WORKLOAD.read_text(encoding="utf-8")))
        rollout = next(item for item in documents if item["kind"] == "Rollout")
        rollout["spec"]["template"]["spec"]["automountServiceAccountToken"] = True
        with self.assertRaises(fresh.Rejected):
            fresh.validate(workload_path=self.temp_yaml(documents))

    def test_job_order_and_permission_case_drift_are_rejected(self):
        documents = list(yaml.safe_load_all(fresh.WORKLOAD.read_text(encoding="utf-8")))
        migration = next(item for item in documents if item.get("kind") == "Job" and
                         item["metadata"]["name"] == "account-migration")
        migration["metadata"]["annotations"]["argocd.argoproj.io/sync-wave"] = "10"
        with self.assertRaises(fresh.Rejected):
            fresh.validate(workload_path=self.temp_yaml(documents))

        cases = json.loads(fresh.DB_CASES.read_text(encoding="utf-8"))
        cases["requiredLiveCases"][2]["expected"] = "allow"
        with self.assertRaises(fresh.Rejected):
            fresh.validate(db_cases_path=self.temp_json(cases))

    def test_api_readiness_is_strictly_after_migration(self):
        documents = list(yaml.safe_load_all(fresh.WORKLOAD.read_text(encoding="utf-8")))
        rollout = next(item for item in documents if item.get("kind") == "Rollout")
        self.assertEqual(rollout["metadata"]["annotations"]["argocd.argoproj.io/sync-wave"], "30")
        del rollout["metadata"]["annotations"]["argocd.argoproj.io/sync-wave"]
        with self.assertRaisesRegex(fresh.Rejected, "application-wave|bootstrap-before-readiness"):
            fresh.validate(workload_path=self.temp_yaml(documents))

    def test_api_readiness_uses_exact_runtime_route(self):
        for path in ("/healthz", "/livez", "/", "/readyz/other"):
            documents = list(yaml.safe_load_all(fresh.WORKLOAD.read_text(encoding="utf-8")))
            rollout = next(item for item in documents if item.get("kind") == "Rollout")
            rollout["spec"]["template"]["spec"]["containers"][0]["readinessProbe"]["httpGet"]["path"] = path
            with self.subTest(path=path), self.assertRaisesRegex(fresh.Rejected, "api-readiness-probe"):
                fresh.validate(workload_path=self.temp_yaml(documents))

        documents = list(yaml.safe_load_all(fresh.WORKLOAD.read_text(encoding="utf-8")))
        rollout = next(item for item in documents if item.get("kind") == "Rollout")
        rollout["metadata"]["annotations"]["argocd.argoproj.io/sync-wave"] = "20"
        with self.assertRaisesRegex(fresh.Rejected, "application-wave|bootstrap-before-readiness"):
            fresh.validate(workload_path=self.temp_yaml(documents))

    def test_runtime_and_migration_use_one_finite_profile_with_distinct_secrets(self):
        _, documents, _ = fresh.validate()
        workloads = {
            item["metadata"]["name"]: item for item in documents
            if item.get("kind") in {"Rollout", "Job"}
        }
        for name, secret in (("account-api", "account-db-runtime"),
                             ("account-migration", "account-db-migration")):
            container = workloads[name]["spec"]["template"]["spec"]["containers"][0]
            env = {item["name"]: item for item in container["env"]}
            self.assertEqual(env["ACCOUNT_RUNTIME_PROFILE"]["value"], "fresh-account")
            self.assertEqual(env["PGHOST"]["value"], "account-postgresql.fresh-account.svc")
            self.assertEqual(env["PGUSER"]["valueFrom"]["secretKeyRef"],
                             {"name": secret, "key": "username"})
            self.assertNotIn("DATABASE_URL", env)
            self.assertNotIn("PGOPTIONS", env)

    def test_profile_alias_fallback_and_role_secret_swap_are_rejected(self):
        base = list(yaml.safe_load_all(fresh.WORKLOAD.read_text(encoding="utf-8")))
        mutations = []

        alias = copy.deepcopy(base)
        rollout = next(item for item in alias if item.get("kind") == "Rollout")
        env = rollout["spec"]["template"]["spec"]["containers"][0]["env"]
        next(item for item in env if item["name"] == "ACCOUNT_RUNTIME_PROFILE")["value"] = "fresh_account"
        mutations.append(alias)

        fallback = copy.deepcopy(base)
        migration = next(item for item in fallback if item.get("kind") == "Job" and
                         item["metadata"]["name"] == "account-migration")
        migration["spec"]["template"]["spec"]["containers"][0]["env"].append(
            {"name": "DATABASE_URL", "valueFrom": {"secretKeyRef":
                {"name": "account-db-migration", "key": "uri"}}})
        mutations.append(fallback)

        swapped = copy.deepcopy(base)
        rollout = next(item for item in swapped if item.get("kind") == "Rollout")
        user = next(item for item in rollout["spec"]["template"]["spec"]["containers"][0]["env"]
                    if item["name"] == "PGUSER")
        user["valueFrom"]["secretKeyRef"]["name"] = "account-db-migration"
        mutations.append(swapped)

        for documents in mutations:
            with self.subTest(), self.assertRaises(fresh.Rejected):
                fresh.validate(workload_path=self.temp_yaml(documents))

    def test_short_or_cross_namespace_database_host_is_rejected(self):
        base = list(yaml.safe_load_all(fresh.WORKLOAD.read_text(encoding="utf-8")))
        for host in ("account-postgresql", "account-postgresql.default.svc",
                     "account-postgresql.fresh-account.svc.cluster.local"):
            changed = copy.deepcopy(base)
            for document in changed:
                if document.get("kind") not in {"Job", "Rollout"}:
                    continue
                if document["metadata"]["name"] not in {"account-migration", "account-api"}:
                    continue
                env = document["spec"]["template"]["spec"]["containers"][0]["env"]
                next(item for item in env if item["name"] == "PGHOST")["value"] = host
            with self.subTest(host=host), self.assertRaisesRegex(
                    fresh.Rejected, "finite-profile-values"):
                fresh.validate(workload_path=self.temp_yaml(changed))

    def test_postgresql_and_role_bootstrap_require_no_privilege_escalation(self):
        for kind, name in (("StatefulSet", "account-postgresql"),
                           ("Job", "account-db-role-bootstrap")):
            for value in (None, True):
                documents = list(yaml.safe_load_all(fresh.WORKLOAD.read_text(encoding="utf-8")))
                document = next(item for item in documents if item.get("kind") == kind and
                                item["metadata"]["name"] == name)
                container = document["spec"]["template"]["spec"]["containers"][0]
                if value is None:
                    container.pop("securityContext", None)
                else:
                    container["securityContext"]["allowPrivilegeEscalation"] = value
                with self.subTest(kind=kind, name=name, value=value), self.assertRaisesRegex(
                        fresh.Rejected, "container-allow-privilege-escalation"):
                    fresh.validate(workload_path=self.temp_yaml(documents))

    def test_init_containers_require_no_privilege_escalation(self):
        for value in (None, True):
            documents = list(yaml.safe_load_all(fresh.WORKLOAD.read_text(encoding="utf-8")))
            migration = next(item for item in documents if item.get("kind") == "Job" and
                             item["metadata"]["name"] == "account-migration")
            init = {"name": "contract-fixture", "image": "example.invalid/fixture@sha256:" + "0" * 64}
            if value is not None:
                init["securityContext"] = {"allowPrivilegeEscalation": value}
            migration["spec"]["template"]["spec"]["initContainers"] = [init]
            with self.subTest(value=value), self.assertRaisesRegex(
                    fresh.Rejected, "container-allow-privilege-escalation"):
                fresh.validate(workload_path=self.temp_yaml(documents))

    def test_runtime_and_migration_database_hosts_cannot_diverge(self):
        documents = list(yaml.safe_load_all(fresh.WORKLOAD.read_text(encoding="utf-8")))
        migration = next(item for item in documents if item.get("kind") == "Job" and
                         item["metadata"]["name"] == "account-migration")
        env = migration["spec"]["template"]["spec"]["containers"][0]["env"]
        next(item for item in env if item["name"] == "PGHOST")["value"] = "account-postgresql"
        with self.assertRaisesRegex(fresh.Rejected, "migrate-finite-profile-values"):
            fresh.validate(workload_path=self.temp_yaml(documents))

    def test_namespace_identity_and_schema_contract_drift_are_rejected(self):
        documents = list(yaml.safe_load_all(fresh.WORKLOAD.read_text(encoding="utf-8")))
        migration = next(item for item in documents if item.get("kind") == "Job" and
                         item["metadata"]["name"] == "account-migration")
        env = migration["spec"]["template"]["spec"]["containers"][0]["env"]
        next(item for item in env if item["name"] == "POD_NAMESPACE")["valueFrom"]["fieldRef"]["fieldPath"] = "metadata.name"
        with self.assertRaises(fresh.Rejected):
            fresh.validate(workload_path=self.temp_yaml(documents))

        contract = json.loads(fresh.CONTRACT.read_text(encoding="utf-8"))
        contract["applicationRuntimeContract"]["database"]["schema"] = "public"
        with self.assertRaises(fresh.Rejected):
            fresh.validate(contract_path=self.temp_json(contract))

if __name__ == "__main__":
    unittest.main()
