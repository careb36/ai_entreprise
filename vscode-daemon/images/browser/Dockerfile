# syntax=docker/dockerfile:1
#
# The browser sandbox image (issue #315): what the application check runs QA's
# Playwright smoke tests in, when DELIVERY_BROWSER_IMAGE names it. Node, the
# Playwright of package-lock.json and the one browser build that Playwright
# launches headless (Chrome Headless Shell), each download checked, for amd64
# and arm64. Nothing is downloaded at run time: the container runs with the
# network off and reaches only the application under test, inside it.
#
#   docker build -t fabrique-browser-sandbox vscode-daemon/images/browser
#   docker buildx build --platform linux/amd64,linux/arm64 vscode-daemon/images/browser
#
# Portions adapted from AixleHQ/flow, docker/base/Dockerfile at commit f9efa22
# (https://github.com/AixleHQ/flow), Copyright 2026 Dualboot Partners, LLC,
# licensed under the Apache License, Version 2.0
# (http://www.apache.org/licenses/LICENSE-2.0): the per-architecture download
# checked against a pinned sha256 (`TARGETARCH`, `case`, `sha256sum -c`), the
# Playwright version pinned so the browser baked at build time is the one
# launched at run time, the browsers in a shared PLAYWRIGHT_BROWSERS_PATH
# readable by a non-root user, and the build-time probe (probe-browser.js) run
# as root and as the unprivileged user on amd64 only. Changed here: Flow keeps
# the chrome-for-testing channel its MCP launches and deletes the headless
# shell; a Playwright test run headless launches the headless shell, so this
# image keeps only that, downloaded directly from the Playwright CDN and
# checked, rather than through `playwright install`, which verifies nothing.

# node:22-bookworm-slim (Node 22.23.5), the multi-architecture index, by digest.
ARG NODE_IMAGE=node:22-bookworm-slim@sha256:e9f565d32859f0cbfb52125eafe8be39390f24dac302dc3cdf566e8ba3ab21ee
FROM ${NODE_IMAGE}

# Keep in step with package.json / package-lock.json: playwright-core's
# browsers.json names the revision and the Chrome version of each release.
ARG PLAYWRIGHT_VERSION=1.64.1
ARG HEADLESS_SHELL_REVISION=1248
ARG HEADLESS_SHELL_VERSION=156.0.8078.4
ARG HEADLESS_SHELL_SHA256_AMD64=c3e50596aef52c58f8fda3c6b3411c33d34c88b177b3ed1c2243359fd3b8ba20
ARG HEADLESS_SHELL_SHA256_ARM64=81ab9d4d3c301dc5fdfb6c541b9e6632b16fc819a6a854460f40a76fe2b2c476
ARG TARGETARCH

ENV PLAYWRIGHT_BROWSERS_PATH=/opt/playwright-browsers \
    PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 \
    FABRIQUE_PLAYWRIGHT_DIR=/opt/fabrique/playwright/node_modules \
    NODE_PATH=/opt/fabrique/playwright/node_modules \
    LANG=C.UTF-8 \
    CI=true

