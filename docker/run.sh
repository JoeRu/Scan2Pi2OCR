#!/bin/bash
#docker run -dt --name t4re tesseractshadow/tesseract4re
docker stop t4re
docker rm t4re
docker run -dt --name t4re -v /MYZFS/Personal/joe/watch_scans:/home/work tesseractshadow/tesseract4ocrit
docker ps -f name=t4re
