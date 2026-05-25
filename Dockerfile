# =============================================================================
# Multi-stage Dockerfile for hermes-a2a-v1
# Stages: builder → runtime
# =============================================================================

# ---------------------------------------------------------------------------
# Stage 1 – Build dependencies in a virtualenv
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS builder

WORKDIR /app

# Install build dependencies (cached unless requirements change)
COPY pyproject.toml ./
RUN pip install --no-cache-dir --prefix=/install .

# Copy source code
COPY src/ ./src/
RUN pip install --no-cache-dir --prefix=/install .

# ---------------------------------------------------------------------------
# Stage 2 – Minimal runtime image
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS runtime

LABEL org.opencontainers.image.source="https://github.com/Etoile04/hermes-a2a-v1"
LABEL org.opencontainers.image.description="Hermes A2A v1.0 Protocol Gateway"
LABEL org.opencontainers.image.licenses="MIT"

# Create non-root user for security
RUN groupadd --gid 1000 appuser \
    && useradd --uid 1000 --gid appuser --shell /bin/bash --create-home appuser

# Install runtime system dependencies (if any) and clean up
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Create application directories
WORKDIR /app
RUN mkdir -p /app/data /app/config \
    && chown -R appuser:appuser /app

# Copy source (for version info, etc.)
COPY --chown=appuser:appuser src/ ./src/
COPY --chown=appuser:appuser pyproject.toml ./

# Switch to non-root user
USER appuser

# Environment defaults
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HERMES_A2A_CONFIG=/app/config/gateway.yaml

EXPOSE 18800

# Health check: hit the /health endpoint every 30s
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:18800/health || exit 1

ENTRYPOINT ["python", "-m", "hermes_a2a.server"]
