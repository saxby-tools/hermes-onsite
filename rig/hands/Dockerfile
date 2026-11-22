# The `hands` container plays the user's laptop in the test rig.
#
# It mirrors the three traits of a real workstation that the plugin
# has to cope with: key-only sshd, a zsh login shell, and a PATH that
# is only complete inside an interactive shell.

FROM debian:stable-slim

ARG HANDS_USER=dev
ARG HANDS_UID=1000

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        git \
        openssh-server \
        procps \
        ripgrep \
        zsh \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /run/sshd

RUN useradd \
        --create-home \
        --uid "${HANDS_UID}" \
        --shell /usr/bin/zsh \
        "${HANDS_USER}"

# Key-only logins, like a workstation hardened for tailnet SSH.
RUN printf '%s\n' \
        'PasswordAuthentication no' \
        'KbdInteractiveAuthentication no' \
        'PermitRootLogin no' \
        'PubkeyAuthentication yes' \
        'AuthorizedKeysFile /etc/hands/login.pub' \
        > /etc/ssh/sshd_config.d/hands.conf

COPY entrypoint.sh /usr/local/bin/hands-entrypoint
COPY seed-home.sh /usr/local/bin/hands-seed-home

RUN chmod 0755 \
        /usr/local/bin/hands-entrypoint \
        /usr/local/bin/hands-seed-home

ENV HANDS_USER=${HANDS_USER}

EXPOSE 22

ENTRYPOINT ["/usr/local/bin/hands-entrypoint"]
