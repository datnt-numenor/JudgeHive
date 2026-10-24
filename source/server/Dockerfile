# Server (FastAPI + cổng worker TCP). Build context là source/ (cần cả common/ và server/):
#   docker build -f server/Dockerfile --target runtime -t judgehive-server .
#   docker build -f server/Dockerfile --target test -t judgehive-server-test .
# Base ghim theo bản Debian, cùng base với worker.

FROM python:3.11-slim-trixie AS base

# tini: PID 1 reap zombie và chuyển SIGTERM cho uvicorn (docker stop không phải chờ hết thời hạn rồi bị kill).
RUN apt-get update \
    && apt-get install -y --no-install-recommends tini \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 10001 --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin app \
    && mkdir /data && chown app:app /data

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app \
    JH_DB_PATH=/data/judgehive.db

WORKDIR /app
COPY server/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt


FROM base AS test

# Mã nguồn được bind mount vào /src lúc chạy (không COPY), nên image test chỉ build lại khi đổi phụ thuộc.
COPY requirements-dev.txt /tmp/requirements-dev.txt
RUN pip install --no-cache-dir -r /tmp/requirements-dev.txt
ENV PYTHONPATH=/src
WORKDIR /src
ENTRYPOINT ["tini", "--"]
CMD ["python", "-m", "pytest", "tests/server", "-q"]


FROM base AS runtime

COPY common /app/common
COPY server /app/server
USER app
# DB nằm trong named volume gắn vào /data (WAL cần file -shm dùng chung, không bind mount từ ổ Windows).
VOLUME /data
EXPOSE 8000 9000
# Đúng một process uvicorn: hàng đợi và danh sách worker nằm trong bộ nhớ (không dùng --workers > 1).
ENTRYPOINT ["tini", "--"]
CMD ["sh", "-c", "exec uvicorn server.main:app --host 0.0.0.0 --port ${JH_HTTP_PORT:-8000}"]
