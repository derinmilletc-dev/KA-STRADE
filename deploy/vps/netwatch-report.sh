#!/usr/bin/env bash
# Binance bağlantı raporu:  sudo bash netwatch-report.sh [saat=6]
set -euo pipefail
H="${1:-6}"
LOG=/var/log/kaistrade/netwatch.csv
[ -s "$LOG" ] || { echo "Kayıt yok. Önce netwatch-install.sh çalıştır."; exit 1; }
SINCE="$(date -u -d "$H hours ago" +%Y-%m-%dT%H:%M:%S)"
awk -F, -v since="$SINCE" 'NR>1 && $1>=since {
  n++; if ($2!="200") fail++; if ($4>1.0) slow1++; if ($4>3.0) slow3++; if ($4>5.0) slow5++;
  if ($5!="200") cfail++; if ($6>1.0) cslow++; if ($4>max) max=$4; sum+=$4
}
END {
  if (!n) { print "Seçilen aralıkta kayıt yok."; exit }
  printf "Son %s saat, %d ölçüm\n", "'"$H"'", n
  printf "Binance: ortalama %.2f sn, en yavaş %.2f sn\n", sum/n, max
  printf "  başarısız: %d | 1 sn üstü: %d | 3 sn üstü: %d | 5 sn üstü: %d\n", fail, slow1, slow3, slow5
  printf "Kontrol sitesi (Cloudflare): başarısız %d | 1 sn üstü %d\n", cfail, cslow
}' "$LOG"
echo "--- en yavaş 8 Binance ölçümü ---"
awk -F, -v since="$SINCE" 'NR>1 && $1>=since {print $4","$1","$2","$6}' "$LOG" | sort -t, -k1 -nr | head -8 | awk -F, '{printf "%s sn  %s  binance_http=%s  kontrol=%s sn\n",$1,$2,$3,$4}'
