"""OCIのbytesを入力にchecksum/platform/revisionの不一致を検出する試験。実registryは使用しない。"""
import copy
import hashlib
import importlib.util
import json
import io
from pathlib import Path
import unittest
from unittest.mock import Mock
import urllib.error

spec=importlib.util.spec_from_file_location('registry',Path(__file__).with_name('trusted-registry.py'))
registry=importlib.util.module_from_spec(spec); spec.loader.exec_module(registry)
SHA='a'*40

def blob(value):
    raw=json.dumps(value).encode()
    return 'sha256:'+hashlib.sha256(raw).hexdigest(),(raw,value)

def fixture(index=False,labels=None,arch='amd64'):
    labels=labels or {'org.opencontainers.image.revision':SHA,'org.opencontainers.image.source':'https://gitlab.com/syuhei-platform-engineering-lab/backend-app'}
    config_digest,config=blob({'os':'linux','architecture':arch,'config':{'Labels':labels}})
    digest,manifest=blob({'schemaVersion':2,'config':{'digest':config_digest}})
    objects={('blobs',config_digest):config,('manifests',digest):manifest}
    if index:
        digest,manifest=blob({'schemaVersion':2,'manifests':[{'digest':digest,'platform':{'os':'linux','architecture':'amd64'}}]})
    objects[('manifests',SHA)]=manifest
    reader=registry.Registry.__new__(registry.Registry)
    reader.path='syuhei-platform-engineering-lab/backend-app'
    reader.object=lambda kind,reference:objects[(kind,reference)]
    return reader,{'commit':SHA,'digest':digest},objects

class RegistryTest(unittest.TestCase):
    def test_fixed_single_manifest_and_linux_index(self):
        for index in (False,True):
            reader,proof,_=fixture(index=index); reader.verify(proof)

    def test_refuses_wrong_tag_digest_and_config_platform(self):
        reader,proof,_=fixture(); proof['digest']='sha256:'+'0'*64
        with self.assertRaisesRegex(ValueError,'selected release digest'): reader.verify(proof)
        reader,proof,_=fixture(arch='arm64')
        with self.assertRaisesRegex(ValueError,'platform/revision/source'): reader.verify(proof)

    def test_refuses_other_oci_source_revision_and_modified_blob(self):
        for key,value in [('org.opencontainers.image.source','https://external.example/x'),('org.opencontainers.image.revision','b'*40)]:
            labels={'org.opencontainers.image.revision':SHA,'org.opencontainers.image.source':'https://gitlab.com/syuhei-platform-engineering-lab/backend-app'}
            labels[key]=value; reader,proof,_=fixture(labels=labels)
            with self.assertRaisesRegex(ValueError,'platform/revision/source'): reader.verify(proof)
        reader,proof,objects=fixture()
        key=next(key for key in objects if key[0]=='blobs')
        raw,value=objects[key]; objects[key]=(raw+b' ',value)
        with self.assertRaisesRegex(ValueError,'config checksum'): reader.verify(proof)

    def test_refuses_authenticated_redirects(self):
        self.assertIsNone(registry.NoRedirect().redirect_request(None,None,None,None,None,None))

    def test_fixed_cdn_blob_hop_strips_credentials_and_preserves_signature(self):
        digest,(raw,value)=blob({'architecture':'amd64'})
        sha=digest.split(':')[1]
        location='https://cdn.registry.gitlab-static.net/gitlab/docker/registry/v2/blobs/sha256/'+sha[:2]+'/'+sha+'/data?Signature=unchanged%2B='
        reader=registry.Registry.__new__(registry.Registry)
        reader.path='syuhei-platform-engineering-lab/backend-app'; reader.token='fake-secret'
        reader.opener=Mock()
        origin='https://registry.gitlab.com/v2/'+reader.path+'/blobs/'+digest
        reader.opener.open.side_effect=[urllib.error.HTTPError(origin,307,'redirect',{'Location':location},None),io.BytesIO(raw)]
        self.assertEqual(reader.object('blobs',digest),(raw,value))
        first,second=[call.args[0] for call in reader.opener.open.call_args_list]
        self.assertEqual(first.get_header('Authorization'),'Bearer fake-secret')
        self.assertEqual(second.full_url,location)
        self.assertEqual(second.header_items(),[])

    def test_rejects_cdn_host_path_scheme_userinfo_and_digest_changes(self):
        digest='sha256:'+'a'*64
        url='https://cdn.registry.gitlab-static.net/gitlab/docker/registry/v2/blobs/sha256/aa/'+'a'*64+'/data?Signature=example'
        for bad in (url.replace('https:','http:'),url.replace('.net/','.net.evil/'),
                url.replace('https://','https://user@'),url.replace('/aa/','/bb/'),
                url.replace('a'*64,'b'*64),url+'#fragment',url.replace('.net/','.net:444/')):
            with self.subTest(url=bad),self.assertRaises(ValueError):
                registry.validate_blob_redirect(bad,digest)

    def test_manifest_redirect_and_second_cdn_redirect_are_refused(self):
        reader=registry.Registry.__new__(registry.Registry)
        reader.path='syuhei-platform-engineering-lab/backend-app'; reader.token='fake-secret'
        reader.opener=Mock()
        digest='sha256:'+'a'*64
        location='https://cdn.registry.gitlab-static.net/gitlab/docker/registry/v2/blobs/sha256/aa/'+'a'*64+'/data'
        error=lambda:urllib.error.HTTPError('https://registry.gitlab.com',307,'redirect',{'Location':location},None)
        reader.opener.open.side_effect=[error()]
        with self.assertRaises(ValueError): reader.object('manifests',SHA)
        reader.opener.open.side_effect=[error(),error()]
        with self.assertRaises(ValueError): reader.object('blobs',digest)

    def test_cdn_blob_bytes_must_match_expected_digest(self):
        digest,(raw,_)=blob({'architecture':'amd64'})
        sha=digest.split(':')[1]
        location='https://cdn.registry.gitlab-static.net/gitlab/docker/registry/v2/blobs/sha256/'+sha[:2]+'/'+sha+'/data'
        reader=registry.Registry.__new__(registry.Registry)
        reader.path='syuhei-platform-engineering-lab/backend-app'; reader.token='fake-secret'
        reader.opener=Mock()
        reader.opener.open.side_effect=[urllib.error.HTTPError('https://registry.gitlab.com',307,'redirect',{'Location':location},None),io.BytesIO(raw+b' ')]
        with self.assertRaisesRegex(ValueError,'config checksum'): reader.object('blobs',digest)

if __name__=='__main__':
    unittest.main()
