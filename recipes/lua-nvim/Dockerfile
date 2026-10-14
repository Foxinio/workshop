FROM ubuntu:24.04

ARG DEBIAN_FRONTEND=noninteractive
ARG CODEX_VERSION=latest
ARG WORKSHOP_TOOLS=both
ARG STYLUA_VERSION=2.3.1
ARG NVIM_VERSION=v0.11.5

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
        ripgrep \
        fd-find \
        python3 \
        python3-pip \
        nodejs \
        npm \
        build-essential \
        cmake \
        ninja-build \
        gettext \
        lua5.1 \
        luajit \
        libluajit-5.1-dev \
        luarocks \
        vim-tiny \
        wget \
        make \
        gcc \
        g++ \
        libc6-dev \
        tar \
        luacheck \
        python3 \
        python3-pip \
    && rm -rf /var/lib/apt/lists/*

RUN set -eu; \
    case "$WORKSHOP_TOOLS" in codex|claude|opencode|both|all) ;; *) exit 1 ;; esac; \
    case "$WORKSHOP_TOOLS" in codex|both|all) npm install --global "@openai/codex@${CODEX_VERSION}" ;; esac; \
    case "$WORKSHOP_TOOLS" in claude|both|all) npm install --global @anthropic-ai/claude-code ;; esac; \
    case "$WORKSHOP_TOOLS" in opencode|all) npm install --global opencode-ai ;; esac; \
    npm cache clean --force

# Build a pinned release for the container architecture.
RUN git clone --depth 1 --branch v0.11.5 https://github.com/neovim/neovim.git /tmp/neovim && \
    make -C /tmp/neovim CMAKE_BUILD_TYPE=Release CMAKE_INSTALL_PREFIX=/usr/local && \
    make -C /tmp/neovim install && \
    rm -rf /tmp/neovim && \
    nvim --headless -u NONE -i NONE -n '+lua assert(vim.fn.has("nvim-0.11") == 1); assert(jit)' +qa && \
    lua5.1 -v && luajit -v && luarocks --version

RUN ln -s /usr/bin/fdfind /usr/local/bin/fd && \
    mkdir -p /home/codex/work

ENV HOME=/home/codex
ENV XDG_CACHE_HOME=/home/codex/.cache
ENV NPM_CONFIG_CACHE=/home/codex/.npm

WORKDIR /home/codex/work

ENTRYPOINT ["codex"]
