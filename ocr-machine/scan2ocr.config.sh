OUT_DIR=<your-final-output-dir>
WATCH_SCANS="<directory-for->/watch_scans"
TMP_DIR="$WATCH_SCANS/$1"
FILE_NAME=scan_`date +%Y%m%d-%H%M%S`

#Process-controll - Trash TMP-Files 1 = YES
TRASH_TMP_FILES = 0

#tesseract only parameter
LANGUAGE="deu"

# https://cloud.ocrsdk.com/Account/Welcome 
# First package of 30 Pages is free - you get another 100 when twittering about them. Prices seems fair - result is much better than tesseract3 while
# tesseract 4 looks very promissing - it is still not available for german language //
# https://github.com/tesseract-ocr/tesseract/wiki/4.0-Docker-Containers.
# Tesseract 4 OCR Runtime Environment
export ABBYY_APPID=<your-abby-app-id>
export ABBYY_PWD=<your-abby-app-pswd>

#https://ocrsdk.com/documentation/quick-start-guide/python-ocr-sdk/
PATH_TO_ABBY_PROCESS_PY=<path-to--process.py>
