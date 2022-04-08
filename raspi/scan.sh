#!/bin/bash
# Thanks to Andreas Gohr (http://www.splitbrain.org/) for the initial work
# https://github.com/splitbrain/paper-backup/
# add output to shell if run directly as well put it to syslog
exec 1> >(logger -s -t $(basename $0)) 2>&1

# https://stackoverflow.com/questions/59895/getting-the-source-directory-of-a-bash-script-from-within
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

OUT_DIR=/media/scanner
TMP_DIR=`mktemp -d`
FILE_NAME=scan_`date +%Y%m%d-%H%M%S`

echo 'scanning...'
scanimage --resolution 300 \
--batch="$TMP_DIR/scan_%03d.pnm.tif" \
#--format=pnm \
--format=tiff
--mode Gray \
--source 'ADF Duplex'
echo "Output saved in $TMP_DIR/scan*.pnm"

#problem: scanner runs only with root... insaned.service
chown -R pi:pi $TMP_DIR
# create new process and free ressources to enable a new scan.
runuser -l pi -c "$DIR/ocrit.sh $OUT_DIR $TMP_DIR" & 

echo "send all to OCR"
