#!/system/bin/sh
# 改机脚本：一次性把整组指纹换成自洽的一套（需要 root + Magisk 的 resetprop）
# 用法（在手机上，root 下执行）：
#   adb push capture/reset_device.sh /data/local/tmp/
#   adb shell "su -c 'sh /data/local/tmp/reset_device.sh --profile 3 --country US'"
#   adb reboot                      # 重启后 device_id/adid 才会重新生成
# 回滚：
#   adb shell "su -c 'sh /data/local/tmp/reset_device.sh --restore'"
#
# 原则：型号 / build / 屏幕 / 运营商 / 国家 / 时区 必须同组自洽，不能随机拼。
# 单改 device_id 不改 build = 照样 570。

set -u
BK=/data/local/tmp/fb_fp_backup
mkdir -p "$BK"

PROFILE="${PROFILE:-1}"
COUNTRY="${COUNTRY:-US}"
ACTION="apply"

while [ $# -gt 0 ]; do
  case "$1" in
    --profile) PROFILE="$2"; shift 2 ;;
    --country) COUNTRY="$2"; shift 2 ;;
    --restore) ACTION="restore"; shift ;;
    *) shift ;;
  esac
done

prop() { resetprop "$1" "$2" 2>/dev/null || setprop "$1" "$2"; }

# ---------------------------------------------------------------- 回滚
if [ "$ACTION" = "restore" ]; then
  if [ -f "$BK/props.map" ]; then
    while IFS='|' read -r k v; do prop "$k" "$v"; done < "$BK/props.map"
    echo "[+] props restored from $BK/props.map"
  else
    echo "[!] no backup found"
  fi
  rm -rf /data/data/com.facebook.katana/cache/* 2>/dev/null
  echo "[+] fb cache cleared, reboot to take effect"
  exit 0
fi

# ---------------------------------------------------------------- 备份（仅首次）
if [ ! -f "$BK/props.map" ]; then
  for k in ro.product.brand ro.product.model ro.product.device ro.product.name \
           ro.product.manufacturer ro.build.fingerprint ro.build.id ro.build.display.id \
           ro.build.version.release ro.build.version.sdk ro.serialno \
           gsm.sim.operator.numeric gsm.operator.numeric ro.product.locale \
           persist.sys.timezone; do
    v=$(getprop "$k")
    echo "$k|$v" >> "$BK/props.map"
  done
  echo "[+] original props backed up -> $BK/props.map"
fi

# ---------------------------------------------------------------- 指纹组定义
# 格式: brand|model|device|product|manufacturer|android_ver|sdk|density|w|h|mccmnc|carrier|locale|tz
case "$PROFILE" in
  1) G="samsung|SM-A536B|a53x|a53xeea|samsung|13|33|450|1080|2340|310260|T-Mobile|en_US|America/New_York" ;;
  2) G="Xiaomi|M2101K7AG|curtana|curtana_global|Xiaomi|12|31|440|1080|2400|310410|AT&T|en_US|America/Chicago" ;;
  3) G="OnePlus|LE2123|OnePlus9Pro|OnePlus9Pro_EEA|OnePlus|13|33|560|1440|3216|23430|EE|en_GB|Europe/London" ;;
  4) G="motorola|moto g(60)|elish|elish_g|motorola|12|31|400|1080|2460|26201|Telekom.de|de_DE|Europe/Berlin" ;;
  5) G="google|Pixel 7|panther|panther|Google|14|34|420|1080|2400|44010|NTT DOCOMO|ja_JP|Asia/Tokyo" ;;
  6) G="OPPO|CPH2451|OP5556L1|CPH2451EEA|OPPO|13|33|450|1080|2412|52501|SingTel|en_SG|Asia/Singapore" ;;
  *) echo "[!] unknown profile $PROFILE (1-6)"; exit 1 ;;
esac

IFS='|' read BRAND MODEL DEVICE PRODUCT MFG AVER SDK DENS W H MCCMN CARR LOC TZ <<EOF
$G
EOF

# 国家一致性校验：US/GB/DE/JP/SG 与 mccmnc 前缀必须匹配
PREFIX=$(echo "$MCCMN" | cut -c1-3)
case "$COUNTRY" in
  US) [ "$PREFIX" = "310" ] || [ "$PREFIX" = "311" ] || { echo "[!] mccmnc/country mismatch (US)"; exit 1; } ;;
  GB) [ "$PREFIX" = "234" ] || { echo "[!] mccmnc/country mismatch (GB)"; exit 1; } ;;
  DE) [ "$PREFIX" = "262" ] || { echo "[!] mccmnc/country mismatch (DE)"; exit 1; } ;;
  JP) [ "$PREFIX" = "440" ] || { echo "[!] mccmnc/country mismatch (JP)"; exit 1; } ;;
  SG) [ "$PREFIX" = "525" ] || { echo "[!] mccmnc/country mismatch (SG)"; exit 1; } ;;
esac

BUILD_ID="TP1A.$(date +%y%m%d).$(head -c2 /dev/urandom | od -An -tu1 | tr -d ' \n' | cut -c1-3)"
FP="${BRAND}/${MODEL}/${DEVICE}:${AVER}/${BUILD_ID}/$(head -c3 /dev/urandom | od -An -tx1 | tr -d ' \n'):user/release-keys"

echo "[*] applying profile $PROFILE / $COUNTRY"
prop ro.product.brand        "$BRAND"
prop ro.product.model        "$MODEL"
prop ro.product.device       "$DEVICE"
prop ro.product.name         "$PRODUCT"
prop ro.product.manufacturer "$MFG"
prop ro.build.fingerprint    "$FP"
prop ro.build.id             "$BUILD_ID"
prop ro.build.display.id     "$BUILD_ID user/release-keys"
prop ro.build.version.release "$AVER"
prop ro.build.version.sdk    "$SDK"
prop ro.serialno             "$(head -c6 /dev/urandom | od -An -tx1 | tr -d ' \n' | tr a-f A-F)"
prop gsm.sim.operator.numeric "$MCCMN"
prop gsm.operator.numeric     "$MCCMN"
prop ro.product.locale        "$LOC"
prop persist.sys.timezone     "$TZ"

# 清掉 FB 的所有本地状态（device_id / machine_id / adid 都存在这里）
for p in /data/data/com.facebook.katana /data/data/com.facebook.orca /sdcard/Android/data/com.facebook.katana; do
  [ -d "$p" ] && rm -rf "$p"/* 2>/dev/null && echo "[+] cleared $p"
done
[ -d /data/data/com.google.android.gms/files ] && rm -rf /data/data/com.google.android.gms/files/adid* 2>/dev/null

echo
echo "[*] verify:"
echo "    brand=$(getprop ro.product.brand) model=$(getprop ro.product.model) build=$(getprop ro.build.fingerprint)"
echo "    sim=$(getprop gsm.sim.operator.numeric) tz=$(getprop persist.sys.timezone) locale=$(getprop ro.product.locale)"
echo "    屏幕(density/w/h) 需与 model 匹配: $DENS / $W / $H  -> 手工核对 displaymetrics"
echo
echo "[!] 必须 reboot 后再生效；开机后第一次启动 FB 前不要登录任何已有账号"
echo "[!] 出口 IP 国家必须与 $COUNTRY 一致，否则本组白改"
