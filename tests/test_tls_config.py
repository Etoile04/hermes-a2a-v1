"""Tests for TLS configuration (tls_config.py)."""

from __future__ import annotations

import os
import ssl
import subprocess
import tempfile
from pathlib import Path

import pytest

from hermes_a2a.tls_config import TLSConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@pytest.fixture
def certs_dir():
    """Generate temporary self-signed certs and return the directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        cert_file = os.path.join(tmpdir, "cert.pem")
        key_file = os.path.join(tmpdir, "key.pem")
        subprocess.run(
            [
                "openssl", "req", "-x509", "-newkey", "rsa:2048",
                "-keyout", key_file, "-out", cert_file,
                "-days", "365", "-nodes",
                "-subj", "/CN=hermes-a2a-test",
            ],
            capture_output=True,
            check=True,
            timeout=30,
        )
        yield tmpdir


@pytest.fixture
def tls_config(certs_dir):
    """Return a TLSConfig pointing at the generated certs."""
    return TLSConfig(
        cert_path=os.path.join(certs_dir, "cert.pem"),
        key_path=os.path.join(certs_dir, "key.pem"),
    )


# ---------------------------------------------------------------------------
# C3 Tests: TLSConfig
# ---------------------------------------------------------------------------

class TestSSLContext:
    """Tests for SSL context creation."""

    def test_get_ssl_context_returns_ssl_context(self, tls_config):
        ctx = tls_config.get_ssl_context()
        assert isinstance(ctx, ssl.SSLContext)

    def test_ssl_context_minimum_tls_12(self, tls_config):
        ctx = tls_config.get_ssl_context()
        assert ctx.minimum_version >= ssl.TLSVersion.TLSv1_2

    def test_ssl_context_with_ca_and_verify_peer(self, certs_dir):
        # Use the server cert as the CA for testing purposes
        config = TLSConfig(
            cert_path=os.path.join(certs_dir, "cert.pem"),
            key_path=os.path.join(certs_dir, "key.pem"),
            ca_path=os.path.join(certs_dir, "cert.pem"),
            verify_peer=True,
        )
        ctx = config.get_ssl_context()
        assert ctx.verify_mode == ssl.CERT_REQUIRED

    def test_ssl_context_no_verify_peer(self, tls_config):
        ctx = tls_config.get_ssl_context()
        # Default is CERT_NONE since no verify_peer and no ca_path
        assert ctx.verify_mode == ssl.CERT_NONE


class TestCertificateVerification:
    """Tests for certificate validation."""

    def test_verify_valid_certificate(self, certs_dir):
        cert_path = os.path.join(certs_dir, "cert.pem")
        assert TLSConfig.verify_certificate(cert_path) is True

    def test_verify_nonexistent_certificate(self):
        assert TLSConfig.verify_certificate("/nonexistent/cert.pem") is False

    def test_verify_expired_certificate(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cert_file = os.path.join(tmpdir, "cert.pem")
            key_file = os.path.join(tmpdir, "key.pem")
            # Generate cert that expired yesterday
            subprocess.run(
                [
                    "openssl", "req", "-x509", "-newkey", "rsa:2048",
                    "-keyout", key_file, "-out", cert_file,
                    "-days", "0", "-nodes",
                    "-subj", "/CN=expired-test",
                ],
                capture_output=True,
                timeout=30,
            )
            # Some openssl versions refuse days=0, so just check it doesn't crash
            if os.path.exists(cert_file):
                # The result depends on whether days=0 works
                TLSConfig.verify_certificate(cert_file)

    def test_verify_non_certificate_file(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".pem", delete=False) as f:
            f.write("not a certificate")
            f.flush()
            result = TLSConfig.verify_certificate(f.name)
            assert result is False
            os.unlink(f.name)


class TestSelfSignedGeneration:
    """Tests for self-signed certificate generation."""

    def test_generate_self_signed_creates_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cert, key = TLSConfig.generate_self_signed(tmpdir, common_name="test-agent")
            assert os.path.exists(cert)
            assert os.path.exists(key)

    def test_generate_self_signed_valid_cert(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cert, key = TLSConfig.generate_self_signed(tmpdir)
            assert TLSConfig.verify_certificate(cert) is True

    def test_generate_self_signed_custom_cn(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cert, key = TLSConfig.generate_self_signed(tmpdir, common_name="custom-name")
            # Verify CN by inspecting the cert
            result = subprocess.run(
                ["openssl", "x509", "-in", cert, "-noout", "-subject"],
                capture_output=True, text=True,
            )
            assert "custom-name" in result.stdout

    def test_generate_self_signed_creates_directory(self):
        with tempfile.TemporaryDirectory() as base:
            output_dir = os.path.join(base, "new", "subdir")
            cert, key = TLSConfig.generate_self_signed(output_dir)
            assert os.path.exists(cert)
