#!/usr/bin/env bash
# Bring up Cloudflare WARP as a local HTTP proxy (wgcf + wireproxy) so yt-dlp
# does not egress from a blocked datacenter IP. Prints NO secrets: private
# keys and tokens are masked before anything else touches them.
# Success requires: warp=on in Cloudflare's trace AND the egress IP changed.
set -euo pipefail
WORK="${WARP_DIR:-${RUNNER_TEMP:-/tmp}/warp}"
mkdir -p "$WORK"
cd "$WORK"
PORT=8080

auth=()
[ -n "${GITHUB_TOKEN:-}" ] && auth=(-H "Authorization: Bearer $GITHUB_TOKEN")

fetch_release() { # repo glob out
  local url
  url=$(curl -fsSL "${auth[@]}" "https://api.github.com/repos/$1/releases/latest" |
    python3 -c "import json,sys,fnmatch; d=json.load(sys.stdin); print(next(a['browser_download_url'] for a in d['assets'] if fnmatch.fnmatch(a['name'], sys.argv[1])))" "$2")
  curl -fsSL "$url" -o "$3"
}

trace_ip() { # optional curl proxy args
  curl -fsS -m 20 "$@" https://www.cloudflare.com/cdn-cgi/trace
}

direct_trace=$(trace_ip)
direct_ip=$(echo "$direct_trace" | sed -n 's/^ip=//p')

fetch_release ViRb3/wgcf '*linux_amd64' wgcf
fetch_release pufferffish/wireproxy 'wireproxy_linux_amd64.tar.gz' wp.tgz
chmod +x wgcf
tar xzf wp.tgz wireproxy

ok=0
for try in 1 2 3; do
  if ./wgcf register --accept-tos >reg.log 2>&1; then ok=1; break; fi
  echo "wgcf register attempt $try failed: $(tail -n1 reg.log | cut -c1-160)"
  sleep $((try * 10))
done
[ "$ok" = 1 ] || { echo "WARP FAILED: could not register a WARP account"; exit 1; }
./wgcf generate >gen.log 2>&1 || { echo "WARP FAILED: wgcf generate"; exit 1; }

# mask secrets FIRST, then use them
for f in wgcf-account.toml wgcf-profile.conf; do
  grep -E '^(private_key|license_key|access_token|PrivateKey)' "$f" 2>/dev/null |
    sed -E "s/^[^=]*= *'?//; s/'? *$//" | while read -r v; do echo "::add-mask::$v"; done
done

cp wgcf-profile.conf wp.conf
printf '\n[http]\nBindAddress = 127.0.0.1:%s\n' "$PORT" >>wp.conf
nohup ./wireproxy -c wp.conf >wp.log 2>&1 &

via_trace=""
for i in $(seq 1 12); do
  if via_trace=$(trace_ip -x "http://127.0.0.1:$PORT" 2>/dev/null); then break; fi
  sleep 3
done
via_ip=$(echo "$via_trace" | sed -n 's/^ip=//p')
warp=$(echo "$via_trace" | sed -n 's/^warp=//p')
loc=$(echo "$via_trace" | sed -n 's/^loc=//p')
echo "egress check: direct_ip=$direct_ip proxy_ip=${via_ip:-none} warp=${warp:-none} loc=${loc:-?}"
if [ "$warp" != "on" ] || [ -z "$via_ip" ] || [ "$via_ip" = "$direct_ip" ]; then
  echo "WARP FAILED: egress did not change"
  tail -n 5 wp.log | cut -c1-200 || true
  exit 1
fi
echo "WARP OK: traffic via 127.0.0.1:$PORT leaves from a different IP"
[ -n "${GITHUB_ENV:-}" ] && echo "YT_PROXY=http://127.0.0.1:$PORT" >>"$GITHUB_ENV"
exit 0
