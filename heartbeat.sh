#!/bin/bash
# Every-minute "I'm alive" timestamp - but only when the clock is NTP-synced,
# so a boot with a wrong clock (no RTC) can never poison the power log.
[ "$(timedatectl show -p NTPSynchronized --value)" = yes ] && date +%s > /home/screenrpi/dashboard/heartbeat
