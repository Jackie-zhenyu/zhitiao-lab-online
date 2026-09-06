# Optional local container delivery. A generated Dockerfile is not a runtime verification.
# Official tag verified at https://hub.docker.com/_/python (2026-09-06).
FROM python:3.12.14-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PATH="/app/.venv/bin:$PATH"
WORKDIR /app
COPY requirements.lock.txt ./
RUN python -m venv /app/.venv \
    && /app/.venv/bin/python -m pip --require-virtualenv install --no-cache-dir --only-binary=:all: -r requirements.lock.txt \
    && /app/.venv/bin/python -m pip check \
    && groupadd --gid 10001 lab \
    && useradd --uid 10001 --gid lab --create-home lab

COPY app.py ./
COPY core/ ./core/
COPY ui/ ./ui/
COPY agent/ ./agent/
COPY reports/ ./reports/
COPY projects/ ./projects/
COPY examples/ ./examples/
COPY .streamlit/config.toml ./.streamlit/config.toml
USER lab:lab
EXPOSE 8501
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD ["/app/.venv/bin/python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8501/_stcore/health', timeout=3).close()"]
CMD ["/app/.venv/bin/python", "-m", "streamlit", "run", "app.py", "--server.address=0.0.0.0", "--server.port=8501", "--server.headless=true", "--browser.gatherUsageStats=false"]
