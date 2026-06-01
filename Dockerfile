FROM python:3.14.0-slim-trixie

ARG UV_VERSION=0.9.26

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y \
    curl \
    wget \
    ca-certificates \
    gnupg \
    lsb-release \
    build-essential \
    jq \
    unzip \
    zip \
    && rm -rf /var/lib/apt/lists/*

RUN set -eux; \
    arch="$(dpkg --print-architecture)"; \
    case "${arch}" in \
      amd64) uv_arch="x86_64-unknown-linux-gnu"; uv_sha256="30ccbf0a66dc8727a02b0e245c583ee970bdafecf3a443c1686e1b30ec4939e8" ;; \
      arm64) uv_arch="aarch64-unknown-linux-gnu"; uv_sha256="f71040c59798f79c44c08a7a1c1af7de95a8d334ea924b47b67ad6b9632be270" ;; \
      *) echo "unsupported architecture: ${arch}" >&2; exit 1 ;; \
    esac; \
    curl -fsSL "https://github.com/astral-sh/uv/releases/download/${UV_VERSION}/uv-${uv_arch}.tar.gz" -o /tmp/uv.tar.gz; \
    echo "${uv_sha256}  /tmp/uv.tar.gz" | sha256sum -c -; \
    tar -xzf /tmp/uv.tar.gz -C /tmp; \
    install -m 0755 -d /root/.local/bin; \
    install -m 0755 "/tmp/uv-${uv_arch}/uv" /root/.local/bin/uv; \
    install -m 0755 "/tmp/uv-${uv_arch}/uvx" /root/.local/bin/uvx; \
    rm -rf /tmp/uv.tar.gz "/tmp/uv-${uv_arch}"

ENV PATH=/root/.local/bin:/usr/local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

WORKDIR /workspace

RUN echo "=== Installed versions ===" \
    && python --version \
    && uv --version
