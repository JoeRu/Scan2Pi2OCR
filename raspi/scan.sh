#!/bin/bash
# Thanks to Andreas Gohr (http://www.splitbrain.org/) for the initial work
# https://github.com/splitbrain/paper-backup/
exec 1> >(logger -s -t $(basename $0)) 2>&1
OUT_DIR=/media/scanner
TMP_DIR=`mktemp -d`
FILE_NAME=scan_`date +%Y%m%d-%H%M%S`
LANGUAGE="deu"
echo 'scanning...'
scanimage --resolution 300 \
--batch="$TMP_DIR/scan_%03d.pnm" \
--format=pnm \
--mode Gray \
--source 'ADF Duplex'
echo "Output saved in $TMP_DIR/scan*.pnm"
chown -R joe:scanner $TMP_DIR
runuser -l joe -c "/home/joe/ocrit.sh $OUT_DIR $TMP_DIR" & 
echo "send all to OCR"
