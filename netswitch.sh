#!/bin/bash
# Manual WiFi switcher: hostel R-VIT <-> iPhone hotspot.
# The hotspot is NEVER joined automatically (profile autoconnect is off) - only
# through this script, via the dashboard WiFi row or pibot /net. The autoreturn
# cron (just after curfew ends) sends us back to R-VIT so mobile data doesn't
# keep burning overnight.
RVIT="R-VIT"
HOTSPOT="iphone-hotspot"
LOG="$HOME/netswitch.log"

say(){ echo "$(date '+%F %T') $*" >> "$LOG"; }

current(){ nmcli -t -f NAME,DEVICE con show --active | awk -F: '$2=="wlan0"{print $1}'; }

online(){
  [ "$(curl -s -o /dev/null -w '%{http_code}' -m 8 http://connectivitycheck.gstatic.com/generate_204)" = "204" ]
}

case "$1" in
  status)
    c=$(current)
    [ "$c" = "$HOTSPOT" ] && net=hotspot || net=rvit
    if online; then ok=true; else ok=false; fi
    echo "{\"net\":\"$net\",\"conn\":\"$c\",\"online\":$ok}"
    ;;
  hotspot)
    [ "$(current)" = "$HOTSPOT" ] && { say "already on hotspot"; exit 0; }
    say "switching to hotspot (manual)"
    sudo nmcli device wifi rescan 2>/dev/null; sleep 2
    if sudo nmcli -w 25 con up "$HOTSPOT" >/dev/null 2>&1; then
      say "on hotspot"
    else
      say "hotspot join FAILED (is the phone's Personal Hotspot screen open?) - back to R-VIT"
      sudo nmcli -w 25 con up "$RVIT" >/dev/null 2>&1
      exit 1
    fi
    ;;
  rvit)
    [ "$(current)" = "$RVIT" ] && { say "already on R-VIT"; exit 0; }
    say "switching to R-VIT"
    sudo nmcli -w 25 con up "$RVIT" >/dev/null 2>&1 || { say "R-VIT join failed"; exit 1; }
    say "on R-VIT (watchdog will re-auth the portal)"
    ;;
  autoreturn)
    # cron, just after curfew: if still on hotspot, go back to R-VIT; if R-VIT
    # has no internet even after the watchdog gets its shot, fall back.
    [ "$(current)" = "$HOTSPOT" ] || exit 0
    say "autoreturn: leaving hotspot"
    sudo nmcli -w 25 con up "$RVIT" >/dev/null 2>&1
    for i in $(seq 1 18); do
      sleep 10
      online && { say "autoreturn: R-VIT online"; exit 0; }
    done
    say "autoreturn: R-VIT still dead after 3 min - back to hotspot"
    sudo nmcli -w 25 con up "$HOTSPOT" >/dev/null 2>&1
    ;;
  *)
    echo "usage: netswitch.sh status|hotspot|rvit|autoreturn"; exit 2
    ;;
esac
