#!/usr/bin/env bash
# Generate self-signed TLS certificates for testing Hermes A2A Gateway.
#
# Usage:
#   ./scripts/generate_test_certs.sh [OUTPUT_DIR]
#
# If OUTPUT_DIR is not specified, defaults to ./test_certs

set -euo pipefail

OUTPUT_DIR="${1:-./test_certs}"
DAYS="${TEST_CERT_DAYS:-365}"
CN="${TEST_CERT_CN:-hermes-a2a}"

mkdir -p "$OUTPUT_DIR"

CERT_FILE="$OUTPUT_DIR/cert.pem"
KEY_FILE="$OUTPUT_DIR/key.pem"

echo "Generating self-signed certificate..."
echo "  Output dir : $OUTPUT_DIR"
echo "  Common Name: $CN"
echo "  Valid for  : $DAYS days"

openssl req -x509 -newkey rsa:2048 \
    -keyout "$KEY_FILE" \
    -out "$CERT_FILE" \
    -days "$DAYS" \
    -nodes \
    -subj "/CN=$CN" \
    2>/dev/null

chmod 644 "$CERT_FILE"
chmod 600 "$KEY_FILE"

echo "Done."
echo "  Certificate: $CERT_FILE"
echo "  Private key : $KEY_FILE"
