#!/bin/sh
# One X display, a window manager, a terminal and a VNC server.
#
# Everything started here carries DISPLAY and the session bus in its
# environment. That is how the plugin finds a desktop on a real
# machine, so it is how it must find this one.

set -eu

export DISPLAY=":99"

SCREEN_SIZE="1280x800x24"
TERMINAL_PLACE="100x30+40+40"
WAIT_STEPS=100

cd "${HOME}"

# No access list: the only clients are this container's own user.
Xvfb "${DISPLAY}" -screen 0 "${SCREEN_SIZE}" -ac -nolisten tcp &

step=0

while ! xdpyinfo >/dev/null 2>&1; do
    step=$((step + 1))

    if [ "${step}" -gt "${WAIT_STEPS}" ]; then
        echo "desk: the display never came up" >&2
        exit 1
    fi

    sleep 0.1
done

# The terminal waits for the window manager. A window that is on
# screen first is never given the keyboard, and the first click on it
# does nothing, which no real desktop does to a person.
#
# The VNC server stays in front so the session lives as long as it
# does. Loopback only and no password: it is reached through SSH.
exec dbus-run-session -- sh -c '
    openbox &

    step=0

    while ! xprop -root _NET_SUPPORTING_WM_CHECK 2>/dev/null \
        | grep -q "window id"; do
        step=$((step + 1))

        if [ "${step}" -gt "$1" ]; then
            echo "desk: the window manager never came up" >&2
            exit 1
        fi

        sleep 0.1
    done

    xterm -geometry "$0" -title desk-terminal &
    exec x11vnc -display "${DISPLAY}" -localhost -nopw -forever \
        -shared -quiet
' "${TERMINAL_PLACE}" "${WAIT_STEPS}"
