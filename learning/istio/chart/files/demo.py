"""Dedicated learning fixture. No DB, writes, retries, or environment disclosure."""
import argparse
import datetime
import http.server
import json
import time
import urllib.error
import urllib.request

URL = "http://mesh-demo.learning-istio-demo.svc.cluster.local:8080/version"


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


# Disjoint acceptance bands; failures never disappear from the denominator.
WINDOWS = {"warmup": (20, 0, 20), "plain": (200, 0, 0),
           "stable": (200, 0, 0), "candidate": (200, 200, 200), "split90": (1000, 60, 140),
           "split50": (1000, 440, 560)}


def measure(window, opener=None, url=URL, pause=time.sleep):
    n, lower, upper = WINDOWS[window]
    opener = opener or urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    counts = dict(v1=0, v2=0, http_failures=0, transport_failures=0, unknown_body=0)
    started = now()
    for _ in range(n):
        request = urllib.request.Request(url, headers={"Connection": "close"}, method="GET")
        try:
            with opener.open(request, timeout=2) as response:
                body = response.read(32)
                if response.status != 200:
                    counts["http_failures"] += 1
                elif body in (b"v1\n", b"v2\n"):
                    counts[body.decode().strip()] += 1
                else:
                    counts["unknown_body"] += 1
        except urllib.error.HTTPError:
            counts["http_failures"] += 1
        except (OSError, urllib.error.URLError, http.client.HTTPException):
            counts["transport_failures"] += 1
        pause(0.05)  # Sequential, at most 20 requests/s, no retry or early exit.
    passed = (counts["http_failures"] == counts["transport_failures"] == counts["unknown_body"] == 0
              and lower <= counts["v2"] <= upper and counts["v1"] == n - counts["v2"])
    result = dict(window=window, started=started, ended=now(), total=n,
                  v2_accepted_counts=[lower, upper], **counts,
                  v2_fraction_of_all_requests=counts["v2"] / n, passed=passed)
    assert sum(counts.values()) == n
    return result


def serve(revision, port=8080):
    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self):
            body = (revision + "\n").encode() if self.path == "/version" else b"ok\n"
            status = 200 if self.path in ("/version", "/livez", "/readyz") else 404
            if revision == "failed" and self.path == "/readyz":
                status = 503
            if status == 404:
                body = b"not found\n"
            self.send_response(status)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    http.server.ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    subs = parser.add_subparsers(dest="action", required=True)
    subs.add_parser("serve").add_argument("revision", choices=["v1", "v2", "failed"])
    subs.add_parser("idle")
    measurement = subs.add_parser("measure")
    measurement.add_argument("window", choices=list(WINDOWS))
    measurement.add_argument("--rollout", action="store_true")
    args = parser.parse_args()
    if args.action == "serve":
        serve(args.revision)
    elif args.action == "measure":
        result = measure(args.window, url=URL.replace("mesh-demo.", "mesh-rollout.") if args.rollout else URL)
        print(json.dumps(result, sort_keys=True))
        raise SystemExit(0 if result["passed"] else 1)
    else:
        while True:
            time.sleep(3600)
