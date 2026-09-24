#!/bin/sh
# Do not probe with connect(): it would disrupt the single-peer demo protocol.
set -u

if [ ! -r /proc/net/unix ]; then
    echo 'Cannot inspect active sockets; leaving socket files in place.' >&2
    exit 1
fi

result=0
for endpoint in "$@"; do
    if [ -L "$endpoint" ]; then
        echo "Refusing to remove symlink: $endpoint" >&2
        result=1
        continue
    fi
    if [ ! -e "$endpoint" ]; then
        echo "Already absent: $endpoint"
        continue
    fi
    if [ ! -S "$endpoint" ]; then
        echo "Refusing to remove non-socket: $endpoint" >&2
        result=1
        continue
    fi
    if CLEAN_SOCKET_ENDPOINT="$endpoint" awk '
        NR > 1 {
            # Preserve spaces in paths after the seven metadata fields.
            for (i = 0; i < 7; ++i) sub(/^[^[:space:]]+[[:space:]]+/, "")
            if ($0 == ENVIRON["CLEAN_SOCKET_ENDPOINT"]) found = 1
        }
        END { exit found ? 0 : 1 }
    ' /proc/net/unix; then
        echo "Socket is still in use; stop both demos first: $endpoint" >&2
        result=1
    else
        status=$?
        if [ "$status" -ne 1 ]; then
            echo "Cannot check socket ownership; leaving in place: $endpoint" >&2
            result=1
        elif rm -- "$endpoint"; then
            echo "Removed stale socket: $endpoint"
        else
            result=1
        fi
    fi
done
exit "$result"
