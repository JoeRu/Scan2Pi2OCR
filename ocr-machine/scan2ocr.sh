#!bash
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
#echo $PATH

#echo $TMP_DIR
echo "Start to OCR - $TMP_DIR"
cd $TMP_DIR


#https://superuser.com/questions/343385/detecting-blank-image-files
# removing blank pages by checking black vs. white pixel proportions in histogram. 
mkdir -p "blanks"
for i in scan_*.pnm.tif; do
    echo "${i}"
    if [[ -e $(dirname "$i")/.$(basename "$i") ]]; then
        echo "   protected."
        continue
    fi
#    echo "convert "${i}" -threshold 50% -format %c histogram:info:-)"
    histogram=$(convert "${i}" -threshold 50% -format %c histogram:info:-)
#    echo "histogramm: $histogram"
    white=$(echo "${histogram}" | grep "(255,255,255)" | cut -d: -f1)
#   echo "(echo "${histogram}" | grep "white" | cut -d: -f1"
    black=$(echo "${histogram}" | grep "(0,0,0)" | cut -d: -f1)
#   echo "echo "${histogram}" | grep "black" | cut -d: -f1"
#    echo "white: $white black: $black"
    if [[ -z "$black" ]]; then
        black=0
    fi
# black below 0.05 Percent of Pixels allover --> than blank
    blank=$(echo "scale=4; ${black}/${white} < 0.005" | bc)
#    echo "(echo \"scale=4; ${black}/${white} < 0.005\" | bc)"
#    echo "xX: $white schwarz: $black blank: $blank"
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
for i in scan_*.pnm.tif; do
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

#echo 'compressing pages...'
#for i in scan_*.pnm; do
#    echo "${i}"
#    /usr/bin/convert "${i}" -compress jpeg "${i}.tif"
#done

rm scan_list.txt
# do OCR
echo 'doing OCR...'
for i in scan_*.pnm.tif; do
    echo "${i}" >> scan_list.txt
# using tesseract
#    tesseract "$i" "$i" -l $LANGUAGE hocr
#    hocr2pdf -i "$i" -s -o "$i.pdf" < "$i.hocr"

# using abby-cloud
# https://ocrsdk.com/documentation/quick-start-guide/python-ocr-sdk/
#    python $PATH_TO_ABBY_PROCESS_PY/process.py -l German -pdf "${i}" "${i}".pdf 
done

# tesseract 4 - if run from container you need to run as root and mount the directorys accordingly to the container.
# ls > scan_list.txt
# @todo adapt path in container and run:
# sudo docker exec -t t4re /bin/bash -c "cd ./$1/; tesseract scan_list.txt $FILE_NAME -l deu --psm 1 --oem 2 txt pdf hocr"

tesseract scan_list.txt $FILE_NAME --dpi 300 --oem 1 -l deu+eng+frk --psm 1 txt pdf hocr

cp $FILE_NAME.pdf $OUT_DIR/
cd $WATCH_SCANS
#sudo chown -R $USER:$USER $WATCH_SCANS
# adapt moving of result
 
# create PDF
echo 'creating PDF...'
#pdftk *.tif.pdf cat output "$FILE_NAME.pdf"
cp $WATCH_SCANS/$1/$FILE_NAME.pdf $OUT_DIR/
exp_file="$OUT_DIR/$FILE_NAME.pdf"
echo "output file $exp_file"

if [ -f $exp_file ]; then

rclone copy $exp_file OneDrive_Joe:scanner/
onedrive_link=$(rclone link OneDrive_Joe:scanner/$FILE_NAME.pdf)

head_file=$( head -n 50 $WATCH_SCANS/$1/$FILE_NAME.txt )
mail_text="
<html>
<body>
Please find the file attached<br>
 Original Path is<br>
$exp_file<p>
OneDrive_link<br>
<a href=\"$onedrive_link\">$onedrive_link</a><p>

<h2>-----------------Content------------</h2>
$head_file
</body>
</html>
"

echo $mail_text | mutt -e "set content_type=text/html" -s "$exp_file - ScanPi Mail" johannes@rumpf.name
fi
# attach file - max size problem
# -a $exp_file

#cd $WATCH_SCANS

# save or trash work of files - for debug and process purposes
if [ $TRASH_TMP_FILES -eq 1 ];
   then
# delete steps
     rm -Rf $1
     echo "delete $1"
   else
# save steps
#     mkdir $OUT_DIR/$FILE_NAME
#     mv $1 $OUT_DIR/$FILE_NAME/
     echo "move done"
   fi

#done - next
echo "OCR-Script is done"

