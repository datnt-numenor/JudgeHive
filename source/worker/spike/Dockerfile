# Spike executor: môi trường đo. Ghim theo bản Debian (không dùng tag trôi python:3.11-slim).
FROM python:3.11-slim-trixie

RUN apt-get update \
    && apt-get install -y --no-install-recommends g++ openjdk-21-jdk-headless \
    && rm -rf /var/lib/apt/lists/* \
    && command -v setpriv prlimit setsid sleep >/dev/null

WORKDIR /spike
COPY *.py /spike/

# Chạy bằng root để hạ quyền con xuống uid thường (root bỏ qua RLIMIT_NPROC); init: true trong compose để reap zombie.
ENTRYPOINT ["python", "-u", "spike.py"]
