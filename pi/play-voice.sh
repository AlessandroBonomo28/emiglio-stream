#!/bin/sh
# Legge il path "voice" da MediaMTX e lo riproduce sullo speaker del ReSpeaker.
# GStreamer: jitter buffer (latency) + PLC di Opus per assorbire il Wi-Fi.
# Lanciato da MediaMTX (runOnAvailable) quando qualcuno pubblica su "voice".

OUT_DEV="${OUT_DEV:-plughw:CARD=seeed2micvoicec,DEV=0}"
JITTER_MS="${JITTER_MS:-250}"
URL="rtsp://localhost:${RTSP_PORT:-8554}/${MTX_PATH:-voice}"

# Player rimasti appesi da una sessione precedente (es. Wi-Fi caduto) terrebbero aperta la scheda
# audio e ogni nuovo player fallirebbe con "device busy" all'infinito: li elimino prima di partire.
pkill -9 -f "gst-launch-1.0.*rtspsrc.*/${MTX_PATH:-voice}" 2>/dev/null

# Se il player precedente sta ancora rilasciando la scheda audio, aspetta (max 5 s)
PCM=/dev/snd/pcmC2D0p
for i in 1 2 3 4 5 6 7 8 9 10; do
  fuser "$PCM" >/dev/null 2>&1 || break
  sleep 0.5
done

# Niente "-e": alla chiusura aspetterebbe un EOS che con la sorgente morta non arriva mai,
# e il processo resterebbe appeso con la scheda audio aperta.
# tcp-timeout/timeout (microsecondi): se lo stream si blocca, esce e MediaMTX lo rilancia.
exec gst-launch-1.0 \
  rtspsrc location="$URL" protocols=tcp latency="$JITTER_MS" tcp-timeout=5000000 timeout=5000000 \
  ! rtpopusdepay ! opusdec plc=true \
  ! audioconvert ! audioresample \
  ! "audio/x-raw,rate=48000,channels=2" \
  ! alsasink device="$OUT_DEV" buffer-time=200000 latency-time=20000
