#!/bin/bash
# Thanks to Andreas Gohr (http://www.splitbrain.org/) for the initial work
# https://github.com/splitbrain/paper-backup/

exec 1> >(logger -s -t $(basename $0)) 2>&1
#https://urbanautomaton.com/blog/2014/09/09/redirecting-bash-script-output-to-syslog/

#https://stackoverflow.com/questions/59895/getting-the-source-directory-of-a-bash-script-from-within
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

OUT_DIR=<..mydir..>
WATCH_SCANS="$DIR/watch_scans"
TMP_DIR="$WATCH_SCANS/$1"
FILE_NAME=scan_`date +%Y%m%d-%H%M%S`
LANGUAGE="deu"

export ABBYY_APPID=<your-app-id>
export ABBYY_PWD=<change-it>

#echo $TMP_DIR
echo "Start to OCR"
cd $TMP_DIR
# cut borders 
echo 'cutting borders...'
for i in scan_*.pnm; do
    mogrify -shave 50x5 "${i}"
done

# apply Contrast-Cleaning
# The whole image-recognition is far away from sold products like scansnap. Also Parameter 
# may need to adapted more flexible depending on the scanned paper. Maybe a job for a Neural Network. 
echo 'cleaning pages...'
for i in scan_*.pnm; do
    echo "${i}"
    convert "${i}" -brightness-contrast 1x40% "${i}"
done

## check if there is blank pages
## unpaper is doing a great job - but  sometimes it makes the resulting PDF nearly unusable.
echo 'checking for blank pages...'
for f in ./*.pnm; do
    unpaper --size "a4" --overwrite "$f" `echo "$f" | sed 's/scan/scan_unpaper/g'`
    #need to rename and delete original since newer versions of unpaper can't use same file name
    rm -f "$f"
done

# apply text cleaning and convert to tif
echo 'cleaning pages...'
for i in scan_*.pnm; do
    echo "${i}"
# adding lzw compression is effectiv by factors in size of resulting tif and pdf.
    convert "${i}" -compress lzw "${i}.tif"
done

# do OCR
echo 'doing OCR...'
for i in scan_*.pnm.tif; do
    echo "${i}"
#  Tests with tesseract 3 show a not acceptable/poor OCR recognition
#  Tesseract 4.0 is promissing but only available in Docker or ubuntu.
#  Also not trained for Language German.
#    tesseract "$i" "$i" -l $LANGUAGE hocr
#    hocr2pdf -i "$i" -s -o "$i.pdf" < "$i.hocr"

# abby offers a cloud OCR with an impressive OCR-Regocnition // 100 pages/month are free
# https://cloud.ocrsdk.com/Account/Welcome
# Abby offers this script at https://github.com/abbyysdk/ocrsdk.com/tree/master/Python
    python /MYZFS/Personal/joe/process.py -l German -pdf "${i}" "${i}".pdf
done

# create PDF
echo 'creating PDF...'
pdftk *.tif.pdf cat output "$FILE_NAME.pdf"

cp $FILE_NAME.pdf $OUT_DIR/
#delete 
#rm -f $TMP_DIR
# or safe process-steps for monitoring
mkdir $OUT_DIR/$FILE_NAME
cd $WATCH_SCANS
mv $1 $OUT_DIR/$FILE_NAME/

echo "OCR-Script is done"
