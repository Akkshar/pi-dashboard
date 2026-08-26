#!/usr/bin/env bash
# Wires hostel-LAN (wlan0) :22 scans into Cowrie on :2222, keeping real sshd on
# the wire/tailscale. Backs up before.rules, edits the *nat block with Python
# (never sed — before.rules corrupted via sed before), reloads ufw, and VERIFIES
# the router still works; restores the backup on any doubt.
set -euo pipefail

BR=/etc/ufw/before.rules
STAMP=$(cat /home/screenrpi/.fwstamp 2>/dev/null || echo backup)
sudo cp "$BR" "${BR}.bak-honeypot"

# allow the redirected port on the hostel interface (INPUT sees dport 2222)
sudo ufw allow in on wlan0 to any port 2222 proto tcp comment 'cowrie honeypot' >/dev/null

sudo python3 - "$BR" <<'PYEOF'
import sys
p = sys.argv[1]
s = open(p).read()
if 'REDIRECT --to-ports 2222' in s:
    print('redirect already present'); raise SystemExit
lines = s.splitlines(keepends=True)
out, chain_done, rule_done = [], False, False
for ln in lines:
    if ln.startswith('*nat'):
        out.append(ln)
        out.append(':PREROUTING ACCEPT [0:0]\n')     # declare the chain
        chain_done = True
        continue
    if chain_done and not rule_done and ln.startswith('-A POSTROUTING'):
        out.append('-A PREROUTING -i wlan0 -p tcp --dport 22 -j REDIRECT --to-ports 2222\n')
        rule_done = True
    out.append(ln)
assert rule_done, 'never found -A POSTROUTING to anchor before'
open(p, 'w').write(''.join(out))
print('patched')
PYEOF

sudo ufw reload >/dev/null
sleep 2

echo '--- redirect rule live in nat table?'
sudo iptables -t nat -S PREROUTING | grep 2222 || { echo 'RULE MISSING - restoring'; sudo cp "${BR}.bak-honeypot" "$BR"; sudo ufw reload; exit 1; }

echo '--- MASQUERADE (router) still present?'
sudo iptables -t nat -S POSTROUTING | grep -q MASQUERADE && echo 'router OK' || { echo 'ROUTER BROKEN - restoring'; sudo cp "${BR}.bak-honeypot" "$BR"; sudo ufw reload; exit 1; }

echo '--- laptop internet path (Pi forwards): generate_204'
curl -s -o /dev/null -w '%{http_code}\n' --max-time 6 http://connectivitycheck.gstatic.com/generate_204 || true
echo 'firewall wired for honeypot'
