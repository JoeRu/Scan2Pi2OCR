# Scan2Pi2OCR
This Project aims to have a RASPI as Saned-Scan-Engine and send the scanned documents to a second more powerfull computer for OCR and potential send to cloud.

```console
sudo apt-get install imagemagick bc exactimage pdftk \
   tesseract-ocr tesseract-ocr-deu tesseract-ocr-eng
```

For use of Buttons is used [insaned](https://github.com/abusenius/insaned)


The Process is to be as followed:
insaned on the RASPi is calling the script scan.sh from the raspi folder.
Please adapt path in file insaned/events/scan and make it executable. 
To enable more scans this script creates a new process for the ocr-task calling ocrit.sh with &.

ocrit.sh manages to transfer the data to the target scanning device - and starts the final OCR-Process there with a ssh execution call.

With this version - supported is the use of tesseract and abby-cloud. 
Abby has really good ocr-capabilities (but payed and with price-politics only good for big count of pages recognition) - still better (at least in German) than tesseract; but [tesseract4](https://github.com/tesseract-ocr/tesseract) with LTSM and (_important_) use of the -l language parameter is really good as well.

The last changes reflect the switch to use docker with tesseract 4. Base of this docker image is [tesseract-ocr-re](https://github.com/tesseract-shadow/tesseract-ocr-re) some changes are in the docker directory. 
--
Added some lines to use [rclone](https://rclone.org/) to push the files to a cloud-Provider and create a link.
Afterwards the link is added to an Email including the scaned File.

## Installation on Raspi

Prepare your raspberry; 
Install your scanner so that `scanimage -L` is working on your raspi; for example with this [tutorial](https://www.johndstech.com/how-to/geek-friday-setting-up-epson-scanning-on-raspberry-pi/)
In my case [Scansnap S1300](https://blog.dtpnk.tech/en/install_snapscan/#) - or check raspi folder


```
 sudo adduser pi scanner
 sudo apt install sane libsane libsane-dev
 git clone https://github.com/abusenius/insaned.git
 cd insaned
 make
 sudo cp ./insaned /usr/bin
 sudo mkdir /etc/insaned
 sudo mkdir /etc/insaned/events
 sudo cp events/* /etc/insaned/events/
 sudo cp systemd/insaned.service /etc/systemd/system/
 sudo systemctl enable insaned
 sudo touch /etc/default/insaned
 sudo touch /etc/insaned/events/off
 sudo chmod a+x /etc/insaned/events/*
 sudo systemctl start insaned
 
 ```
 tbd - script the make - udev-rule for scansnap - as insaned runs 'insane' on powerdown... better use PR of insaned / This fixes the problem

## OCR REST API (New Architecture)

The OCR pipeline is now a containerized REST API service, replacing the previous SSH/rsync-based approach.

### Quick Start

```bash
# 1. Configure environment
cp .env.example .env
# Edit .env with your API key, Paperless token, etc.

# 2. Start the service
cd ocr-api
docker compose up -d

# 3. Verify health
curl http://192.168.176.224:8000/health
```

### API Reference

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Service health check |
| `/scan/upload` | POST | Upload scan pages for OCR processing |
| `/scan/status/{job_id}` | GET | Check job status |

All endpoints except `/health` require the `X-Api-Key` header.

### Raspberry Pi Configuration

On the scanner Pi, configure `raspi/ocrit.env` (copy from `raspi/ocrit.env.example`):

```bash
cp raspi/ocrit.env.example raspi/ocrit.env
# Edit with your API host and key
```

### Output Destinations

Set flags in `.env` to enable one or more output targets simultaneously:

| Flag | Target |
|------|--------|
| `ENABLE_FILESYSTEM=true` | Local `output/` directory |
| `ENABLE_PAPERLESS=true` | Paperless-ngx via REST API |
| `ENABLE_RCLONE=true` | Cloud via rclone |
| `ENABLE_MAIL=true` | E-Mail-Benachrichtigung mit OCR-Textvorschau |

### rclone konfigurieren (ohne lokale rclone-Installation)

Die `rclone.conf` wird per Volume-Mount in den Container eingebunden (Pfad: `RCLONE_CONFIG_PATH` in `.env`).
Zum Anlegen der Config ohne lokales rclone gibt es zwei Wege:

#### Weg 1: `docker exec` mit interaktivem Config-Wizard

```bash
# Container temporär starten (ohne den normalen Dienst)
docker compose -f ocr-api/docker-compose.yml run --rm --entrypoint bash ocr-api

# Im Container:
rclone config
# → Wizard folgen: n (New remote) → Name → Microsoft OneDrive → ...
# Am Ende: Config liegt unter /ocr-api/.config/rclone/rclone.conf
# ABER: das ist nur im laufenden Container – beim Stopp weg!
```

> **Problem bei OAuth (OneDrive):** rclone öffnet eine Browser-URL für die Anmeldung.
> In einem headless Container funktioniert das nicht direkt.
> Lösung: **Auto-Token auf einem anderen Rechner** (Weg 1b) oder **Weg 2**.

#### Weg 1b: Headless OAuth – Token von anderem Rechner übertragen

Auf einem Rechner **mit Browser und rclone installiert**:

```bash
rclone authorize "onedrive"
# Browser öffnet sich → Microsoft-Login → Token wird ausgegeben
# Token kopieren (JSON-Block zwischen { ... })
```

Dann im Container-Wizard bei der Frage `Use auto config?` → `n` → Token einfügen.

#### Weg 2: Fertige Config von einem anderen Rechner kopieren

Falls rclone irgendwo bereits konfiguriert ist:

```bash
# Config auf den Server übertragen
scp ~/.config/rclone/rclone.conf user@server:/root/.config/rclone/rclone.conf

# Sicherstellen dass RCLONE_CONFIG_PATH in .env stimmt:
RCLONE_CONFIG_PATH=/root/.config/rclone/rclone.conf
```

#### Konfiguration testen

```bash
# Nach dem Start des Containers – rclone im laufenden Container testen:
docker compose -f ocr-api/docker-compose.yml exec ocr-api \
    rclone lsd OneDrive_xx:

# Oder direkt ein File hochladen:
docker compose -f ocr-api/docker-compose.yml exec ocr-api \
    rclone copy /ocr-api/output/test.pdf OneDrive_xx:scanner/
```

### Architecture

```
Scanner Pi  ──curl POST──▶  OCR API (:8000)
                                │
                    ┌───────────┼───────────┐
                    ▼           ▼           ▼
              Filesystem   Paperless-ngx  rclone
               (output/)    (REST API)   (OneDrive)
```

