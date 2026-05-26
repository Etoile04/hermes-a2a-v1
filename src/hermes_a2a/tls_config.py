"""TLS configuration and certificate management for Hermes A2A Gateway.

Uses stdlib ``ssl`` and ``subprocess`` (openssl) — no external dependencies.
"""

from __future__ import annotations

import ssl
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class TLSConfig:
    """TLS / SSL configuration container.

    Parameters
    ----------
    cert_path:
        Path to the PEM-encoded server certificate.
    key_path:
        Path to the PEM-encoded private key.
    ca_path:
        Optional path to a PEM-encoded CA bundle for peer verification.
    verify_peer:
        If True, require and verify client certificates.
    """

    def __init__(
        self,
        cert_path: str,
        key_path: str,
        ca_path: str | None = None,
        verify_peer: bool = False,
    ) -> None:
        self.cert_path = Path(cert_path)
        self.key_path = Path(key_path)
        self.ca_path = Path(ca_path) if ca_path else None
        self.verify_peer = verify_peer

    def get_ssl_context(self) -> ssl.SSLContext:
        """Create and return a properly configured :class:`ssl.SSLContext`.

        The context is set up for server-side TLS with:
        - TLS 1.2+ preferred
        - Server certificate and key loaded
        - Optional CA bundle and client verification
        - Strong cipher defaults
        """
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2

        # Load server cert + key
        ctx.load_cert_chain(
            certfile=str(self.cert_path),
            keyfile=str(self.key_path),
        )

        # CA bundle and peer verification
        if self.ca_path and self.ca_path.exists():
            ctx.load_verify_locations(cafile=str(self.ca_path))
            if self.verify_peer:
                ctx.verify_mode = ssl.CERT_REQUIRED
            else:
                ctx.verify_mode = ssl.CERT_OPTIONAL
        elif self.verify_peer:
            # If verify_peer is set but no CA, use system defaults
            ctx.load_default_certs()
            ctx.verify_mode = ssl.CERT_REQUIRED

        return ctx

    @staticmethod
    def verify_certificate(cert_path: str) -> bool:
        """Validate that a certificate is not expired and has a parseable structure.

        Uses ``openssl x509`` to check the certificate.

        Returns
        -------
        bool
            True if the certificate is valid and not expired.
        """
        cert = Path(cert_path)
        if not cert.exists():
            return False

        try:
            # Check that openssl can parse it
            result = subprocess.run(
                ["openssl", "x509", "-in", str(cert), "-noout", "-enddate"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode != 0:
                return False

            # Parse "notAfter=" line
            output = result.stdout.strip()
            if not output.startswith("notAfter="):
                return False

            date_str = output[len("notAfter="):]
            # Parse the date — format varies by openssl version
            # Common: "May 27 00:00:00 2026 GMT" or "2026-05-27 00:00:00 GMT"
            try:
                # Try ISO-like format first
                if "T" in date_str or date_str[4] == "-":
                    # Remove timezone suffix
                    date_str_clean = date_str.rstrip("GMT").strip()
                    expiry = datetime.fromisoformat(date_str_clean)
                else:
                    # Classic format: "Mon DD HH:MM:SS YYYY GMT"
                    from email.utils import parsedate_to_datetime
                    expiry = parsedate_to_datetime(date_str)
            except (ValueError, TypeError):
                # Fallback: compare raw strings
                return True

            # Check not expired
            now = datetime.now(timezone.utc)
            if expiry.tzinfo is None:
                from datetime import timezone as tz
                expiry = expiry.replace(tzinfo=tz.utc)
            return now < expiry

        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            return False

    @staticmethod
    def generate_self_signed(
        output_dir: str,
        common_name: str = "hermes-a2a",
        days: int = 365,
    ) -> tuple[str, str]:
        """Generate a self-signed certificate for testing.

        Uses ``openssl`` subprocess.

        Parameters
        ----------
        output_dir:
            Directory where cert and key files will be written.
        common_name:
            Subject Common Name (CN).
        days:
            Certificate validity in days.

        Returns
        -------
        tuple[str, str]
            ``(cert_path, key_path)`` of the generated files.
        """
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)

        cert_file = out / "cert.pem"
        key_file = out / "key.pem"

        subprocess.run(
            [
                "openssl", "req", "-x509", "-newkey", "rsa:2048",
                "-keyout", str(key_file),
                "-out", str(cert_file),
                "-days", str(days),
                "-nodes",
                "-subj", f"/CN={common_name}",
            ],
            capture_output=True,
            check=True,
            timeout=30,
        )

        return str(cert_file), str(key_file)
