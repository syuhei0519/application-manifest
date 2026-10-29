"""Offline proof that the existing-upgrade overlay preserves old resources."""
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
import unittest
import yaml

ROOT=Path(__file__).resolve().parent
REPOSITORY=ROOT.parents[1]
OLD="3707f73581da5d04db0e4771dda7c1e82bd0f47c"
KUBECTL=os.environ.get("KUBECTL_BIN") or shutil.which("kubectl")
ALLOWED={"Rollout/account-api","Service/account-api","DestinationRule/account-api","VirtualService/account-api","AnalysisTemplate/account-api-policy"}

class ExistingUpgradeOverlayTest(unittest.TestCase):
    def test_old_render_is_preserved_except_canary_resources(self):
        if not KUBECTL or not Path(KUBECTL).is_file(): self.skipTest("kubectl fixture unavailable")
        with tempfile.TemporaryDirectory() as directory:
            archive=subprocess.run(["git","-C",str(REPOSITORY),"archive",OLD,"learning/fresh-profile/workload"],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
            if archive.returncode: self.skipTest("old immutable Git object unavailable")
            subprocess.run(["tar","-x","-C",directory],input=archive.stdout,check=True)
            old=Path(directory)/"learning/fresh-profile/workload"
            def render(path):
                result=subprocess.check_output([str(KUBECTL),"kustomize",str(path)])
                return {(o["kind"],o["metadata"].get("namespace"),o["metadata"]["name"]):o for o in yaml.safe_load_all(result) if o}
            before=render(old);after=render(ROOT/"workload-existing")
            for key in before.keys() | after.keys():
                if key[0]+"/"+key[2] not in ALLOWED:
                    self.assertEqual(before.get(key),after.get(key),key)
            self.assertNotEqual(before[("Rollout","fresh-account","account-api")],after[("Rollout","fresh-account","account-api")])
            self.assertFalse(any(key[0]=="Secret" for key in after))
            analysis=("AnalysisTemplate","fresh-account","account-api-policy")
            self.assertNotIn(analysis,before)
            self.assertNotIn("account-api-policy",str(before[("Rollout","fresh-account","account-api")]["spec"]))
            annotations=after[analysis]["metadata"]["annotations"]
            self.assertEqual(annotations["argocd.argoproj.io/compare-options"],"IgnoreExtraneous")
            self.assertEqual(annotations["argocd.argoproj.io/sync-options"],"Prune=false,Delete=false")
            self.assertEqual({k[0]+"/"+k[2] for k in before if k[0]+"/"+k[2] in ALLOWED},ALLOWED-{"AnalysisTemplate/account-api-policy"})

    def test_existing_auto_changes_only_rollout_pauses(self):
        if not KUBECTL or not Path(KUBECTL).is_file(): self.skipTest("kubectl fixture unavailable")
        def render(path):
            data=subprocess.check_output([str(KUBECTL),"kustomize",str(ROOT/path)])
            return {(o["kind"],o["metadata"].get("namespace"),o["metadata"]["name"]):o for o in yaml.safe_load_all(data) if o}
        manual=render("workload-existing"); auto=render("workload-existing-auto")
        self.assertEqual(manual.keys(),auto.keys())
        rollout=("Rollout","fresh-account","account-api")
        steps=manual[rollout]["spec"]["strategy"]["canary"]["steps"]
        self.assertEqual(sum("pause" in step for step in steps),3)
        manual[rollout]["spec"]["strategy"]["canary"]["steps"]=[step for step in steps if "pause" not in step]
        self.assertEqual(manual,auto)
