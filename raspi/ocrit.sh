#!/bin/bash
#exec 1> >(logger -s -t $(basename $0)) 2>&1
OUT_DIR=$1
TMP_DIR=$2
echo $OUT_DIR
echo $TMP_DIR
BASE=$(basename $TMP_DIR)
echo "$1 und $2"
echo $(id)
#chown -R pi:pi $TMP_DIR
#cp -R $TMP_DIR $OUT_DIR
#/usr/bin/rsync -avz "$TMP_DIR/" "$OUT_DIR/$BASE/"
/usr/bin/rsync -avz -e "ssh" "$TMP_DIR/" joe@joesnuc:"/home/joe/watch_scans/$BASE/"
ls -la $OUT_DIR/$BASE
#cp $TMP_DIR/* .
echo "/scan2ocr.sh $BASE" 
#-t for pseudy tty - sudo docker problem
ssh joe@joesnuc -t "/home/joe/scan2ocr.sh $BASE"
echo "ssh fertig.."
echo $TMP_DIR
rm -rf $TMP_DIR
#exit
echo "OCR done"
