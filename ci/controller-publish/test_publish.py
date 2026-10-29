import importlib.util
import io
import json
import pathlib
import subprocess
import tarfile
import tempfile
import unittest
import contextlib
from unittest import mock

HERE=pathlib.Path(__file__).parent
def load(name):
    spec=importlib.util.spec_from_file_location(name,HERE/(name+'.py'))
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module
p=load('publish');v=load('verify_oci')

def result(code=0,out='',err=''):
    return subprocess.CompletedProcess([],code,out,err)

class RegistryContract(unittest.TestCase):
    def test_tag_decisions(self):
        self.assertEqual(p.state(result(out=p.MANIFEST)), 'same')
        self.assertEqual(p.state(result(1,err='MANIFEST_UNKNOWN')), 'absent')
        for r in (result(out='sha256:'+'0'*64),result(1,err='DENIED'),result(1,err='MANIFEST_UNKNOWN unauthorized'),result(1,err='timeout')):
            with self.assertRaises(ValueError): p.state(r)

    def simulate(self, digests, push_rc=0, login_rc=0, token_rc=0, token_out=None):
        calls=[]; queue=list(digests)
        if token_out is None: token_out=json.dumps({'token':'opaque-registry-token'})
        def fake(args,secret=None):
            calls.append(args)
            if args[:2]==['auth','login']: return result(login_rc)
            if args[:2]==['auth','token']: return result(token_rc,out=token_out)
            if args[0]=='digest': return queue.pop(0)
            if args[0]=='push': return result(push_rc)
            if args[0]=='export': return result()
            raise AssertionError(args)
        def write(args,**kwargs):
            calls.append(['push',args[1]])
            return result(push_rc)
        with mock.patch.object(p,'crane',side_effect=fake), mock.patch.object(p.subprocess,'run',side_effect=write), mock.patch.object(p,'verify_remote'), mock.patch.object(p,'verify_rootfs'), mock.patch.dict('os.environ',{'GITHUB_ACTOR':'actor','GITHUB_TOKEN':'secret'}), contextlib.redirect_stdout(io.StringIO()):
            try: p.publish('/layout','/tmp/rootfs'); error=None
            except ValueError as exc: error=exc
        return calls,error

    def test_absent_tag_push_once(self):
        calls,error=self.simulate([result(1,err='MANIFEST_UNKNOWN'),result(1,err='MANIFEST_UNKNOWN')])
        self.assertIsNone(error);self.assertEqual(sum(c[0]=='push' for c in calls),1)

    def test_opaque_token_is_not_parsed_by_client(self):
        calls,error=self.simulate([result(1,err='MANIFEST_UNKNOWN'),result(1,err='MANIFEST_UNKNOWN')],token_out=json.dumps({'token':'not-a-jwt'}))
        self.assertIsNone(error);self.assertEqual(sum(c[0]=='push' for c in calls),1)

    def test_existing_same_no_write(self):
        calls,error=self.simulate([result(out=p.MANIFEST)])
        self.assertIsNone(error);self.assertFalse(any(c[0]=='push' for c in calls))

    def test_race_and_unknown_stop_before_write(self):
        for digests in ([result(1,err='DENIED')],[result(out='sha256:'+'0'*64)],[result(1,err='MANIFEST_UNKNOWN'),result(out=p.MANIFEST)]):
            calls,error=self.simulate(digests);self.assertIsNotNone(error);self.assertFalse(any(c[0]=='push' for c in calls))

    def test_failed_write_is_not_retried(self):
        calls,error=self.simulate([result(1,err='MANIFEST_UNKNOWN'),result(1,err='MANIFEST_UNKNOWN')],push_rc=1)
        self.assertEqual((error.phase,error.status),('write','failed-or-uncertain'));self.assertEqual(sum(c[0]=='push' for c in calls),1)

    def test_safe_failure_phase_and_status(self):
        cases=(
            ({'login_rc':1},('authentication','rejected')),
            ({'token_rc':1},('authorization','token-request-rejected')),
        )
        for options,want in cases:
            with self.subTest(want=want):
                calls,error=self.simulate([],**options)
                self.assertEqual((error.phase,error.status),want)
                self.assertFalse(any(c[0]=='push' for c in calls))

class ArchiveContract(unittest.TestCase):
    def test_changed_archive_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            a=pathlib.Path(d)/'bad.tar';a.write_bytes(b'not the reviewed image')
            with self.assertRaisesRegex(ValueError,'archive-sha256'): v.verify(a,pathlib.Path(d)/'out')

    def test_path_traversal_rejected_even_with_matching_hash(self):
        with tempfile.TemporaryDirectory() as d:
            a=pathlib.Path(d)/'bad.tar'
            with tarfile.open(a,'w') as t:
                item=tarfile.TarInfo('../escape');item.size=1;t.addfile(item,io.BytesIO(b'x'))
            with mock.patch.object(v,'ARCHIVE',v.digest(a.read_bytes())):
                with self.assertRaisesRegex(ValueError,'archive-entry'): v.verify(a,pathlib.Path(d)/'out')

if __name__=='__main__': unittest.main()
