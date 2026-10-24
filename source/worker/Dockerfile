# Judge worker. Build context là source/ (cần cả common/ và worker/):
#   docker build -f worker/Dockerfile --target runtime -t judgehive-worker .
#   docker build -f worker/Dockerfile --target test -t judgehive-worker-test .
# Base ghim theo bản Debian (không dùng tag trôi).

FROM python:3.11-slim-trixie AS base

# tini: PID 1 reap zombie (tiến trình mồ côi của code thí sinh sẽ reparent về PID 1, và mỗi zombie chiếm một suất pids_limit).
RUN apt-get update \
    && apt-get install -y --no-install-recommends g++ openjdk-21-jdk-headless tini \
    && rm -rf /var/lib/apt/lists/* \
    && command -v setpriv sleep setsid tini >/dev/null

# uid/gid chạy code thí sinh. Worker chạy root để hạ quyền con bằng setpriv; root cần SETUID, SETGID, KILL, CHOWN
# (xem worker/README.md).
RUN groupadd --gid 61000 judge \
    && useradd --uid 61000 --gid 61000 --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin judge

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app \
    JH_WORK_ROOT=/work

WORKDIR /app


FROM base AS test

# Mã nguồn được bind mount vào /src lúc chạy (không COPY), nên image test chỉ build lại khi đổi toolchain.
COPY requirements-dev.txt /tmp/requirements-dev.txt
RUN pip install --no-cache-dir -r /tmp/requirements-dev.txt
WORKDIR /src
ENTRYPOINT ["tini", "--"]
CMD ["python", "-m", "pytest", "tests", "-q"]


FROM base AS runtime

COPY common /app/common
COPY worker /app/worker
# Code thí sinh (uid judge) không đọc được mã worker.
RUN chmod -R go-rwx /app && mkdir -p /work && chmod 755 /work
ENTRYPOINT ["tini", "--", "python", "-m", "worker.main"]
