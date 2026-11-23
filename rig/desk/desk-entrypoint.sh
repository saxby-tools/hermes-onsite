#!/bin/sh
# Starts the desktop as the hands user, then hands over to the hands
# container's own entrypoint, which starts sshd.
#
# The desktop waits for the home folder, which that entrypoint seeds.

set -eu

HOME_DIR="/home/${HANDS_USER}"
SEEDED_DIR="${HOME_DIR}/Development/sample"
WAIT_STEPS=100

wait_for_home() {
    step=0

    while [ ! -d "${SEEDED_DIR}" ]; do
        step=$((step + 1))

        if [ "${step}" -gt "${WAIT_STEPS}" ]; then
            echo "desk: the home folder was never seeded" >&2
            return 1
        fi

        sleep 0.1
    done
}

start_desktop() {
    wait_for_home

    exec su "${HANDS_USER}" -s /bin/sh -c /usr/local/bin/desk-start
}

start_desktop &

exec /usr/local/bin/hands-entrypoint
