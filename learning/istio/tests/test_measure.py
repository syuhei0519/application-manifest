# 通信比率や応答の測定処理を、小さな試験データで確認します。
"""Synthetic accounting tests; these are not Kubernetes traffic evidence."""
import importlib.util
from pathlib import Path
import unittest
import urllib.error

spec = importlib.util.spec_from_file_location('demo', Path(__file__).resolve().parents[1] / 'chart/files/demo.py')
demo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(demo)


class Response:
    status = 200

    def __init__(self, body=b'v1\n'):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        pass

    def read(self, _limit):
        return self.body


class Opener:
    def __init__(self, replies):
        self.replies = iter(replies)
        self.calls = 0

    def open(self, request, timeout):
        assert request.method == 'GET' and timeout == 2
        assert request.get_header('Connection') == 'close'
        self.calls += 1
        response = next(self.replies)
        if isinstance(response, Exception):
            raise response
        return response


class Accounting(unittest.TestCase):
    def measure(self, window, count):
        n = demo.WINDOWS[window][0]
        return demo.measure(window, Opener([Response(b'v2\n')] * count + [Response()] * (n-count)), pause=lambda _: None)

    def test_disjoint_fixed_bands(self):
        for window, lower, upper in [('split90', 60, 140), ('split50', 440, 560)]:
            for count in [lower, upper]:
                self.assertTrue(self.measure(window, count)['passed'])
            for count in [lower-1, upper+1]:
                self.assertFalse(self.measure(window, count)['passed'])
        self.assertFalse(self.measure('split90', 500)['passed'])
        self.assertFalse(self.measure('split50', 100)['passed'])

    def test_restore_and_plain(self):
        for window in ['stable', 'plain']:
            self.assertTrue(self.measure(window, 0)['passed'])
            self.assertFalse(self.measure(window, 1)['passed'])

    def test_failures_count_once_and_never_retry(self):
        replies = [Response(b'v2\n')] * 100 + [Response()] * 896 + [
            urllib.error.HTTPError(demo.URL, 503, 'unavailable', {}, None),
            urllib.error.URLError('synthetic timeout'), Response(b'v3\n'), Response(b'v2\ntrailing')]
        opener = Opener(replies)
        result = demo.measure('split90', opener, pause=lambda _: None)
        self.assertFalse(result['passed'])
        self.assertEqual(opener.calls, 1000)
        self.assertEqual(result['total'], 1000)
        self.assertEqual(result['http_failures'], 1)
        self.assertEqual(result['transport_failures'], 1)
        self.assertEqual(result['unknown_body'], 2)
        self.assertEqual(result['v2_fraction_of_all_requests'], .1)

    def test_pacing(self):
        pauses = []
        result = demo.measure('warmup', Opener([Response()] * 20), pause=pauses.append)
        self.assertTrue(result['passed'])
        self.assertEqual(pauses, [.05] * 20)

    def test_redirect_not_followed(self):
        self.assertIsNone(demo.NoRedirect().redirect_request(None, None, 302, 'moved', {}, 'http://other/'))


if __name__ == '__main__':
    unittest.main()
