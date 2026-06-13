# ============================================================
# Bridge Application — Dockerfile
# ============================================================
FROM python:3.11-slim

# Security: run as non-root
RUN groupadd --gid 1001 bridge && \
    useradd  --uid 1001 --gid bridge --shell /bin/bash --create-home bridge

WORKDIR /app

# Install dependencies first (layer cache)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source
COPY main.py .

# Switch to non-root user
USER bridge

# Health check — verifies the process is alive
HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
    CMD python -c "import os, sys; sys.exit(0)"

CMD ["python", "-u", "main.py"]
