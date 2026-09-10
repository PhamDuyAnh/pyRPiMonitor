#!/bin/sh
set -eu
if [ "$(id -u)" -eq 0 ] || [ "$(id -un)" != pi ]; then
    printf '%s\n' 'Run as pi without sudo/root.' >&2
    exit 1
fi
unit_path=/home/pi/.config/systemd/user/rpi-monitor.service
if [ -e "$unit_path" ] || [ -L "$unit_path" ]; then
    systemctl --user disable --now rpi-monitor.service
    rm -- "$unit_path"
    systemctl --user daemon-reload
fi
printf '%s\n' 'Removed only the RPiMonitor user unit. Credentials, source, backups and user data preserved.'
