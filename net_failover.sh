#!/bin/bash
# net_failover.sh - auto-join the iPhone hotspot ONLY after R-VIT has been
# unreachable for FAILOVER_OFFLINE_SECS (default 15 min), and auto-return to
# R-VIT once it genuinely looks recovered. Manual hotspot switches (dashboard
# popover / pibot /net) are never auto-reverted: we only return when the
# .net_failover_state file says WE made the switch.
#
# Runs as the net-failover systemd service. Log: ~/net_failover.log

LOG="$HOME/net_failover.log"
STATE="$HOME/.net_failover_state"      # contains "auto" when this daemon switched to hotspot
PAUSE="$HOME/.portal_pause"            # shared with portal watchdog (epoch = resume time)
NS="$HOME/dashboard/netswitch.sh"

THRESH="${FAILOVER_OFFLINE_SECS:-900}" # how long R-VIT must be dead before failover
IVL="${FAILOVER_CHECK_SECS:-60}"       # main loop cadence
JOIN_RETRY=900                         # min gap between hotspot join attempts (incl. scans)
RETURN_IVL=600                         # how often to scan for R-VIT recovery while on hotspot
MIN_SIG=45                             # nmcli SIGNAL (0-100) needed to call R-VIT "recovered"
BAD_BSSID="D4:C9:3C:42:4D:A1"          # FT-bug AP: seeing it is NOT recovery

HS_SSID="$(nmcli -g 802-11-wireless.ssid con show iphone-hotspot 2>/dev/null)"
[ -z "$HS_SSID" ] && HS_SSID="akksh’s iPhone"

log() { echo "$(date '+%F %T') $*" >> "$LOG"; }

tg() { # best-effort telegram note, never fatal
    [ -f "$HOME/.pibot_token" ] || return 0
    local TOKEN="" CHAT_ID=""
    . "$HOME/.pibot_token" 2>/dev/null
    [ -n "$TOKEN" ] && [ -n "$CHAT_ID" ] && \
        curl -s -m 10 -o /dev/null "https://api.telegram.org/bot${TOKEN}/sendMessage" \
             --data-urlencode "chat_id=${CHAT_ID}" --data-urlencode "text=$1"
    return 0
}

online() {
    [ "$(curl -s -o /dev/null -w '%{http_code}' -m 6 http://connectivitycheck.gstatic.com/generate_204)" = "204" ]
}

wlan_conn() { nmcli -t -f NAME,DEVICE con show --active 2>/dev/null | awk -F: '$2=="wlan0"{print $1; exit}'; }

paused() {
    [ -f "$PAUSE" ] || return 1
    local exp; exp=$(cat "$PAUSE" 2>/dev/null)
    [ -n "$exp" ] && [ "$(date +%s)" -lt "$exp" ]
}

rescan() { sudo nmcli device wifi rescan 2>/dev/null; sleep 4; }

hotspot_visible() {
    nmcli -f SSID dev wifi list --rescan no 2>/dev/null | grep -qF "$HS_SSID"
}

rvit_good() { # a 2.4GHz R-VIT BSSID that is NOT the FT-bug AP, at usable signal
    nmcli -f BSSID,SSID,SIGNAL,FREQ dev wifi list --rescan no 2>/dev/null | \
        awk -v bad="$BAD_BSSID" -v min="$MIN_SIG" \
            '$2=="R-VIT" && toupper($1)!=bad && $4+0<3000 && $3+0>=min {found=1} END {exit !found}'
}

log "net-failover started (threshold=${THRESH}s, check=${IVL}s, hotspot SSID=${HS_SSID})"

offline_since=0
last_try=0
last_return=0
good_scans=0

while true; do
    sleep "$IVL"
    paused && { offline_since=0; continue; }
    cur="$(wlan_conn)"

    if [ "$cur" = "iphone-hotspot" ]; then
        offline_since=0
        grep -q auto "$STATE" 2>/dev/null || continue   # manual switch: not ours to revert
        now=$(date +%s)
        [ $((now - last_return)) -lt "$RETURN_IVL" ] && continue
        last_return=$now
        rescan
        if rvit_good; then
            good_scans=$((good_scans + 1))
            if [ "$good_scans" -ge 2 ]; then
                log "R-VIT looks recovered (2 good scans) - trying autoreturn"
                good_scans=0
                "$NS" autoreturn >/dev/null 2>&1
                sleep 5
                if [ "$(wlan_conn)" = "R-VIT" ] && online; then
                    rm -f "$STATE"
                    log "back on R-VIT"
                    tg "✅ R-VIT recovered — Pi is back on hostel WiFi"
                else
                    log "autoreturn did not stick, staying on hotspot"
                fi
            fi
        else
            good_scans=0
        fi
        continue
    fi

    # on R-VIT (or stuck connecting / disconnected)
    if online; then
        offline_since=0
        rm -f "$STATE" 2>/dev/null
        continue
    fi
    now=$(date +%s)
    if [ "$offline_since" = 0 ]; then
        offline_since=$now
        log "offline detected (wlan0: ${cur:-none})"
        continue
    fi
    [ $((now - offline_since)) -lt "$THRESH" ] && continue
    [ $((now - last_try)) -lt "$JOIN_RETRY" ] && continue
    last_try=$now
    rescan
    if hotspot_visible; then
        mins=$(( (now - offline_since) / 60 ))
        log "R-VIT dead ${mins} min and hotspot visible - failing over"
        "$NS" hotspot >/dev/null 2>&1
        sleep 5
        if [ "$(wlan_conn)" = "iphone-hotspot" ] && online; then
            echo auto > "$STATE"
            log "failover to hotspot OK"
            tg "⚠️ R-VIT down ${mins} min — Pi auto-switched to your hotspot. It will hop back when R-VIT recovers."
        else
            log "hotspot join failed (netswitch fell back) - next attempt in $((JOIN_RETRY/60)) min"
        fi
    fi
done
