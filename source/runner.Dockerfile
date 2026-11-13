FROM python:3.11-slim-trixie
RUN apt-get update && apt-get install -y --no-install-recommends g++ openjdk-21-jdk-headless \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd -g 1000 judge && useradd -u 1000 -g 1000 -M judge \
    && mkdir /work && chown 1000:1000 /work
ENV PYTHONDONTWRITEBYTECODE=1
WORKDIR /work
USER 1000:1000
CMD ["true"]
