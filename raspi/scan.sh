#!/bin/bash
# Thanks to Andreas Gohr (http://www.splitbrain.org/) for the initial work
# https://github.com/splitbrain/paper-backup/
exec 1> >(logger -s -t $(basename $0)) 2>&1

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Load API config (OCR_API_HOST, API_KEY)
if [[ -f "$SCRIPT_DIR/ocrit.env" ]]; then
    # shellcheck source=raspi/ocrit.env
    source "$SCRIPT_DIR/ocrit.env"
fi
: "${OCR_API_HOST:=http://192.168.176.224:8000}"
: "${API_KEY:=changeme}"

TMP_DIR=$(mktemp -d)
FILE_NAME=scan_$(date +%Y%m%d-%H%M%S)

echo "Scanning to $TMP_DIR ..."
scanimage \
    --resolution 300 \
    --batch="$TMP_DIR/scan_%03d.pnm.tif" \
    --format=tiff \
    --mode Gray \
    --source 'ADF Duplex'
echo "Scan complete: $(ls "$TMP_DIR"/scan_*.pnm.tif 2>/dev/null | wc -l) pages"

# Upload scanned files to OCR REST API
echo "Uploading to $OCR_API_HOST ..."
CURL_FILES=()
for f in "$TMP_DIR"/scan_*.pnm.tif; do
    CURL_FILES+=(-F "files=@$f")
done

RESPONSE=$(curl -sf \
    -H "X-Api-Key: $API_KEY" \
    "${CURL_FILES[@]}" \
    "$OCR_API_HOST/scan/upload")

if [[ $? -ne 0 ]]; then
    echo "ERROR: Upload to OCR API failed" >&2
    rm -rf "$TMP_DIR"
    exit 1
fi

JOB_ID=$(echo "$RESPONSE" | grep -o '"job_id":"[^"]*"' | cut -d'"' -f4)
echo "OCR job queued: $JOB_ID"
echo "Check status: curl -H 'X-Api-Key: \$API_KEY' $OCR_API_HOST/scan/status/$JOB_ID"

rm -rf "$TMP_DIR"
echo "Done."

