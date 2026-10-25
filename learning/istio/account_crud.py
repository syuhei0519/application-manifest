#!/usr/bin/env python3
# 承認した試験環境で Account の CRUD を確認し、経路切替による API への影響を調べます。
"""Explicit Account non-impact check after routing restoration. Writes one temporary row."""
import argparse
import json
import urllib.error
import urllib.parse
import urllib.request
import uuid


def check(base):
    parsed = urllib.parse.urlsplit(base)
    if parsed.scheme != 'http' or parsed.hostname != '127.0.0.1' or not parsed.port or parsed.path or parsed.query or parsed.fragment or parsed.username:
        raise ValueError('use the explicit loopback port-forward origin, e.g. http://127.0.0.1:18080')
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    def request(path, method='GET', body=None, expected=200):
        req = urllib.request.Request(base + '/api/accounts' + path,
            data=None if body is None else json.dumps(body).encode(), method=method,
            headers={'Content-Type': 'application/json', 'Connection': 'close'})
        try:
            response = opener.open(req, timeout=5)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            raw = response.read(65536)
            if response.status != expected:
                raise ValueError(f'{method} Account returned {response.status}, expected {expected}')
            return json.loads(raw) if raw and expected != 404 else None

    email = 'istio-restore-' + uuid.uuid4().hex + '@example.test'
    created = request('', 'POST', {'name': 'Istio restore check', 'email': email}, 201)
    account_id = created['id']
    if not isinstance(account_id, int) or isinstance(account_id, bool) or account_id <= 0:
        raise ValueError('unexpected Account ID; stop and clean up the created row privately')
    path = '/' + str(account_id)
    try:
        found = request(path)
        if found['email'] != email or found['id'] != account_id:
            raise ValueError('created Account not persisted')
        updated = request(path, 'PUT', {'name': 'Istio restored', 'email': email})
        found = request(path)
        if updated['name'] != 'Istio restored' or found['name'] != 'Istio restored' or found['email'] != email:
            raise ValueError('updated Account not persisted')
    finally:
        request(path, 'DELETE', expected=204)
    request(path, expected=404)
    print('PASS: Account create/read/update/read/delete/404; temporary row removed')


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', required=True)
    check(parser.parse_args().base_url)
