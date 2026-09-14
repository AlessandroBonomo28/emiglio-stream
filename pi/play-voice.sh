#!/bin/sh
# Legge il path "voice" da MediaMTX e lo riproduce sullo speaker del ReSpeaker.
# GStreamer: jitter buffer (latency) + PLC di Opus per assorbire il Wi-Fi.
# Lanciato da MediaMTX (runOnAvailable) quando qualcuno pubblica su "voice".

OUT_DEV="${OUT_DEV:-plughw:CARD=seeed2micvoicec,DEV=0}"
JITTER_MS="${JITTER_MS:-150}"
URL="rtsp://localhost:${RTSP_PORT:-8554}/${MTX_PATH:-voice}"

exec gst-launch-1.0 -e \
  rtspsrc location="$URL" protocols=tcp latency="$JITTER_MS" \
  ! rtpopusdepay ! opusdec plc=true \
  ! audioconvert ! audioresample \
  ! "audio/x-raw,rate=48000,channels=2" \
  ! alsasink device="$OUT_DEV" buffer-time=200000 latency-time=20000
