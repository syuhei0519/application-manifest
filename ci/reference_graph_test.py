import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
GRAPH = ROOT / "ci" / "active-reference-graph.json"
ALLOWED = {
    "active-current",
    "active-release-definition",
    "legacy-compatibility",
    "planned-disabled",
    "historical-evidence",
    "learning-evidence",
}


class ReferenceGraphTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = json.loads(GRAPH.read_text(encoding="utf-8"))

    def test_finite_existing_paths(self):
        self.assertEqual(self.data["schemaVersion"], "platform-lab/reference-graph/v1")
        entries = self.data["entries"]
        paths = [entry["path"] for entry in entries]
        self.assertEqual(len(paths), len(set(paths)))
        for entry in entries:
            self.assertIn(entry["classification"], ALLOWED)
            self.assertEqual(entry["owner"], "application-manifest")
            self.assertTrue((ROOT / entry["path"]).exists(), entry["path"])
            self.assertIs(type(entry["mutable"]), bool)
            self.assertTrue(entry["purpose"])

    def test_session_is_never_an_active_input(self):
        by_path = {entry["path"]: entry for entry in self.data["entries"]}
        self.assertEqual(by_path["learning/account-release/session"]["classification"], "historical-evidence")
        self.assertFalse(by_path["learning/account-release/session"]["mutable"])
        self.assertEqual(by_path["environments/local"]["classification"], "legacy-compatibility")
        self.assertEqual(by_path["charts/order"]["classification"], "planned-disabled")


if __name__ == "__main__":
    unittest.main()
