#!/bin/sh
# Takes the dial-in walk's containers and network down.

set -eu

docker rm --force dial-laptop dial-server dial-desktop \
    >/dev/null 2>&1 || true
docker network rm dial-net >/dev/null 2>&1 || true
