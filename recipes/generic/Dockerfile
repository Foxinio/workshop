FROM ubuntu:24.04

ARG DEBIAN_FRONTEND=noninteractive
ARG CODEX_VERSION=latest

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
        nodejs \
        npm \
    && rm -rf /var/lib/apt/lists/*

RUN npm install --global "@openai/codex@${CODEX_VERSION}" && \
    npm cache clean --force

RUN ln -s /usr/bin/fdfind /usr/local/bin/fd && \
    mkdir -p /home/codex/work

ENV HOME=/home/codex
ENV XDG_CACHE_HOME=/home/codex/.cache
ENV NPM_CONFIG_CACHE=/home/codex/.npm

WORKDIR /home/codex/work

ENTRYPOINT ["codex"]
