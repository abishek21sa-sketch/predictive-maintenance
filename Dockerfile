FROM python:3.13-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PDM_AUTH_MODE=local
COPY pyproject.toml README.md ./
COPY src ./src
COPY scripts ./scripts
COPY artifacts ./artifacts
COPY data ./data
RUN pip install --no-cache-dir '.[managed]' \
    && useradd --create-home --uid 10001 --shell /usr/sbin/nologin pdm \
    && chown -R pdm:pdm /app
USER pdm
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health/ready', timeout=4)"
CMD ["uvicorn", "pdm_intelligence.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
