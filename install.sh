#!/bin/sh
set -eu
umask 077
migrate=no
case "${1-}" in
    '') ;;
    --migrate) migrate=yes ;;
    *) printf '%s\n' 'Usage: sh install.sh [--migrate]' >&2; exit 2 ;;
esac
[ "$#" -le 1 ] || exit 2
if [ "$(id -u)" -eq 0 ] || [ "$(id -un)" != pi ]; then
    printf '%s\n' 'Run as pi without sudo/root.' >&2
    exit 1
fi
source_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
target_dir=/home/pi/pyRPiMonitor
unit_dir=/home/pi/.config/systemd/user
unit_path=$unit_dir/pyrpimonitor.service
old_unit=$unit_dir/rpi-monitor.service
config_dir=/home/pi/.config/pyRPiMonitor
config_path=$config_dir/pyRPiMonitor.env
old_config=/home/pi/.config/rpi-monitor/rpi-monitor.env
/usr/bin/python3 -B -c 'import sys; import paho.mqtt; sys.exit(0 if sys.version_info >= (3, 11) and paho.mqtt.__version__ == "1.6.1" else 1)' || {
    printf '%s\n' 'Requires Python >=3.11 and existing paho-mqtt 1.6.1; no packages installed.' >&2
    exit 1
}
systemctl --user show-environment >/dev/null 2>&1 || {
    printf '%s\n' 'User systemd manager unavailable. Run installation in a login session as pi.' >&2
    exit 1
}
if [ "$migrate" = no ] && { [ -e "$old_unit" ] || [ -L "$old_unit" ]; }; then
    printf '%s\n' 'Old rpi-monitor unit exists. Use sh install.sh --migrate to avoid two publishers.' >&2
    exit 1
fi
# Linger starts pi user manager at boot and keeps it after logout.
# Never silently succeed with a login-dependent installation.
if [ "$(loginctl show-user pi --property=Linger --value)" != yes ]; then
    loginctl --no-ask-password enable-linger pi || {
        printf '%s\n' 'Cannot enable lingering under current policy. Ask the administrator to enable linger for pi, then rerun. No sudo was used.' >&2
        exit 1
    }
fi
[ "$(loginctl show-user pi --property=Linger --value)" = yes ] || {
    printf '%s\n' 'Linger is not enabled; installation stopped.' >&2
    exit 1
}
mkdir -p "$target_dir" "$unit_dir" "$config_dir"
chmod 700 "$config_dir"
backup_unit() {
    if [ -e "$1" ] || [ -L "$1" ]; then
        backup_path=$(mktemp "$1.backup.XXXXXXXX")
        cp -pL -- "$1" "$backup_path"
        printf '%s\n' "Unit backed up: $backup_path"
    fi
}
backup_unit "$unit_path"
if [ "$migrate" = yes ]; then
    backup_unit "$old_unit"
    if [ ! -e "$config_path" ] && [ ! -L "$config_path" ] && [ -f "$old_config" ]; then
        install -m 600 "$old_config" "$config_path"
        printf '%s\n' 'Private environment file copied; original preserved; no values displayed.'
    fi
fi
if [ "$source_dir" != "$target_dir" ]; then
    for file in py_rpi_monitor.py requirements.txt pyrpimonitor.service pyRPiMonitor.env.example install.sh uninstall.sh README.md; do
        install -m 600 "$source_dir/$file" "$target_dir/$file"
    done
fi
install -m 600 "$source_dir/pyrpimonitor.service" "$unit_path"
systemctl --user daemon-reload
systemctl --user enable pyrpimonitor.service
if [ "$migrate" = yes ] && { [ -e "$old_unit" ] || [ -L "$old_unit" ]; }; then
    systemctl --user disable --now rpi-monitor.service
    rm -- "$old_unit"
    systemctl --user daemon-reload
fi
printf '%s\n' 'pyRPiMonitor installed and enabled with Linger=yes; not started/restarted.'
printf '%s\n' 'Configure ~/.config/pyRPiMonitor/pyRPiMonitor.env if needed, then start pyrpimonitor.service.'
