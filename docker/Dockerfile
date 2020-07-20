#
# Tesseract 4 OCR Runtime Environment - Docker Container
#

FROM ubuntu:18.04

RUN apt-get update && apt-get install -y software-properties-common wget git && add-apt-repository -y ppa:alex-p/tesseract-ocr
RUN apt-get update && apt-get install -y tesseract-ocr
RUN cd /usr/share/tesseract-ocr/4.00/tessdata && rm deu* && wget https://github.com/tesseract-ocr/tessdata/blob/master/deu.traineddata?raw=true -O deu.traineddata
RUN cd /usr/share/tesseract-ocr/4.00/tessdata && rm eng* && wget https://github.com/tesseract-ocr/tessdata/blob/master/eng.traineddata?raw=true -O eng.traineddata

VOLUME /home/work
WORKDIR /home/work
