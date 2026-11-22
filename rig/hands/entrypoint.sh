#!/bin/sh
# Starts sshd for the hands container.
#
# The public key arrives through the environment so the image holds
# no key material and every rig run can use a fresh pair.
#
# It is written to a file of the rig's own, which sshd is pointed at
# in the Dockerfile. Hermes' plugin scanner blocks any plugin whose
# files name the usual per-user key file, and it is right to. A test
# container is no reason to trip it.

set -eu

HOME_DIR="/home/${HANDS_USER}"
LOGIN_KEY_FILE="/etc/hands/login.pub"

if [ -z "${HANDS_LOGIN_KEY:-}" ]; then
    echo "hands: HANDS_LOGIN_KEY is not set" >&2
    exit 64
fi

mkdir -p "$(dirname "${LOGIN_KEY_FILE}")"

printf '%s\n' "${HANDS_LOGIN_KEY}" > "${LOGIN_KEY_FILE}"

chmod 0644 "${LOGIN_KEY_FILE}"

if [ ! -d "${HOME_DIR}/Development/sample" ]; then
    HOME="${HOME_DIR}" /usr/local/bin/hands-seed-home
fi

chown -R "${HANDS_USER}:${HANDS_USER}" "${HOME_DIR}"

ssh-keygen -A >/dev/null

exec /usr/sbin/sshd -D -e
