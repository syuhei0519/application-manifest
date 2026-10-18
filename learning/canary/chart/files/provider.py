"""Internal fixed-result fixture for Account analysis, never an SLO signal."""
import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        request = urlsplit(self.path)
        if request.path == '/healthz':
            self.respond(200, {'ready': True})
            return
        query = parse_qs(request.query)
        required = ('service', 'namespace', 'latest-hash', 'stable-hash', 'case')
        if request.path != '/measure' or any(len(query.get(k, [])) != 1 for k in required):
            self.respond(400, {'error': 'explicit analysis arguments required'})
            return
        args = {k: query[k][0] for k in required}
        case = args.pop('case')
        if args['service'] != 'account' or case not in ('success', 'failure', 'inconclusive', 'missing', 'timeout'):
            self.respond(400, {'error': 'unsupported fixture input'})
            return
        if case == 'timeout':
            time.sleep(3)  # Deliberately exceeds the web provider's one second timeout.
        result = dict(args)
        if case != 'missing':
            result['value'] = {'success': 1, 'failure': 0, 'inconclusive': 0.5, 'timeout': 1}[case]
        self.respond(200, result)

    def respond(self, status, payload):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass  # Expected when the timeout test client has already disconnected.

    def log_message(self, *_):
        pass


if __name__ == '__main__':
    ThreadingHTTPServer(('0.0.0.0', 8080), Handler).serve_forever()
