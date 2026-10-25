# 承認した試験環境で Account API の作成・取得・更新・削除を確認する回帰試験です。
"""Run only inside the approved dedicated client, after Abort traffic measurement."""
import json
import time
import urllib.error
import urllib.request

BASE = 'http://account-release.learning-account-release.svc.cluster.local:8080/api/accounts'


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def main():
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    results = []
    def request(method, suffix='', body=None, expected=200):
        req = urllib.request.Request(BASE+suffix, method=method,
            data=None if body is None else json.dumps(body).encode(),
            headers={'Content-Type': 'application/json', 'Connection': 'close'})
        try:
            response = opener.open(req, timeout=2)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            code, payload = response.status, response.read()
        results.append(dict(method=method, suffix=suffix, expected=expected, actual=code))
        assert code == expected, results[-1]
        return json.loads(payload) if payload else None
    before = request('GET')
    assert before == [], 'dedicated test database must start empty'
    original = {'name': 'PLAT170 synthetic', 'email': 'plat170-'+str(time.time_ns())+'@example.invalid'}
    created = request('POST', body=original, expected=201)
    suffix = '/'+str(created['id'])
    deleted = False
    try:
        assert created['name'] == original['name'] and created['email'] == original['email']
        assert request('GET', suffix) == created
        request('POST', body=original, expected=409)
        updated = dict(original, name='PLAT170 updated')
        value = request('PUT', suffix, updated)
        assert value['id'] == created['id'] and value['name'] == updated['name']
        assert request('GET', suffix) == value
        assert any(row['id'] == created['id'] for row in request('GET'))
        request('POST', body={'name': '', 'email': 'invalid'}, expected=400)
        request('GET', '/invalid', expected=400)
        request('DELETE', suffix, expected=204)
        deleted = True
        request('GET', suffix, expected=404)
        assert request('GET') == []
    finally:
        if not deleted:
            request('DELETE', suffix, expected=204)
    print(json.dumps(dict(passed=True, synthetic_data_removed=True, requests=results)))


if __name__ == '__main__':
    main()
