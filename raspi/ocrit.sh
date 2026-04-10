#!/bin/bash
# Load local config (API host and key)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -f "$SCRIPT_DIR/ocrit.env" ]]; then
    # shellcheck source=raspi/ocrit.env
    source "$SCRIPT_DIR/ocrit.env"
fi
: "${OCR_API_HOST:=http://192.168.176.224:8000}"
: "${API_KEY:=changeme}"

#exec 1> >(logger -s -t $(basename $0)) 2>&1
OUT_DIR=$1
TMP_DIR=$2
SCAN_DIR=$TMP_DIR
echo $OUT_DIR
echo $TMP_DIR
BASE=$(basename $TMP_DIR)
echo "$1 und $2"
echo $(id)
# Upload scanned files to OCR REST API
echo "Uploading $(ls "$SCAN_DIR"/*.pnm.tif | wc -l) pages to OCR API..."
CURL_FILES=()
for f in "$SCAN_DIR"/*.pnm.tif; do
    CURL_FILES+=(-F "files=@$f")
done

RESPONSE=$(curl -sf \
    -H "X-Api-Key: $API_KEY" \
    "${CURL_FILES[@]}" \
    "$OCR_API_HOST/scan/upload")

if [[ $? -ne 0 ]]; then
    echo "ERROR: Upload to OCR API failed" >&2
    exit 1
fi

JOB_ID=$(echo "$RESPONSE" | grep -o '"job_id":"[^"]*"' | cut -d'"' -f4)
echo "OCR job queued: $JOB_ID"
echo "Check status: curl -H 'X-Api-Key: \$API_KEY' $OCR_API_HOST/scan/status/$JOB_ID"

echo $TMP_DIR
rm -rf $TMP_DIR
echo "OCR done"
