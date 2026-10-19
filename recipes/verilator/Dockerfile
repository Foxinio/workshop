FROM ubuntu:24.04

ARG DEBIAN_FRONTEND=noninteractive
ARG CODEX_VERSION=latest

# Build and contributor prerequisites: https://verilator.org/guide/latest/install.html
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        bash \
        ca-certificates \
        curl \
        git \
        jq \
        unzip \
        zip \
        xz-utils \
        file \
        less \
        nano \
        vim-tiny \
        ripgrep \
        fd-find \
        python3 \
        python3-pip \
        python3-venv \
        python3-clang \
        nodejs \
        npm \
        build-essential \
        autoconf \
        flex \
        bison \
        help2man \
        perl \
        perl-doc \
        ccache \
        mold \
        libgoogle-perftools-dev \
        libjemalloc-dev \
        numactl \
        libfl2 \
        libfl-dev \
        zlib1g \
        zlib1g-dev \
        liblz4-1 \
        liblz4-dev \
        libsystemc \
        libsystemc-dev \
        z3 \
        clang \
        clang-format-18 \
        cmake \
        gdb \
        binutils \
        graphviz \
        lcov \
        bear \
    && rm -rf /var/lib/apt/lists/*

RUN npm install --global "@openai/codex@${CODEX_VERSION}" @anthropic-ai/claude-code opencode-ai && \
    npm cache clean --force

RUN ln -s /usr/bin/fdfind /usr/local/bin/fd && \
    mkdir -p /home/codex/work

ENV HOME=/home/codex
ENV XDG_CACHE_HOME=/home/codex/.cache
ENV NPM_CONFIG_CACHE=/home/codex/.npm

WORKDIR /home/codex/work

ENTRYPOINT ["codex"]
