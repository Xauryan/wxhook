#!/bin/sh
set -eu
umask 077
cd /opt/jqr-task-center/current/deploy
mkdir -p /opt/jqr-task-center/backups
output="/opt/jqr-task-center/backups/task-center-$(date -u +%Y%m%dT%H%M%SZ).dump"
docker compose --env-file /opt/jqr-task-center/runtime.env exec -T db pg_dump -U task_center -d task_center -Fc > "$output.part"
docker compose --env-file /opt/jqr-task-center/runtime.env exec -T db pg_restore --list < "$output.part" > /dev/null
mv "$output.part" "$output"
printf '%s\n' "$output"
