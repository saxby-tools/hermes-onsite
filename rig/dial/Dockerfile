# The server in the dial-in walk: the official Hermes image, with the
# two things a server needs for a computer that dials in to it.
#
# - `sshfs`, to mount the computer's disk. See the README.
# - An SSH server of its own. The computer logs in to it and offers
#   its own SSH server back through that link.
#
# `socat` and `iptables` are for the walk alone. The first relays
# the backend's port to the machine that runs Docker, so the real
# desktop app can be pointed at it. The second lets the walk make
# packets to the laptop's old address vanish, as they do when a
# real laptop moves to another network.
#
# A second account, `guest`, plays another person on the same server.
# The walk uses it to listen on the computer's port and checks that
# the plugin does not take it for the computer.

FROM nousresearch/hermes-agent:v0.23.2

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        fuse3 \
        iptables \
        openssh-client \
        openssh-server \
        socat \
        sshfs \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /run/sshd

RUN useradd --create-home --shell /bin/sh guest

# Key-only logins. The keys the server accepts are kept in a file of
# the walk's own, so no file here names the usual per-user one.
RUN printf '%s\n' \
        'PasswordAuthentication no' \
        'KbdInteractiveAuthentication no' \
        'PermitRootLogin no' \
        'PubkeyAuthentication yes' \
        'AuthorizedKeysFile /etc/dial/%u.keys' \
        > /etc/ssh/sshd_config.d/dial.conf

COPY start.sh /usr/local/bin/dial-start

RUN chmod 0755 /usr/local/bin/dial-start

ENTRYPOINT ["/usr/local/bin/dial-start"]
