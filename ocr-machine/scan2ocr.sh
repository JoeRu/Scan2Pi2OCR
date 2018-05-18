#!/bin/bash
# Thanks to Andreas Gohr (http://www.splitbrain.org/) for the initial work
# https://github.com/splitbrain/paper-backup/
exec 1> >(logger -s -t $(basename $0)) 2>&1
#https://urbanautomaton.com/blog/2014/09/09/redirecting-bash-script-output-to-syslog/

OUT_DIR=<your-output-dir>
WATCH_SCANS="<dir-to->/watch_scans"
TMP_DIR="$WATCH_SCANS/$1"
FILE_NAME=scan_`date +%Y%m%d-%H%M%S`
LANGUAGE="deu" # only for tesseract - for abby you need to adapt the line below

export ABBYY_APPID=<your-app-id>
export ABBYY_PWD=<your-app-passwd>

#echo $TMP_DIR
echo "Start to OCR"
cd $TMP_DIR


#https://superuser.com/questions/343385/detecting-blank-image-files
# removing blank pages by checking black vs. white pixel proportions in histogram. 
mkdir -p "blanks"
for i in scan_*.pnm; do
    echo "${i}"
    if [[ -e $(dirname "$i")/.$(basename "$i") ]]; then
        echo "   protected."
        continue
    fi

    histogram=$(convert "${i}" -threshold 50% -format %c histogram:info:-)
    #echo $histogram
    white=$(echo "${histogram}" | grep "white" | cut -d: -f1)
    black=$(echo "${histogram}" | grep "black" | cut -d: -f1)
    if [[ -z "$black" ]]; then
        black=0
    fi

    blank=$(echo "scale=4; ${black}/${white} < 0.005" | bc)
    #echo $white $black $blank
    if [ "${blank}" -eq "1" ]; then
        echo "${i} seems to be blank - removing it..."
        mv "${i}" "blanks/${i}"
    fi
done


# cut borders 
echo 'cutting borders...'
for i in scan_*.pnm; do
    mogrify -shave 50x5 "${i}"
done

# apply Contrast-Cleaning
echo 'cleaning pages...'
for i in scan_*.pnm; do
    echo "${i}"
    convert "${i}" -brightness-contrast 1x40% "${i}"
done

## check if there is blank pages
# creates to much mess with the final documents - they simple don't look good anymore
#echo 'checking for blank pages...'
#for f in ./*.pnm; do
#    unpaper --size "a4" --overwrite "$f" `echo "$f" | sed 's/scan/scan_unpaper/g'`
#    #need to rename and delete original since newer versions of unpaper can't use same file name
#    rm -f "$f"
#done

# apply text cleaning and convert to tif
echo 'cleaning pages...'
for i in scan_*.pnm; do
    echo "${i}"
    convert "${i}" -compress lzw "${i}.tif"
done

# do OCR
echo 'doing OCR...'
for i in scan_*.pnm.tif; do
    echo "${i}"
# using tesseract
    tesseract "$i" "$i" -l $LANGUAGE hocr
    hocr2pdf -i "$i" -s -o "$i.pdf" < "$i.hocr"
# using abby-cloud
#    python <path-to->/process.py -l German -pdf "${i}" "${i}".pdf 
done

# create PDF
echo 'creating PDF...'
pdftk *.tif.pdf cat output "$FILE_NAME.pdf"
cp $FILE_NAME.pdf $OUT_DIR/

cd $WATCH_SCANS
# save steps
mkdir $OUT_DIR/$FILE_NAME
mv $1 $OUT_DIR/$FILE_NAME/

# delete steps
#rm -Rf $1

#done - next
echo "OCR-Script is done"
