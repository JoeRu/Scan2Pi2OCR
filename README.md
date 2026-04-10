# Scan2Pi2OCR

A Raspberry Pi scanner front-end that sends scanned documents through a containerized OCR pipeline, enriches them with AI-extracted metadata, and delivers them to Paperless-ngx, cloud storage, e-mail, or the local filesystem.

## How it works

```
Scanner Pi
  insaned ──▶ scan.sh ──▶ ocrit.sh
                               │
                          curl POST (multipart)
                               │
                               ▼
                      OCR API (:8000)  [Docker]
                               │
               ┌───────────────┼──────────────┬──────────┐
               ▼               ▼              ▼          ▼
         Filesystem       Paperless-ngx    rclone      Mail
          (output/)        (REST API)    (OneDrive)
```

After OCR, an optional AI step (OpenRouter) classifies the document and:
- Sets **correspondent**, **document type**, and **tags** in Paperless-ngx (auto-creates if missing)
- Renames the output file to `YYYYMMDD_HHMMSS_<topic>_<korrespondent>.pdf`

---

## Installation on the Raspberry Pi

Prepare your Raspberry Pi and install a scanner so that `scanimage -L` works. Example for Scansnap S1300: [blog post](https://blog.dtpnk.tech/en/install_snapscan/).

### Install insaned (scanner button daemon)

```bash
sudo adduser pi scanner
sudo apt install sane libsane libsane-dev
git clone https://github.com/abusenius/insaned.git
cd insaned
make
sudo cp ./insaned /usr/bin
sudo mkdir -p /etc/insaned/events
sudo cp events/* /etc/insaned/events/
sudo cp systemd/insaned.service /etc/systemd/system/
sudo systemctl enable insaned
sudo touch /etc/default/insaned /etc/insaned/events/off
sudo chmod a+x /etc/insaned/events/*
sudo systemctl start insaned
```

### Configure scan scripts

```bash
cp raspi/ocrit.env.example raspi/ocrit.env
# Edit raspi/ocrit.env – set API_HOST and API_KEY
```

Edit `insaned/events/scan` to point to the correct path of `scan.sh`, then make it executable.

---

## OCR API – Quick Start

The OCR pipeline runs as a Docker container on the more powerful machine.

### 1. Configure environment

```bash
cp .env.example .env
# Edit .env with your values (see Configuration section below)
```

### 2. Start the service

```bash
docker compose up -d
```

### 3. Verify health

```bash
curl http://localhost:9000/health
# {"status":"ok"}
```

---

## Configuration

All settings live in `.env` (copy `.env.example` as a starting point).

### Authentication

| Variable | Example | Description |
|----------|---------|-------------|
| `API_KEY` | `s3cr3t-l0ng-rand0m-key` | Required for all API endpoints except `/health` |

### Output destinations

Enable one or more simultaneously:

| Variable | Example | Description |
|----------|---------|-------------|
| `ENABLE_FILESYSTEM` | `true` | Save PDFs to local `output/` directory |
| `OUTPUT_DIR` | `/ocr-api/output` | Path **inside** container (matches volume mount) |
| `ENABLE_PAPERLESS` | `true` | Upload to Paperless-ngx |
| `PAPERLESS_URL` | `http://192.168.1.100:8000` | Paperless-ngx base URL |
| `PAPERLESS_TOKEN` | `abc123...` | Paperless API token |
| `ENABLE_RCLONE` | `false` | Upload to cloud via rclone |
| `RCLONE_TARGET` | `OneDrive:scanner/` | rclone remote:path |
| `RCLONE_CONFIG_PATH` | `/root/.config/rclone/rclone.conf` | Host path to rclone.conf (mounted read-only) |
| `ENABLE_MAIL` | `false` | Send e-mail notification with OCR text preview |
| `MAIL_TO` | `you@example.com` | Recipient address |
| `SMTP_HOST` | `smtp.example.com` | SMTP server |
| `SMTP_PORT` | `587` | SMTP port |
| `SMTP_USER` | `sender@example.com` | SMTP login |
| `SMTP_PASSWORD` | `yourpassword` | SMTP password |
| `SMTP_FROM` | `ocr@example.com` | From address (defaults to `SMTP_USER`) |

### OCR settings

| Variable | Example | Description |
|----------|---------|-------------|
| `OCR_LANGUAGE` | `deu+eng+frk` | Tesseract language codes (`+`-separated) |
| `TRASH_TMP_FILES` | `true` | Delete tmp files after job completes |

### AI metadata extraction (optional)

When enabled, the first ~3 000 characters of OCR text are sent to an OpenRouter LLM. The model returns structured metadata that drives Paperless enrichment and file naming.

| Variable | Example | Description |
|----------|---------|-------------|
| `ENABLE_AI_METADATA` | `false` | Enable AI classification step |
| `OPENROUTER_API_KEY` | `sk-or-abc...` | API key from [openrouter.ai](https://openrouter.ai/keys) |
| `OPENROUTER_MODEL` | `anthropic/claude-haiku-4.5` | Model to use – see [openrouter.ai/models](https://openrouter.ai/models) |
| `AI_DOCUMENT_LANGUAGE` | `de` | Primary language of your documents (`de` or `en`) |

**What the AI does per scan:**
- Extracts: `topic`, `korrespondent` (sender/org), `dokumenttyp`, `tags`
- Looks up existing Paperless correspondents / document types – auto-creates if missing
- Renames output file: `20260410_181500_Stromrechnung_Stadtwerke.pdf`
- Falls back gracefully (log warning, original filename) on any failure

**Recommended models (cost/quality):**
- `anthropic/claude-haiku-4.5` — fast and cheap, good for German documents *(default)*
- `anthropic/claude-sonnet-4.5` — higher accuracy for complex layouts
- `google/gemini-flash-1.5` — very low cost alternative

---

## API Reference

All endpoints except `/health` require the `X-Api-Key` header.

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Service health check |
| `/scan/upload` | POST | Upload scan pages (multipart) for async OCR processing |
| `/scan/status/{job_id}` | GET | Poll job status and retrieve output metadata |

**Example upload:**
```bash
curl -X POST http://localhost:9000/scan/upload \
  -H "X-Api-Key: your-api-key" \
  -F "files=@page1.pnm" \
  -F "files=@page2.pnm"
# {"job_id": "550e8400-...", "status": "queued"}
```

**Example status poll:**
```bash
curl http://localhost:9000/scan/status/550e8400-... \
  -H "X-Api-Key: your-api-key"
# {"status": "done", "outputs": {"paperless_id": 42, ...}}
```

---

## rclone setup (without a local rclone installation)

The `rclone.conf` is mounted read-only into the container via `RCLONE_CONFIG_PATH`.

### Option 1 – Copy an existing config from another machine

```bash
scp ~/.config/rclone/rclone.conf user@server:/root/.config/rclone/rclone.conf
# Ensure RCLONE_CONFIG_PATH in .env matches the destination path
```

### Option 2 – Headless OAuth (OneDrive)

On a machine with a browser and rclone installed:

```bash
rclone authorize "onedrive"
# Follow browser prompt → copy the token JSON
```

Then run the interactive wizard inside the container:

```bash
docker compose run --rm --entrypoint bash ocr-api
# Inside container:
rclone config
# At "Use auto config?" → n → paste token
```

### Test the connection

```bash
docker compose exec ocr-api rclone lsd OneDrive:
docker compose exec ocr-api rclone copy /ocr-api/output/test.pdf OneDrive:scanner/
```

