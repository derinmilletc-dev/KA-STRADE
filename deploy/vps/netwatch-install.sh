#!/usr/bin/env bash
# Sunucu -> Binance bağlantı kalitesini 10 sn'de bir ölçüp kaydeder (kurulum, root):
#   sudo bash /opt/kaistrade/deploy/vps/netwatch-install.sh
# Rapor:  sudo bash /opt/kaistrade/deploy/vps/netwatch-report.sh [saat]
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "root olarak çalıştır: sudo bash $0"; exit 1; }
install -d -m 755 /var/log/kaistrade
cat > /usr/local/bin/kaistrade-netwatch <<'LOOP'
#!/usr/bin/env bash
LOG=/var/log/kaistrade/netwatch.csv
[ -s "$LOG" ] || echo "time_utc,binance_http,binance_connect_s,binance_total_s,control_http,control_total_s" > "$LOG"
while true; do
  b=$(curl -s -o /dev/null -m 15 -w "%{http_code},%{time_connect},%{time_total}" https://fapi.binance.com/fapi/v1/time 2>/dev/null || echo "000,0,15")
  c=$(curl -s -o /dev/null -m 15 -w "%{http_code},%{time_total}" https://www.cloudflare.com/cdn-cgi/trace 2>/dev/null || echo "000,15")
  echo "$(date -u +%Y-%m-%dT%H:%M:%S),$b,$c" >> "$LOG"
  if [ "$(stat -c %s "$LOG")" -gt 20000000 ]; then tail -n 20000 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"; fi
  sleep 10
done
LOOP
chmod 755 /usr/local/bin/kaistrade-netwatch
cat > /etc/systemd/system/kaistrade-netwatch.service <<'UNIT'
[Unit]
Description=KaisTrade Binance baglanti izleme
After=network-online.target

[Service]
ExecStart=/usr/local/bin/kaistrade-netwatch
Restart=always
Nice=10

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable --now kaistrade-netwatch
echo "Kuruldu. Kayıt: /var/log/kaistrade/netwatch.csv  (birkaç saat sonra rapor al)"
