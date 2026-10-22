#!/usr/bin/env python3
"""Run inside the dedicated injected client after environment approval.

Only the fixed Account list API is requested; no data changes or redirects.
Run warmup separately before taking the baseline counter snapshot.
"""
import argparse
import json
import time
import urllib.error
import urllib.request

from contract import ROUTING


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--phase', choices=['warmup', 'measure'], required=True)
    p.add_argument('--weight', type=int, choices=list(ROUTING), required=True)
    a = p.parse_args()
    count = 20 if a.phase == 'warmup' else ROUTING[a.weight]['samples']
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    result = dict(sent=0, ok=0, errors=0, timeouts=0, retries=0, warmup_excluded=a.phase == 'measure')
    start = time.time()
    latencies = []
    for _ in range(count):
        request = urllib.request.Request('http://account-release.learning-account-release.svc.cluster.local:8080/api/accounts',
                                         headers={'Connection': 'close'})
        result['sent'] += 1
        t = time.monotonic()
        try:
            with opener.open(request, timeout=2) as response:
                response.read()
                result['ok' if response.status == 200 else 'errors'] += 1
        except urllib.error.HTTPError:
            result['errors'] += 1
        except (TimeoutError, urllib.error.URLError):
            # Connection failures are counted conservatively with timeouts, never retried.
            result['timeouts'] += 1
        latencies.append(time.monotonic() - t)
    print(json.dumps(dict(phase=a.phase, weight=a.weight, start=start, end=time.time(), client=result,
                         client_latency_seconds=latencies)))


if __name__ == '__main__':
    main()
