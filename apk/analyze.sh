#!/usr/bin/env bash
# FB APK 静态侦察：不问设备，先把「协议里有什么」从 APK 里挖出来
# 用法:
#   apk/analyze.sh /path/to/base.apk
#   apk/analyze.sh ~/apk_bundle/            # 目录（split APK / xapk 解包后）
# 输出:
#   apk/work/<apk名>/           解包
#   apk/report/strings_all.txt  汇总字符串
#   apk/report/REPORT.md        报告
#   apk/report/hits.json        机读结果
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(dirname "$HERE")"
WORK="$HERE/work"; REP="$HERE/report"
SRC="${1:-}"
[ -z "$SRC" ] && { echo "usage: $0 <base.apk|dir>"; exit 2; }
mkdir -p "$WORK" "$REP"

NAME=$(basename "$SRC"); NAME="${NAME%.apk}"; NAME="${NAME%.xapk}"
OUT="$WORK/$NAME"; mkdir -p "$OUT"

echo "[1/5] 解包 $SRC"
if [ -d "$SRC" ]; then
  for f in "$SRC"/*.apk; do d="$OUT/$(basename "${f%.apk}")"; mkdir -p "$d"; unzip -oq "$f" -d "$d" 2>/dev/null; done
else
  unzip -oq "$SRC" -d "$OUT" 2>/dev/null
  # xapk / apks 里套 apk
  find "$OUT" -maxdepth 2 -name "*.apk" | while read -r inner; do
    d="$OUT/unpacked/$(basename "${inner%.apk}")"; mkdir -p "$d"; unzip -oq "$inner" -d "$d" 2>/dev/null
  done
fi

DEX=$(find "$OUT" -name "classes*.dex" | wc -l | tr -d ' ')
SO=$(find "$OUT" -name "*.so" | wc -l | tr -d ' ')
ASSET=$(find "$OUT" -path "*/assets/*" -type f | wc -l | tr -d ' ')
echo "      dex=$DEX  so=$SO  assets=$ASSET"

echo "[2/5] 抽字符串（dex + so + assets 清单）"
ALL="$REP/strings_${NAME}.txt"; : > "$ALL"
find "$OUT" -name "classes*.dex" -o -name "*.so" | while read -r b; do
  echo "### $b" >> "$ALL"; strings -a -n 6 "$b" >> "$ALL" 2>/dev/null
done
find "$OUT" -path "*/assets/*" -type f | sed "s|$OUT/||" | sort > "$REP/assets_${NAME}.txt"

echo "[3/5] 关键字扫描"
scan() {  # scan <key> <regex> [file-filter-regex]
  local key="$1" pat="$2"
  local n; n=$(grep -aoE "$pat" "$ALL" 2>/dev/null | sort -u | wc -l | tr -d ' ')
  printf '%s\t%s\n' "$key" "$n"
}
{
echo -e "== 端点 host =="
grep -aoE '(b-graph|graph|api|m|www|upload|edge-mqtt)\.facebook\.com|facebook\.com/(reg|api/graphql|method/)[a-zA-Z0-9_/]*' "$ALL" | sort | uniq -c | sort -rn | head -30
echo; echo -e "== 协议头 =="
grep -aoE 'X-FB-[A-Za-z-]+|X-TIGON-[A-Za-z-]+|X-Zero-Rated|X-ASBD-ID' "$ALL" | sort | uniq -c | sort -rn | head -40
echo; echo -e "== GraphQL 元字段 =="
grep -aoE 'fb_api_(req_friendly_name|caller_class|version)|client_doc_id|graph_service|RelayModern|params=|bloks_version_id' "$ALL" | sort | uniq -c | sort -rn | head -20
echo; echo -e "== 注册相关 identifier =="
grep -aoE '[A-Z][A-Za-z0-9_]{3,60}(:?(Reg|Register|Signup|AccountCreate|CreateAccount)[A-Za-z0-9_]{0,40})' "$ALL" | sort -u | grep -iE 'reg|signup|accountcreate|createaccount' | head -40
echo; echo -e "== doc_id 候选（17-20位数字）=="
grep -aoE '\b[0-9]{17,20}\b' "$ALL" | sort | uniq -c | sort -rn | head -25
echo; echo -e "== 完整性/证明 =="
grep -aoiE 'play[._-]?integrity|integrity[._-]?token|safetynet|attestation[a-z_]*|X-FB-Attestation|device[._-]?attest|key[._-]?attestation' "$ALL" | sort | uniq -c | sort -rn | head -25
echo; echo -e "== play core / integrity 依赖 =="
grep -aoE 'com\.google\.android\.play\.core[a-z.]*' "$ALL" | sort | uniq -c | sort -rn | head -10
echo; echo -e "== 证书绑定 =="
grep -aoE 'CertificatePinner|TrustKit|FbPinning|OkHostnameVerifier|X509TrustManager' "$ALL" | sort | uniq -c | sort -rn | head -10
echo; echo -e "== 设备/遥测 =="
grep -aoE 'machine_id|device_id|family_device_id|adid|GAID|android_id|install_id|session_id|X-FB-Device-Group|Quality-Session-ID' "$ALL" | sort | uniq -c | sort -rn | head -20
echo; echo -e "== Liger / tigon =="
grep -aoiE 'liger|tigon|proxygen|HTTP/2|h2' "$ALL" | sort | uniq -c | sort -rn | head -12
} > "$REP/hits_${NAME}.txt" 2>/dev/null

echo "[4/5] 生成 json + 判定"
python3 - "$REP/hits_${NAME}.txt" "$REP/hits_${NAME}.json" "$NAME" <<'PY'
import json, re, sys
txt, out, name = sys.argv[1], sys.argv[2], sys.argv[3]
raw = open(txt, encoding="utf-8", errors="ignore").read()
def sect(title):
    m = re.search(r"== " + re.escape(title) + r" ==\n(.*?)(?=\n== |\Z)", raw, re.S)
    return [l.strip() for l in (m.group(1).strip().splitlines() if m else []) if l.strip()]
integrity = sect("完整性/证明") + sect("play core / integrity 依赖")
attest_hit = any(re.search(r"integrity|attest|safetynet", l, re.I) for l in integrity)
fb_attest = any("X-FB-Attestation" in l for l in sect("协议头"))
verdict = "BLOCKED_FOR_PURE_PROTOCOL_LIKELY" if (attest_hit and fb_attest) else \
          ("NEEDS_CAPTURE_CONFIRM" if attest_hit else "LIKELY_FEASIBLE")
res = {
  "apk": name, "verdict": verdict,
  "integrity_hits": integrity[:20],
  "hosts": sect("端点 host")[:15],
  "xb_headers": sect("协议头")[:20],
  "graphql_meta": sect("GraphQL 元字段")[:15],
  "reg_identifiers": sect("注册相关 identifier")[:25],
  "doc_id_candidates": sect("doc_id 候选（17-20位数字）")[:15],
  "pinning": sect("证书绑定")[:10],
  "device_fields": sect("设备/遥测")[:15],
}
json.dump(res, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print("     verdict =", verdict)
print("     " + json.dumps({"integrity": len(integrity), "hosts": len(res["hosts"]),
      "reg_ids": len(res["reg_identifiers"]), "docids": len(res["doc_id_candidates"])}, ensure_ascii=False))
PY

echo "[5/5] 写报告"
{
echo "# APK 静态侦察报告：$NAME"
echo
echo "生成时间：$(date '+%F %T')"
echo
echo '```'
cat "$REP/hits_${NAME}.txt"
echo '```'
echo
echo "机读结果：\`$(basename "$REP/hits_${NAME}.json")\`"
} > "$REP/REPORT_${NAME}.md"

echo
echo "[done] $REP/REPORT_${NAME}.md"
