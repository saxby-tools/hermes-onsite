#!/bin/sh
# Starts sshd for the hands container.
#
# The public key arrives through the environment so the image holds
# no key material and every rig run can use a fresh pair.

set -eu

HOME_DIR="/home/${HANDS_USER}"
SSH_DIR="${HOME_DIR}/.ssh"

if [ -z "${HANDS_AUTHORIZED_KEY:-}" ]; then
    echo "hands: HANDS_AUTHORIZED_KEY is not set" >&2
    exit 64
fi

mkdir -p "${SSH_DIR}"

printf '%s\n' "${HANDS_AUTHORIZED_KEY}" > "${SSH_DIR}/authorized_keys"

chmod 0700 "${SSH_DIR}"
chmod 0600 "${SSH_DIR}/authorized_keys"

if [ ! -d "${HOME_DIR}/Development/sample" ]; then
    HOME="${HOME_DIR}" /usr/local/bin/hands-seed-home
fi

chown -R "${HANDS_USER}:${HANDS_USER}" "${HOME_DIR}"

ssh-keygen -A >/dev/null

exec /usr/sbin/sshd -D -e
