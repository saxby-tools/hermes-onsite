# The `desk` container is the hands container with a desktop.
#
# Screen control needs a screen. A test must not drive a real one, so
# this image gives the rig a small X display that nobody looks at,
# with a window manager, a terminal to type into, and the two things
# the plugin can reach a screen through:
#
# - Hermes' own screen driver, at the version and checksum Hermes
#   itself pins in pm/lock.json,
# - a VNC server bound to loopback, as a machine's built-in screen
#   sharing would be.

ARG BASE_IMAGE=onsite-rig/hands:dev

FROM ${BASE_IMAGE}

ARG TARGETARCH

ARG DRIVER_VERSION=0.21.0
ARG DRIVER_SHA256_ARM64=6b3a308c6741dd8291aad22a308bd2ec8eb52eab64687be007ac86ceaf7e3307
ARG DRIVER_SHA256_AMD64=cf29bfaad16737e31e72f38dad2a6424f24b21a4ac2edea885e83cb9d5034135
ARG DRIVER_RELEASES=https://github.com/trycua/cua/releases/download

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        at-spi2-core \
        curl \
        dbus-x11 \
        openbox \
        x11-utils \
        x11vnc \
        xauth \
        xdotool \
        xterm \
        xvfb \
    && rm -rf /var/lib/apt/lists/*

# The driver is checked against the checksum before it is installed.
# A build for an unlisted machine type stops here.
RUN set -eu; \
    case "${TARGETARCH}" in \
        arm64) arch=arm64; sum="${DRIVER_SHA256_ARM64}" ;; \
        amd64) arch=x86_64; sum="${DRIVER_SHA256_AMD64}" ;; \
        *) echo "desk: no driver for ${TARGETARCH}" >&2; exit 1 ;; \
    esac; \
    name="cua-driver-rs-${DRIVER_VERSION}-linux-${arch}-binary.tar.gz"; \
    work="$(mktemp -d)"; \
    curl -fsSL \
        -o "${work}/driver.tar.gz" \
        "${DRIVER_RELEASES}/cua-driver-rs-v${DRIVER_VERSION}/${name}"; \
    echo "${sum}  ${work}/driver.tar.gz" | sha256sum -c -; \
    tar -xzf "${work}/driver.tar.gz" -C "${work}"; \
    install -m 0755 \
        "$(find "${work}" -type f -name cua-driver | head -n 1)" \
        /usr/local/bin/cua-driver; \
    find "${work}" -delete

COPY desk-entrypoint.sh /usr/local/bin/desk-entrypoint
COPY desk-start.sh /usr/local/bin/desk-start

RUN chmod 0755 \
        /usr/local/bin/desk-entrypoint \
        /usr/local/bin/desk-start

EXPOSE 22

ENTRYPOINT ["/usr/local/bin/desk-entrypoint"]
