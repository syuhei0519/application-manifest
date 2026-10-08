# 選択digestのregistry manifest/config/OCIラベルを読み、source identityを独立照合する。
# trusted-consumer.pyがサービス別read資格情報を注入する。API資格情報との分離契約はdocs/trusted-phase0-verification.md。
"""固定GitLab registryの読取専用reader。projectごとのpull資格情報だけを使用する。"""
import base64
import hashlib
import json
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request

ACCEPT='application/vnd.oci.image.manifest.v1+json, application/vnd.docker.distribution.manifest.v2+json, application/vnd.oci.image.index.v1+json, application/vnd.docker.distribution.manifest.list.v2+json'

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):
        return None

def validate_blob_redirect(location,digest):
    if (not isinstance(location,str) or len(location)>8192
            or any(ord(char)<33 or ord(char)>126 for char in location)
            or not re.fullmatch('sha256:[0-9a-f]{64}',digest)):
        raise ValueError('invalid blob redirect')
    parsed=urllib.parse.urlsplit(location)
    value=digest.split(':')[1]
    expected='/gitlab/docker/registry/v2/blobs/sha256/'+value[:2]+'/'+value+'/data'
    if (parsed.scheme!='https' or parsed.hostname!='cdn.registry.gitlab-static.net'
            or parsed.port not in (None,443) or parsed.username is not None
            or parsed.password is not None or parsed.fragment or parsed.path!=expected):
        raise ValueError('blob redirect outside fixed CDN/digest')
    return location  # Preserve the signed query verbatim; never log it.

class Registry:
    def __init__(self,service,username,password):
        if service not in ('frontend','backend') or not username or not password:
            raise ValueError('separate project pull credential required')
        self.service=service
        self.path='syuhei-platform-engineering-lab/'+service+'-app'
        context=ssl.create_default_context(cafile='/etc/ssl/certs/ca-certificates.crt')
        self.opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect(),urllib.request.HTTPSHandler(context=context))
        query=urllib.parse.urlencode({'service':'container_registry','scope':'repository:'+self.path+':pull'})
        auth=base64.b64encode((username+':'+password).encode()).decode()
        token=json.loads(self.read('https://gitlab.com/jwt/auth?'+query,{'Authorization':'Basic '+auth},1024**2))
        self.token=token.get('token') or token.get('access_token')
        if not isinstance(self.token,str) or not self.token:
            raise ValueError('registry token absent')

    def read(self,url,headers,limit,redirect_digest=None):
        try:
            with self.opener.open(urllib.request.Request(url,headers=headers),timeout=10) as response:
                data=response.read(limit+1)
                if len(data)>limit:
                    raise ValueError('registry response exceeds bound')
                return data
        except urllib.error.HTTPError as error:
            try:
                if (redirect_digest is None or error.code not in (302,307)
                        or url!='https://registry.gitlab.com/v2/'+self.path+'/blobs/'+redirect_digest):
                    raise ValueError('fixed registry read refused; private diagnostics suppressed')
                location=validate_blob_redirect(error.headers.get('Location'),redirect_digest)
            finally:
                error.close()
            # 固定CDNへの1 hopだけを許可。Authorization/cookie/refererを送らず、それ以降のredirectを拒否する。
            try:
                with self.opener.open(urllib.request.Request(location,headers={}),timeout=10) as response:
                    data=response.read(limit+1)
                    if len(data)>limit:
                        raise ValueError('registry response exceeds bound')
                    return data
            except (urllib.error.HTTPError,urllib.error.URLError,TimeoutError):
                raise ValueError('fixed CDN blob read refused; private diagnostics suppressed') from None
        except (urllib.error.URLError,TimeoutError):
            raise ValueError('fixed registry read refused; private diagnostics suppressed') from None

    def object(self,kind,reference):
        data=self.read('https://registry.gitlab.com/v2/'+self.path+'/'+kind+'/'+reference,
            {'Authorization':'Bearer '+self.token,'Accept':ACCEPT},4*1024**2,
            redirect_digest=reference if kind=='blobs' else None)
        if kind=='blobs' and 'sha256:'+hashlib.sha256(data).hexdigest()!=reference:
            raise ValueError('OCI config checksum mismatch')
        return data,json.loads(data)

    def verify(self,proof):
        # proofはすでに固定repository/SHA/digestの契約を通過している。
        data,manifest=self.object('manifests',proof['commit'])
        if 'sha256:'+hashlib.sha256(data).hexdigest()!=proof['digest']:
            raise ValueError('registry tag does not match the selected release digest')
        if 'manifests' in manifest:
            selected=[entry for entry in manifest['manifests'] if entry.get('platform',{}).get('os')=='linux' and entry.get('platform',{}).get('architecture')=='amd64']
            if len(selected)!=1:
                raise ValueError('exactly one linux/amd64 manifest required')
            digest=selected[0].get('digest','')
            if not __import__('re').fullmatch('sha256:[0-9a-f]{64}',digest):
                raise ValueError('invalid platform manifest digest')
            data,manifest=self.object('manifests',digest)
            if 'sha256:'+hashlib.sha256(data).hexdigest()!=digest:
                raise ValueError('platform manifest checksum mismatch')
        config_digest=manifest.get('config',{}).get('digest','')
        if not __import__('re').fullmatch('sha256:[0-9a-f]{64}',config_digest):
            raise ValueError('invalid OCI config digest')
        data,config=self.object('blobs',config_digest)
        if 'sha256:'+hashlib.sha256(data).hexdigest()!=config_digest:
            raise ValueError('OCI config checksum mismatch')
        labels=config.get('config',{}).get('Labels',{})
        if (config.get('os')!='linux' or config.get('architecture')!='amd64'
                or labels.get('org.opencontainers.image.revision')!=proof['commit']
                or labels.get('org.opencontainers.image.source')!='https://gitlab.com/'+self.path):
            raise ValueError('OCI platform/revision/source mismatch')
