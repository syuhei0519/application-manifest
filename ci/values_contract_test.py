import os
from pathlib import Path
import re
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
HELM = os.environ.get("HELM_BIN", "helm")


def render(chart, values, *extra, ok=True):
    command = [HELM, "template", chart, str(ROOT / "charts" / chart), "--namespace", "account", "-f", str(ROOT / values), *extra]
    result = subprocess.run(command, cwd=ROOT, text=True, encoding="utf-8", errors="replace", capture_output=True)
    if ok and result.returncode != 0:
        raise AssertionError(result.stderr)
    if not ok and result.returncode == 0:
        raise AssertionError("unsupported values unexpectedly rendered")
    return result


class ValuesContractTests(unittest.TestCase):
    def test_backend_values_change_the_workload(self):
        base = render("backend", "environments/local/backend.yaml").stdout
        changed = render(
            "backend", "environments/local/backend.yaml",
            "--set", "replicaCount=2",
            "--set", "config.logLevel=debug",
            "--set", "probes.readiness.periodSeconds=7",
            "--set", "image.pullPolicy=Never",
            "--set-json", "imagePullSecrets=[]",
        ).stdout
        self.assertIn("replicas: 2", changed)
        self.assertIn('log-level: "debug"', changed)
        self.assertRegex(changed, r"readinessProbe:\s+httpGet: \{path: /readyz, port: http\}\s+periodSeconds: 7")
        self.assertEqual(changed.count("imagePullPolicy: Never"), 2)
        self.assertRegex(changed, r"imagePullSecrets:\s+\[\]")
        before = re.search(r"checksum/config: ([0-9a-f]+)", base).group(1)
        after = re.search(r"checksum/config: ([0-9a-f]+)", changed).group(1)
        self.assertNotEqual(before, after)

    def test_database_values_change_the_pod_without_replacing_the_pvc(self):
        changed = render(
            "postgresql", "environments/local/postgresql.yaml",
            "--set", "config.sharedBuffers=64MB",
            "--set", "image.pullPolicy=Never",
            "--set-json", 'imagePullSecrets=[{"name":"registry-postgresql-pull"}]',
        ).stdout
        self.assertIn("shared_buffers=64MB", changed)
        self.assertIn("imagePullPolicy: Never", changed)
        self.assertIn("name: registry-postgresql-pull", changed)
        self.assertIn("claimName: postgresql-data", changed)
        self.assertNotIn("volumeClaimTemplates:", changed)

    def test_unknown_prefix_and_secret_key_fail_closed(self):
        for extra in (
            ("--set", "config.unknownPrefix=value"),
            ("--set", "secrets.unknownKey=value"),
            ("--set", "imagePullSecrets[0].name=arbitrary-secret"),
            ("--set", "migration.waitTimeout=120"),
        ):
            with self.subTest(extra=extra):
                render("backend", "environments/local/backend.yaml", *extra, ok=False)

    def test_disabled_order_profiles_are_explicit_and_non_workload(self):
        order = render("order", "environments/local/order.yaml").stdout
        database = render("order-postgresql", "environments/local/order-postgresql.yaml").stdout
        self.assertIn("name: order-disabled", order)
        self.assertIn('enabled: "false"', order)
        self.assertNotIn("kind: Deployment", order)
        self.assertIn("name: order-postgresql-disabled", database)
        self.assertNotIn("kind: StatefulSet", database)

    def test_enabled_order_profiles_render_workloads_and_no_marker(self):
        order = render("order", "ci/fixtures/order/enabled.yaml", "--set", "replicaCount=2", "--set", "probes.readiness.periodSeconds=7").stdout
        database = render("order-postgresql", "ci/fixtures/order/postgresql-enabled.yaml").stdout
        self.assertIn("kind: Deployment", order)
        self.assertIn("replicas: 2", order)
        self.assertNotIn("order-disabled", order)
        self.assertIn("kind: StatefulSet", database)
        self.assertNotIn("order-postgresql-disabled", database)

    def test_frontend_api_route_is_preserved(self):
        frontend = render("frontend", "environments/local/frontend.yaml").stdout
        self.assertIn("location /api/", frontend)
        self.assertIn("proxy_pass http://backend.account.svc.cluster.local:8080;", frontend)


if __name__ == "__main__":
    unittest.main()
