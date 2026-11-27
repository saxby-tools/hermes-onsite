#!/bin/sh
# Starts the server of the dial-in walk: an SSH server for the
# computer to dial, the stub model, Hermes' gateway, its backend and
# a relay.
#
# Hermes only skips its sign-in gate for a backend bound to loopback.
# The relay lets the machine that runs Docker reach that port.
#
# Hermes drops to its own account, so everything it must write is
# handed to that account first.

set -eu

DATA=/opt/data
KEYS=/etc/dial
WRAP=/opt/hermes/docker/main-wrapper.sh

export PATH="/command:/package/admin/s6/command:${PATH}"

/opt/hermes/docker/stage2-hook.sh

mkdir -p "${DATA}/plugins" "${DATA}/logs" "${KEYS}"

# The keys each account accepts. Empty until the walk adds the
# computer's key, the way a person would.
: > "${KEYS}/hermes.keys"
: > "${KEYS}/guest.keys"

# The image locks the account's password, and sshd turns a locked
# account away even when its key is good.
usermod -p '*' hermes
usermod -p '*' guest

# Without this the server keeps a dead link's port for as long as its
# system takes to give up on the connection. The walk measures both.
if [ -n "${DIAL_CLIENT_ALIVE:-}" ]; then
    printf '%s\n' \
        "ClientAliveInterval ${DIAL_CLIENT_ALIVE}" \
        'ClientAliveCountMax 3' \
        > /etc/ssh/sshd_config.d/alive.conf
fi

ssh-keygen -A >/dev/null

/usr/sbin/sshd -E "${DATA}/logs/sshd.log"

# The key the server logs in to the computer with. It sits where the
# account's SSH setup looks, so the target needs no `key` setting.
install -d -o hermes -g hermes -m 0700 "${DATA}/.ssh"
install -o hermes -g hermes -m 0600 /dial-in/key "${DATA}/.ssh/id_ed25519"

install -o hermes -g hermes -m 0600 /dial-in/config.yaml "${DATA}/config.yaml"

chown hermes:hermes "${DATA}/plugins" "${DATA}/logs"

# onsite refuses a mount inside the Hermes home, and in this image
# the account's home is the Hermes home. So the person who runs the
# container makes a place, as the README says.
install -d -o hermes -g hermes /mnt/onsite

# The mount needs the device, and Hermes is not root.
chmod 0666 /dev/fuse

s6-setuidgid hermes python3 /rig/stub-model/server.py --port 11499 \
    > "${DATA}/logs/stub-model.log" 2>&1 &

socat TCP-LISTEN:9120,fork,reuseaddr,bind=0.0.0.0 TCP:127.0.0.1:9119 &

"${WRAP}" gateway run --quiet > "${DATA}/logs/dial-gateway.log" 2>&1 &

exec "${WRAP}" serve --host 127.0.0.1 --port 9119 --skip-build
