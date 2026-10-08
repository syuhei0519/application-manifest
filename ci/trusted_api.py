# 固定GitLab originへの認証付きread API。producer/consumer/直前checkerが使う。
# 応答サイズ・時間・redirectを制限し、失敗を成功へ変換しない。token値やraw応答を診断へ出さない。
"""宛先と取得量を限定したGitLab読取。認証付きredirectを追跡しない。"""
import json
import ssl
import urllib.error
import urllib.request

BASE='https://gitlab.com/api/v4'

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):
        return None

class GitLab:
    def __init__(self,token,job_token=False):
        if not token:
            raise ValueError('required read credential is absent')
        self.header='JOB-TOKEN' if job_token else 'PRIVATE-TOKEN'
        self.token=token
        context=ssl.create_default_context(cafile='/etc/ssl/certs/ca-certificates.crt')
        self.opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect(),urllib.request.HTTPSHandler(context=context))

    def read(self,path,limit=8*1024**2):
        # pathは信頼済みの数値ID constructorだけから構成する。
        if not path.startswith('/') or '..' in path or '\\' in path or '#' in path:
            raise ValueError('invalid fixed API path')
        request=urllib.request.Request(BASE+path,headers={self.header:self.token})
        try:
            with self.opener.open(request,timeout=10) as response:
                data=response.read(limit+1)
                if len(data)>limit:
                    raise ValueError('API response exceeds bound')
                return data
        except (urllib.error.HTTPError,urllib.error.URLError,TimeoutError):
            raise ValueError('fixed-origin API read refused; private diagnostics suppressed') from None

    def json(self,path):
        return json.loads(self.read(path))