# The headless shell's runtime libraries (its deb.deps), python3 for a Python
# application under test, curl for the daemon's probe of the port, unzip for
# the browser archive. Each apt layer removes its own lists.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl unzip python3 \
        fonts-liberation fonts-noto-color-emoji \
        libasound2 libatk-bridge2.0-0 libatk1.0-0 libatspi2.0-0 libcairo2 \
        libcups2 libdbus-1-3 libexpat1 libgbm1 libglib2.0-0 libgtk-3-0 \
        libnspr4 libnss3 libpango-1.0-0 libudev1 libvulkan1 libx11-6 libxcb1 \
        libxcomposite1 libxdamage1 libxext6 libxfixes3 libxkbcommon0 libxrandr2 \
    && rm -rf /var/lib/apt/lists/*

# Behind a proxy that intercepts TLS, pass its CA bundle as the optional build
# secret `extra_ca` (`--secret id=extra_ca,src=bundle.pem`): it is used for the
# two downloads below and kept out of the image. It changes nothing about what
# is accepted: the lockfile's integrity hashes and the pinned sha256 decide.
#
# Playwright from the lockfile: npm ci checks each tarball against its
# integrity hash and fails on a mismatch; no install script runs.
COPY package.json package-lock.json /opt/fabrique/playwright/
RUN --mount=type=secret,id=extra_ca,required=false \
    cd /opt/fabrique/playwright \
    && if [ -s /run/secrets/extra_ca ]; then export NODE_EXTRA_CA_CERTS=/run/secrets/extra_ca; fi \
    && npm ci --omit=dev --ignore-scripts --no-audit --no-fund \
    && test "$(node -p "require('playwright-core/package.json').version")" = "${PLAYWRIGHT_VERSION}" \
    && ln -s /opt/fabrique/playwright/node_modules/.bin/playwright /usr/local/bin/playwright \
    && npm cache clean --force && rm -rf /root/.npm

# The headless shell for this architecture, checked against its pinned sha256,
# unpacked where Playwright looks for it (chromium_headless_shell-<revision>).
RUN --mount=type=secret,id=extra_ca,required=false \
    set -eu; \
    cacert=""; if [ -s /run/secrets/extra_ca ]; then cacert=/run/secrets/extra_ca; fi; \
    case "${TARGETARCH:-$(dpkg --print-architecture)}" in \
        amd64) dir=linux64;    zip=chrome-headless-shell-linux64.zip;     sum="${HEADLESS_SHELL_SHA256_AMD64}" ;; \
        arm64) dir=linux-arm64; zip=chrome-headless-shell-linux-arm64.zip; sum="${HEADLESS_SHELL_SHA256_ARM64}" ;; \
        *) echo "unsupported architecture: ${TARGETARCH:-unknown}" >&2; exit 1 ;; \
    esac; \
    curl -fsSL ${cacert:+--cacert "$cacert"} -o "/tmp/${zip}" \
        "https://cdn.playwright.dev/builds/cft/${HEADLESS_SHELL_VERSION}/${dir}/${zip}"; \
    echo "${sum}  /tmp/${zip}" | sha256sum -c -; \
    target="${PLAYWRIGHT_BROWSERS_PATH}/chromium_headless_shell-${HEADLESS_SHELL_REVISION}"; \
    mkdir -p "${target}"; \
    unzip -q "/tmp/${zip}" -d "${target}"; \
    rm -f "/tmp/${zip}"; \
    test -x "${target}"/chrome-headless-shell-*/chrome-headless-shell; \
    touch "${target}/INSTALLATION_COMPLETE" "${target}/DEPENDENCIES_VALIDATED"; \
    chmod -R a+rX "${PLAYWRIGHT_BROWSERS_PATH}"; \
    # Playwright may create a lock folder there at launch: writable by any uid
    # (the daemon runs the container as the person's own), sticky.
    chmod 1777 "${PLAYWRIGHT_BROWSERS_PATH}"

# The probe the daemon also runs before a smoke test (vscode-daemon/browser_probe.py),
# run here as root and as the unprivileged user: a Playwright bump whose browser
# does not launch fails the build, not a delivery. amd64 only, as in Flow: under
# QEMU (an arm64 build on an amd64 runner) the browser aborts; both builds pin
# the same Playwright and revision.
COPY probe-browser.js /opt/fabrique/probe-browser.js
RUN set -eu; \
    case "${TARGETARCH:-$(dpkg --print-architecture)}" in \
        amd64) \
            cd /tmp && node /opt/fabrique/probe-browser.js; \
            su node -s /bin/sh -c "cd /tmp && HOME=/tmp node /opt/fabrique/probe-browser.js" ;; \
        *) echo "probe-browser: skipped on ${TARGETARCH:-$(dpkg --print-architecture)} (the browser aborts under QEMU; the amd64 build runs it)" ;; \
    esac; \
    rm -rf /tmp/* /root/.cache

# Non-root by default; the daemon runs it with --user <the person's uid>.
USER node
WORKDIR /workspace
