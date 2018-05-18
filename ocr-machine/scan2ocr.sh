#!/bin/bash
# https://github.com/JoeRu/Scan2Pi2OCR
# Licensed under GPLv3 // Author: Johannes Rumpf - https://web-dreamer.de
# twitter: @jay_ar
# 
# Thanks to Andreas Gohr (http://www.splitbrain.org/) for the initial work
# https://github.com/splitbrain/paper-backup/
exec 1> >(logger -s -t $(basename $0)) 2>&1
#https://urbanautomaton.com/blog/2014/09/09/redirecting-bash-script-output-to-syslog/

#reading config
source ./scan2ocr.config.sh

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
# black below 0.05 Percent of Pixels allover --> than blank
    blank=$(echo "scale=4; ${black}/${white} < 0.005" | bc)
    #echo $white $black $blank
    if [ "${blank}" -eq "1" ]; then
        echo "${i} seems to be blank - removing it..."
        mv "${i}" "blanks/${i}"
    fi
done


# cut borders 
#echo 'cutting borders...'
#for i in scan_*.pnm; do
#    mogrify -shave 50x5 "${i}"
#done
# don't needed with ScanSnap.. just for not-document-scanners inside

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

# convert to tif and compress a little with lzw
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
#    tesseract "$i" "$i" -l $LANGUAGE hocr
#    hocr2pdf -i "$i" -s -o "$i.pdf" < "$i.hocr"

# using abby-cloud
# https://ocrsdk.com/documentation/quick-start-guide/python-ocr-sdk/
    python $PATH_TO_ABBY_PROCESS_PY/process.py -l German -pdf "${i}" "${i}".pdf 
done

# tesseract 4 - if run from container you need to run as root and mount the directorys accordingly to the container.
# ls > scan_list.txt
# @todo adapt path in container and run:
# docker exec -it t4re /bin/bash -c "cd ./test.ocr/; tesseract scan_list.txt test --psm 1 --oem 2 txt pdf hocr"
# adapt moving of result

# create PDF
echo 'creating PDF...'
pdftk *.tif.pdf cat output "$FILE_NAME.pdf"
cp $FILE_NAME.pdf $OUT_DIR/

cd $WATCH_SCANS

# save or trash work of files - for debug and process purposes
if [$TRASH_TMP_FILES -eq 1 ]
then
# delete steps
   rm -Rf $1
else
# save steps
   mkdir $OUT_DIR/$FILE_NAME
   mv $1 $OUT_DIR/$FILE_NAME/
fi

#done - next
echo "OCR-Script is done"

