FROM python:3.11-slim

RUN groupadd --gid 1001 bridge && \
    useradd  --uid 1001 --gid bridge --shell /bin/bash --create-home bridge

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .

USER bridge

HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
    CMD python -c "import os, sys; sys.exit(0)"

CMD ["python", "-u", "main.py"]
