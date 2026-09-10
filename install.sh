#!/bin/sh
set -eu
umask 077
if [ "$(id -u)" -eq 0 ] || [ "$(id -un)" != pi ]; then
    printf '%s\n' 'Run as pi without sudo/root.' >&2
    exit 1
fi
source_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
target_dir=/home/pi/rpi-monitor
unit_dir=/home/pi/.config/systemd/user
unit_path=$unit_dir/rpi-monitor.service
config_dir=/home/pi/.config/rpi-monitor
# Read-only prerequisites, before changing any files. Never install dependencies.
/usr/bin/python3 -c 'import sys; import paho.mqtt; sys.exit(0 if sys.version_info >= (3, 11) and paho.mqtt.__version__ == "1.6.1" else 1)' || {
    printf '%s\n' 'Requires Python >=3.11 and existing paho-mqtt 1.6.1; nothing installed.' >&2
    exit 1
}
systemctl --user show-environment >/dev/null 2>&1 || {
    printf '%s\n' 'User systemd manager unavailable. Run in a login session as pi.' >&2
    exit 1
}
mkdir -p "$target_dir" "$unit_dir" "$config_dir"
chmod 700 "$config_dir"
if [ -e "$unit_path" ] || [ -L "$unit_path" ]; then
    backup_path=$(mktemp "$unit_dir/rpi-monitor.service.backup.XXXXXXXX")
    cp -pL -- "$unit_path" "$backup_path"
    printf '%s\n' "Previous unit backed up: $backup_path"
fi
if [ "$source_dir" != "$target_dir" ]; then
    for file in rpi_monitor.py requirements.txt rpi-monitor.service rpi-monitor.env.example install.sh uninstall.sh README.md; do
        install -m 600 "$source_dir/$file" "$target_dir/$file"
    done
fi
install -m 600 "$source_dir/rpi-monitor.service" "$unit_path"
# Do not create, read or overwrite the private credential file.
systemctl --user daemon-reload
systemctl --user enable rpi-monitor.service
printf '%s\n' 'Installed and enabled the user unit; not started or restarted.'
printf '%s\n' 'Configure the private EnvironmentFile as described in README.md, then start explicitly.'
