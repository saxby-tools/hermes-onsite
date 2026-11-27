# The second computer of the dial-in walk: one with a real service
# manager, so the service file the plugin prints can be run as it is.
#
# The stand-in laptop has no service manager and uses a loop. This
# one starts `systemd` as its first process and gives the `dev`
# account a session of its own, which is where a user service runs.
#
# Its SSH server answers on its own loopback address only, like the
# laptop's. The server cannot reach it.

FROM debian:stable-slim

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        dbus \
        dbus-user-session \
        libpam-systemd \
        openssh-client \
        openssh-server \
        procps \
        systemd \
        systemd-sysv \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --uid 1000 --shell /bin/bash dev

# Key-only logins, from the computer itself and nowhere else. The
# keys an account accepts are kept in a file of the walk's own.
RUN printf '%s\n' \
        'ListenAddress 127.0.0.1' \
        'PasswordAuthentication no' \
        'KbdInteractiveAuthentication no' \
        'AuthorizedKeysFile /etc/dial/%u.keys' \
        > /etc/ssh/sshd_config.d/dial.conf

CMD ["/sbin/init"]
