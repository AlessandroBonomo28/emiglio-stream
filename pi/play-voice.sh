#!/bin/sh
# Legge il path "voice" da MediaMTX e lo riproduce sullo speaker del ReSpeaker.
# Lanciato da MediaMTX (runOnReady) quando qualcuno pubblica su "voice".

OUT_DEV="${OUT_DEV:-plughw:CARD=seeed2micvoicec,DEV=0}"

exec ffmpeg -hide_banner -loglevel warning \
  -fflags nobuffer -flags low_delay -rtsp_transport tcp \
  -i "rtsp://localhost:${RTSP_PORT:-8554}/${MTX_PATH:-voice}" \
  -vn -ac 2 -ar 48000 -f alsa "$OUT_DEV"
