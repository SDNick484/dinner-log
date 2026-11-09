#!/bin/sh
# CI: let real OpenRC run services inside a container it didn't boot (the Alpine job's container).
#
# Production boots OpenRC as PID 1, so rc-service runs the generated /etc/init.d/dinner-log through
# openrc-run: it sources /etc/conf.d/dinner-log (only `export`ed settings reach the app), runs start_pre,
# and start-stop-daemon starts the command as command_user with the pidfile and logs. Using the real thing
# here, instead of a stub that imitates it, is the point of the job. Three settings make it work without
# OpenRC as init:
#   rc_sys="docker"              OpenRC skips what a container can't do (cgroups, clock, ...)
#   rc_provide="loopback net"    stands in for the networking service, so the script's `need net` is met
#   /run/openrc/softlevel        marks a runlevel as active; without it openrc-run refuses to start anything
# Nothing else is changed: the environment is still cleared for services (no rc_env_allow), as in production.
set -eu

apk add --no-cache openrc >/dev/null
sed -i 's/^#\{0,1\}rc_sys=.*/rc_sys="docker"/' /etc/rc.conf
grep -q '^rc_sys="docker"$' /etc/rc.conf || echo 'rc_sys="docker"' >> /etc/rc.conf
grep -q '^rc_provide=' /etc/rc.conf || echo 'rc_provide="loopback net"' >> /etc/rc.conf
mkdir -p /run/openrc
touch /run/openrc/softlevel
echo "OpenRC $(openrc --version 2>/dev/null | head -n 1) ready for services in this container"
