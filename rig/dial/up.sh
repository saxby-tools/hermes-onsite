#!/bin/sh
# Brings up the two containers of the dial-in walk.
#
#   dial-server   Hermes with the plugin, and an SSH server
#   dial-laptop   a stand-in computer whose SSH server answers on its
#                 own loopback address only, so the server cannot
#                 reach it
#
# Nothing here opens the link. `walk.sh` does that from the laptop's
# side, with the command the plugin prints.
#
# Set DIAL_CLIENT_ALIVE to a number of seconds to give the server's
# SSH server a keepalive. Left unset, it has none, as most do.

set -eu

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "${HERE}/../.." && pwd)"
STATE="${REPO}/.rig/dial"
NET=dial-net
# A subnet of the walk's own, so the walk can bring the laptop back
# at an address it picks.
SUBNET=10.213.77.0/24
PORT="${DIAL_PORT:-9392}"

mkdir -p "${STATE}"

if [ ! -f "${STATE}/key" ]; then
    ssh-keygen -q -t ed25519 -N '' -C dial-server -f "${STATE}/key"
fi

if [ ! -f "${STATE}/token" ]; then
    (
        umask 077
        python3 -c 'import secrets; print(secrets.token_urlsafe(32))' \
            > "${STATE}/token"
    )
fi

docker network inspect "${NET}" >/dev/null 2>&1 \
    || docker network create --subnet "${SUBNET}" "${NET}" >/dev/null

# The stand-in computer is the rig's own. It is built once.
docker image inspect onsite-rig/hands:dev >/dev/null 2>&1 \
    || docker build --quiet --tag onsite-rig/hands:dev "${REPO}/rig/hands" \
        >/dev/null

docker build --quiet --tag onsite-rig/dial-server:dev "${HERE}" \
    >/dev/null

docker rm --force dial-laptop dial-server >/dev/null 2>&1 || true

docker run --detach --name dial-laptop \
    --hostname laptop \
    --network "${NET}" --network-alias laptop \
    --env "HANDS_LOGIN_KEY=$(cat "${STATE}/key.pub")" \
    --volume "${HERE}/loopback.conf:/etc/ssh/sshd_config.d/loopback.conf:ro" \
    onsite-rig/hands:dev >/dev/null

# Hermes' image lets its file tools write under /opt/data alone, and
# checks the path on the server before it is sent to a target. So
# the laptop's folders are added, as the refusal itself says to do.
docker run --detach --init --name dial-server \
    --hostname server \
    --network "${NET}" --network-alias server \
    --publish "127.0.0.1:${PORT}:9120" \
    --device /dev/fuse --cap-add SYS_ADMIN --cap-add NET_ADMIN \
    --security-opt apparmor=unconfined \
    --env "HERMES_DASHBOARD_SESSION_TOKEN=$(cat "${STATE}/token")" \
    --env "DIAL_CLIENT_ALIVE=${DIAL_CLIENT_ALIVE:-}" \
    --env "HERMES_WRITE_SAFE_ROOT=/opt/data:/home/dev:/srv/dial" \
    --volume "${REPO}/onsite:/opt/data/plugins/onsite:ro" \
    --volume "${REPO}/rig:/rig:ro" \
    --volume "${STATE}/key:/dial-in/key:ro" \
    --volume "${HERE}/config.yaml:/dial-in/config.yaml:ro" \
    onsite-rig/dial-server:dev >/dev/null

echo "backend: http://127.0.0.1:${PORT}"
echo "token:   ${STATE}/token"
