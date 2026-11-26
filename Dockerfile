# syntax=docker/dockerfile:1
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HOME=/tmp \
    MATCHER_HOST=0.0.0.0 \
    MATCHER_PORT=8080

WORKDIR /app

COPY pyproject.toml README.md ./
COPY shoko_matcher ./shoko_matcher

RUN pip install --no-cache-dir ".[web]"

# Run unprivileged; the web UI keeps jobs in memory and needs no writable paths.
USER 10001:10001

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import os,urllib.request,sys; p=os.environ.get('MATCHER_PORT','8080'); sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:'+p+'/api/health', timeout=4).status == 200 else 1)"

CMD ["shoko-matcher-web"]
