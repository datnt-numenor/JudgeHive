# Server (FastAPI + cổng worker TCP + giao diện web đã build). Build context là source/ (cần common/, server/ và client/):
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
COPY server/requirements.txt server/constraints.txt /tmp/
RUN pip install --no-cache-dir -r /tmp/requirements.txt -c /tmp/constraints.txt


FROM base AS test

# Mã nguồn được bind mount vào /src lúc chạy (không COPY), nên image test chỉ build lại khi đổi phụ thuộc.
COPY requirements-dev.txt /tmp/requirements-dev.txt
RUN pip install --no-cache-dir -r /tmp/requirements-dev.txt -c /tmp/constraints.txt
ENV PYTHONPATH=/src
WORKDIR /src
ENTRYPOINT ["tini", "--"]
CMD ["python", "-m", "pytest", "tests/server", "-q"]


FROM node:22-slim AS client-build

# npm 10.9.x (đi kèm node:22-slim) lỗi nội bộ khi giải peer dependency của vitest 5 (xem client/README.md), nên cài npm 11
# bản cố định. Cần mạng lúc build (npm ci). Tách lớp: sửa mã client không cài lại phụ thuộc.
RUN npm i -g npm@11.21.1
WORKDIR /client
COPY client/package.json client/package-lock.json client/.npmrc ./
RUN npm ci --no-audit --no-fund
COPY client/ ./
RUN npm run build


FROM base AS runtime

COPY common /app/common
COPY server /app/server
# Giao diện web đã build (index.html và assets/); server phục vụ cùng origin với API, người chạy không cần cài Node.
COPY --from=client-build /client/dist /app/static
ENV JH_STATIC_DIR=/app/static
USER app
# DB nằm trong named volume gắn vào /data (WAL cần file -shm dùng chung, không bind mount từ ổ Windows).
VOLUME /data
EXPOSE 8000 9000
# Đúng một process uvicorn: hàng đợi và danh sách worker nằm trong bộ nhớ (không dùng --workers > 1).
# --no-server-header: không lộ "server: uvicorn". --timeout-keep-alive 5: đóng kết nối rảnh sau 5 giây (kết nối giữ mở
# không làm gì chiếm chỗ). --limit-concurrency 600: quá 600 kết nối/tác vụ đồng thời thì uvicorn trả 503 ngay thay vì
# nhận thêm (một process, một event loop); mỗi WebSocket là một kết nối nên con số này gồm trần WebSocket của ứng dụng
# (`ws_max_total` = 400) cộng khoảng 200 cho HTTP. Cổng worker (TCP, 9000) là socket riêng, không tính vào số này.
# --ws-max-size 4096: uvicorn đóng 1009 với frame WebSocket lớn hơn 4096 byte trước khi ứng dụng nhận (README 4.3).
# --ws-per-message-deflate false: không nén WebSocket (mỗi kết nối nén giữ một ngữ cảnh zlib hàng trăm KB).
# Còn thiếu (chờ thử với uvicorn 0.54.0 và phiên bản `websockets` ghim): `--ws`, cách tắt control-ping của uvicorn.
ENTRYPOINT ["tini", "--"]
CMD ["sh", "-c", "exec uvicorn server.main:app --host 0.0.0.0 --port ${JH_HTTP_PORT:-8000} --no-server-header --timeout-keep-alive 5 --limit-concurrency 600 --ws-max-size 4096 --ws-per-message-deflate false"]
