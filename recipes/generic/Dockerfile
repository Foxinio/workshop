FROM ubuntu:24.04

ARG DEBIAN_FRONTEND=noninteractive
ARG CODEX_VERSION=latest
ARG WORKSHOP_HOME=/home/codex

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

RUN npm install --global "@openai/codex@${CODEX_VERSION}" @anthropic-ai/claude-code opencode-ai && \
    npm cache clean --force

RUN ln -s /usr/bin/fdfind /usr/local/bin/fd && \
    mkdir -p "${WORKSHOP_HOME}/work"

ENV HOME=${WORKSHOP_HOME}
ENV XDG_CACHE_HOME=${WORKSHOP_HOME}/.cache
ENV NPM_CONFIG_CACHE=${WORKSHOP_HOME}/.npm

WORKDIR ${WORKSHOP_HOME}/work

ENTRYPOINT ["codex"]
