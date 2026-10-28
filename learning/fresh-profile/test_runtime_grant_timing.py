"""Execute the manifest shell with virtual-time subprocesses, without a DB or sleep."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parent
# Fake only time/psql/timeout. The real manifest's shell, branching, command
# ordering and exit propagation execute unchanged. Real PG coverage is separate.
COMMAND = r'''
import json, os
from pathlib import Path
import subprocess, sys
state = Path(os.environ["CLOCK_STATE"])
data = json.loads(state.read_text())
name = Path(sys.argv[0]).name
args = sys.argv[1:]
def save():
    state.write_text(json.dumps(data))
def advance(seconds):
    end = data["now"] + seconds
    deadline = float(os.environ["FAKE_DEADLINE"])
    data["now"] = min(end, deadline)
    save()
    if end >= deadline:
        sys.exit(124)
if name == "timeout":
    assert args[0] == "--kill-after=5s", args
    duration = float(args[1].removesuffix("s"))
    data["timeouts"].append(duration)
    save()
    env = dict(os.environ, FAKE_DEADLINE=str(data["now"] + duration))
    sys.exit(subprocess.run(args[2:], env=env).returncode)
elif name == "sleep":
    advance(float(args[0]))
elif name == "psql":
    if "--set=grant_runtime=1" in args:
        data["grant_started"] = data["now"]
        save()
        advance(float(os.environ["GRANT_SECONDS"]))
    else:
        assert "account.schema_migrations" in args[-1], args
        advance(float(os.environ["QUERY_SECONDS"]))
        print("t" if data["now"] >= float(os.environ["COMMIT_AT"]) else "f")
else:
    raise AssertionError(name)
'''


class RuntimeGrantTimingTests(unittest.TestCase):
    def run_manifest(self, commit_at, query_seconds=0, grant_seconds=1):
        job = next(d for d in yaml.safe_load_all((ROOT / "workload/account.yaml").read_text())
                   if d["kind"] == "Job" and d["metadata"]["name"] == "account-migration")
        container = job["spec"]["template"]["spec"]["containers"][1]
        with tempfile.TemporaryDirectory(prefix="grant-clock-") as temporary:
            root = Path(temporary)
            clock = root / "clock.json"
            clock.write_text(json.dumps({"now": 0, "timeouts": []}))
            for name in ("timeout", "sleep", "psql"):
                path = root / name
                path.write_text(f"#!{sys.executable}\n" + COMMAND)
                path.chmod(0o700)
            env = dict(os.environ, PATH=f"{root}:{os.environ['PATH']}", CLOCK_STATE=str(clock),
                       COMMIT_AT=str(commit_at), QUERY_SECONDS=str(query_seconds),
                       GRANT_SECONDS=str(grant_seconds))
            result = subprocess.run(container["command"] + container["args"], env=env,
                                    text=True, capture_output=True, timeout=20)
            return result, json.loads(clock.read_text()), job["spec"]["activeDeadlineSeconds"]

    def test_successful_commit_through_migrator_deadline_has_grant_budget(self):
        for commit_at in (160, 165, 169, 170):
            with self.subTest(commit_at=commit_at):
                result, state, deadline = self.run_manifest(commit_at, grant_seconds=29)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertGreaterEqual(state["grant_started"], commit_at)
                self.assertLessEqual(state["grant_started"], commit_at + 2)
                self.assertEqual(state["timeouts"], [190, 30])
                self.assertLess(state["now"], deadline)
                self.assertGreaterEqual(deadline - (190 + 30 + 5 + 5), 30)

    def test_history_wait_fails_bounded_without_running_grants(self):
        for query_seconds in (0, 2):
            with self.subTest(query_seconds=query_seconds):
                result, state, _ = self.run_manifest(999, query_seconds=query_seconds)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(state["now"], 190)
                self.assertNotIn("grant_started", state)
                self.assertEqual(state["timeouts"], [190])

    def test_slow_poll_is_included_in_wall_time_budget(self):
        result, state, _ = self.run_manifest(169, query_seconds=2)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(state["grant_started"], 170)

    def test_grant_sql_has_separate_bounded_failure(self):
        result, state, _ = self.run_manifest(170, grant_seconds=100)
        self.assertEqual(result.returncode, 124)
        self.assertEqual(state["now"], 200)
        self.assertEqual(state["timeouts"], [190, 30])


if __name__ == "__main__":
    unittest.main()
