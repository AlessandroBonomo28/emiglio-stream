#!/bin/sh
# Riproduce il path "voice" di MediaMTX sullo speaker del ReSpeaker.
# Il lavoro lo fa voice-player.py (coda che non scarta mai il parlato e recupera il ritardo saltando
# le pause): vedi li' il perche'. Questo wrapper fa solo pulizia e attesa della scheda.
# Lanciato da MediaMTX (runOnAvailable) quando qualcuno pubblica su "voice".

export OUT_DEV="${OUT_DEV:-plughw:CARD=seeed2micvoicec,DEV=0}"
export JITTER_MS="${JITTER_MS:-200}"

# Player rimasti appesi da una sessione precedente terrebbero aperta la scheda audio e ogni nuovo
# player fallirebbe con "device busy" all'infinito: li elimino prima di partire.
pkill -9 -f "voice-player.py" 2>/dev/null
pkill -9 -f "gst-launch-1.0.*rtspsrc.*/${MTX_PATH:-voice}" 2>/dev/null

# Se la scheda sta ancora venendo rilasciata, aspetta (max 5 s)
PCM=/dev/snd/pcmC2D0p
for i in 1 2 3 4 5 6 7 8 9 10; do
  fuser "$PCM" >/dev/null 2>&1 || break
  sleep 0.5
done

exec python3 /opt/emiglio/voice-player.py
