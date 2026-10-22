import hashlib
import io
import os
import ssl
import unittest
import urllib.error
from contextlib import redirect_stdout
from unittest.mock import Mock, patch

import verify_tls_trust as trust


class TrustVerificationTests(unittest.TestCase):
    def context(self):
        return Mock(get_ca_certs=Mock(return_value=[b"synthetic-certificate"]))

    def test_rejects_empty_store(self):
        with self.assertRaises(RuntimeError):
            trust.inspect_store("test", Mock(get_ca_certs=Mock(return_value=[])), False)

    def test_rejects_blocked_fingerprint(self):
        digest = hashlib.sha256(b"synthetic-certificate").hexdigest()
        with patch.object(trust, "BLOCKED_ROOTS", {digest}), self.assertRaises(RuntimeError):
            trust.inspect_store("test", self.context(), False)

    def test_certificate_failures_pass_after_positive_control(self):
        response = Mock()
        response.__enter__ = Mock(return_value=Mock(status=200))
        response.__exit__ = Mock(return_value=False)
        errors = [urllib.error.URLError(ssl.SSLCertVerificationError("untrusted")) for _ in trust.PROBES]
        with patch.object(trust.urllib.request, "urlopen", side_effect=[response, *errors]) as request:
            with redirect_stdout(io.StringIO()):
                trust.inspect_store("test", self.context(), True)
            self.assertEqual(request.call_count, 3)

    def test_dns_failure_is_not_certificate_rejection(self):
        response = Mock()
        response.__enter__ = Mock(return_value=Mock(status=200))
        response.__exit__ = Mock(return_value=False)
        with patch.object(trust.urllib.request, "urlopen", side_effect=[response, urllib.error.URLError("DNS")]):
            with self.assertRaises(urllib.error.URLError):
                trust.inspect_store("test", self.context(), True)

    def test_accepting_compromised_chain_fails(self):
        response = Mock()
        response.__enter__ = Mock(return_value=Mock(status=200))
        response.__exit__ = Mock(return_value=False)
        with patch.object(trust.urllib.request, "urlopen", return_value=response):
            with self.assertRaises(RuntimeError):
                trust.inspect_store("test", self.context(), True)

    def test_grpc_requires_the_os_ca_file(self):
        with patch.dict(os.environ, {"GRPC_DEFAULT_SSL_ROOTS_FILE_PATH": "/missing"}, clear=False):
            with self.assertRaises(RuntimeError):
                with patch.object(trust.Path, "is_file", return_value=False):
                    trust.verify_grpc_roots()


if __name__ == "__main__":
    unittest.main()
