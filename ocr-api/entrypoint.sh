#!/bin/bash
set -e

# Generate msmtp config from environment variables (if SMTP_HOST is set)
if [[ -n "${SMTP_HOST:-}" ]]; then
    mkdir -p /ocr-api/.config/msmtp
    cat > /ocr-api/.config/msmtp/config <<EOF
defaults
auth           on
tls            on
tls_trust_file /etc/ssl/certs/ca-certificates.crt
logfile        /ocr-api/.config/msmtp/msmtp.log

account        default
host           ${SMTP_HOST}
port           ${SMTP_PORT:-587}
from           ${SMTP_FROM:-${SMTP_USER}}
user           ${SMTP_USER}
password       ${SMTP_PASSWORD}
EOF
    chmod 600 /ocr-api/.config/msmtp/config

    # Point mutt at msmtp as sendmail
    mkdir -p /ocr-api/.config/mutt
    cat > /ocr-api/.config/mutt/muttrc <<EOF
set sendmail="/usr/bin/msmtp"
set use_from=yes
set from="${SMTP_FROM:-${SMTP_USER}}"
EOF
    export MUTT_HOME=/ocr-api/.config/mutt
fi

exec "$@"
