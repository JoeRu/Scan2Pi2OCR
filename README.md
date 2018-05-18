# Scan2Pi2OCR
This Project aims to have a RASPI as Saned-Scan-Engine and send the scanned documents to a second more powerfull computer for OCR and potentiall put to cloud.

```console
sudo apt-get install imagemagick bc exactimage pdftk \
   tesseract-ocr tesseract-ocr-deu tesseract-ocr-eng
```

For use of Buttons is used:
https://github.com/abusenius/insaned

The Process is to be as followed:
insaned on the RASPi is calling the script scan.sh from the raspi folder. (configs still missing)...
To enable more scans this script creates a new process for the ocr-task calling ocrit.sh with &.

ocrit.sh manages to transfer the data to the target scanning device - and starts the final OCR-Process there with a ssh execution call.

With this version - supported is the use of tesseract and abby-cloud. While tesseract4 shows great capabilities it is still in Beta and only usable with ubuntu or with docker.
Abby-Cloud is payed! But has a real good OCR-recognition - so you choose what you need.

