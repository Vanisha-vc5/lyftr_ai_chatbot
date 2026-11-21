# ---- builder ----
FROM python:3.12-slim AS builder
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1
RUN python -m venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ---- runtime ----
FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH=/opt/venv/bin:$PATH
# The app runs as the non-root "vani" user. The entrypoint starts as root only to
# chown /data (Railway mounts volumes owned by root), then drops privileges.
RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin vani \
    && mkdir -p /data/litellm_cache && chown -R vani:vani /data
COPY --from=builder /opt/venv /opt/venv
WORKDIR /srv
COPY app ./app
COPY --chmod=755 docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
  CMD python -c "import os,urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",\"8000\")}/health', timeout=4)" || exit 1
# One worker on purpose: in-process locks + a single SQLite writer.
CMD ["sh", "-c", "uvicorn app.main:create_app --factory --host 0.0.0.0 --port ${PORT:-8000} --workers 1 --proxy-headers --forwarded-allow-ips='*'"]
