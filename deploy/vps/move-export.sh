#!/usr/bin/env bash
# ESKİ sunucuda çalışır: servisi durdurur, veritabanı + veri klasörü + ayar dosyasını yeni sunucuya kopyalar.
# Kullanım:  sudo bash move-export.sh YENI_SUNUCU_IP
# Geri dönüş (yeni sunucu denenmeden önce de, sonra da): sudo systemctl start kaistrade
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "sudo ile çalıştır: sudo bash $0 YENI_IP"; exit 1; }
NEWIP="${1:-}"
[ -n "$NEWIP" ] || { echo "Kullanım: sudo bash $0 YENI_SUNUCU_IP"; exit 1; }
OUT=/root/kais-move
DB=kaistrade

echo "Bu betik ESKİ sunucudaki servisi DURDURUR."
echo "Önce emin ol: açık pozisyon yok, hiçbir bot çalışmıyor."
read -r -p "Açık pozisyon yok ve botlar durduysa 'evet' yaz: " ANS </dev/tty
[ "$ANS" = "evet" ] || { echo "İptal edildi. Hiçbir şey değişmedi."; exit 1; }

STOPPED=0
trap 'if [ "$STOPPED" = 1 ]; then echo; echo "HATA: servis eski sunucuda yeniden başlatılıyor."; systemctl start kaistrade || true; fi' ERR

rm -rf "$OUT"; mkdir -p "$OUT"; chmod 700 "$OUT"

echo "[1/5] Servis durduruluyor..."
systemctl stop kaistrade; STOPPED=1

echo "[2/5] Veritabanı yedeği alınıyor..."
sudo -u postgres pg_dump -Fc "$DB" -f "$OUT/kais.dump"
MAJOR=$(( $(sudo -u postgres psql -tAc "show server_version_num") / 10000 ))
echo "$MAJOR" > "$OUT/pgmajor"
for t in $(sudo -u postgres psql -d "$DB" -tAc "select tablename from pg_tables where schemaname='public' order by 1"); do
  echo "$t $(sudo -u postgres psql -d "$DB" -tAc "select count(*) from \"$t\"")"
done > "$OUT/counts.txt"
echo "   tablo sayısı: $(wc -l < "$OUT/counts.txt"), PostgreSQL sürüm: $MAJOR"

echo "[3/5] Veri klasörü ve ayar dosyası paketleniyor..."
tar czf "$OUT/kais-data.tgz" -C / var/lib/kaistrade etc/kaistrade.env
( cd "$OUT" && sha256sum kais.dump kais-data.tgz pgmajor counts.txt > SHA256SUMS )
chmod 600 "$OUT"/*
du -h "$OUT"/*

echo "[4/5] Yeni sunucuya kopyalanıyor (yeni sunucunun root şifresi istenecek)..."
scp -o StrictHostKeyChecking=accept-new -r "$OUT" "root@$NEWIP:/root/"

echo "[5/5] Bitti."
echo "Eski sunucudaki servis DURDU ve durmuş kalacak. Yeni sunucuda 'move-import.sh' çalıştır."
echo "Vazgeçersen eski sunucuda: sudo systemctl start kaistrade"
trap - ERR
