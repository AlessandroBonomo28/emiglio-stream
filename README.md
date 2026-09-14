# emiglio-stream

Stream bidirezionale video/audio tra il Raspberry Pi di Emiglio e il PC.

## Hardware

| Componente | Modello | Note |
|---|---|---|
| SBC | Raspberry Pi Zero 2 W (aarch64, Debian 13 trixie) | encoder H.264 hardware via V4L2 M2M |
| Webcam | Trust Webcam USB | solo YUYV raw, 640x480 max 30 fps |
| Audio | ReSpeaker 2-Mics Pi HAT | ALSA `plughw:CARD=seeed2micvoicec,DEV=0`, mic + speaker |

## Architettura

```
Pi:  webcam USB (YUYV) ─► v4l2convert (ISP) ─► v4l2h264enc (VideoCore) ─┐
     ReSpeaker mic ────► opusenc ───────────────────────────────────────┴─► MPEG-TS/UDP ─► MediaMTX
                                                                                    path "emiglio"
                                                                                     ├─► browser (WebRTC)
                                                                                     └─► PC OBS (RTSP)

PC:  browser (WHIP) o publish-mic.ps1 ─► MediaMTX path "voice" ─► GStreamer ─► ReSpeaker speaker
```

MediaMTX fa da hub: un path per Emiglio in uscita, uno per la voce in entrata.
Tutto gira in un solo servizio systemd (`mediamtx.service`), che lancia e riavvia gli script.

## Pi

```bash
git clone https://github.com/AlessandroBonomo28/emiglio-stream.git
cd emiglio-stream/pi && sudo ./install.sh
```

- `https://ronaldo.local:8889/emiglio` — vedi/ascolti Emiglio (accetta il certificato self-signed)
- `https://ronaldo.local:8889/voice/publish` — parli a Emiglio dal browser
- `rtsp://ronaldo.local:8554/emiglio` — stream per OBS / ffmpeg
- Log: `journalctl -u mediamtx -f`
- Tuning: variabili `FPS`, `VBITRATE` in `pi/publish.sh` (default 30 fps, 1 Mbit/s)

File:

- `pi/mediamtx.yml` — config MediaMTX, installata in `/etc/mediamtx/`
- `pi/publish.sh` — GStreamer: webcam + mic → H.264/Opus → MPEG-TS su UDP locale
- `pi/play-voice.sh` — GStreamer: path `voice` → speaker ReSpeaker (jitter buffer 150 ms)
- `pi/systemd/mediamtx.service` — servizio
- `pi/install.sh` — installa dipendenze, MediaMTX, script, certificato, servizio

## PC (virtual cam + virtual mic)

Emiglio come webcam e microfono del PC, per darlo in pasto a MiniCPM-o, Discord, Teams, ecc.

Prerequisiti, una volta sola:

1. **ffmpeg** nel PATH.
2. **OBS Studio** installato: serve solo il suo driver "OBS Virtual Camera", OBS non va aperto.
3. **[VB-Cable](https://vb-audio.com/Cable/)** installato (installer come amministratore, poi riavvia).
4. `pip install -r pc/requirements.txt`

Poi:

```bash
python pc/bridge.py
```

Nell'app scegli **OBS Virtual Camera** come webcam e **CABLE Output** come microfono. In Chrome,
se l'app non ha un menu, imposta i default in `chrome://settings/content/camera` e
`chrome://settings/content/microphone`. Chrome enumera i device all'avvio: riavvialo dopo aver
installato i driver.

Opzioni: `--host <pi>`, `--no-audio`, `--no-video`, `--audio-device <sottostringa>`, `--list-devices`.
Il bridge riconnette da solo se lo stream cade.

## PC (parlare a Emiglio con effetti voce)

```bash
python pc/voice.py
```

Apre `http://localhost:8765/voice.html` nel browser: scegli il microfono, attiva gli effetti
(pitch, robot, walkie-talkie, distorsione, coro, caverna) e premi **Avvia**. La pagina processa
l'audio con Tone.js e lo pubblica via WHIP su `https://ronaldo.local:8889/voice/whip`; il Python
serve solo la pagina, l'audio va dal browser al Pi direttamente. Gli effetti si accendono e
spengono a caldo. Opzioni: `--host <pi>`, `--port <n>`.

Al primo uso apri `https://ronaldo.local:8889` e accetta il certificato self-signed, altrimenti
il browser rifiuta la connessione WHIP.

Senza effetti e senza browser: `pc/publish-mic.ps1 -Mic "<nome device dshow>"`.

## Note

- WebRTC è in HTTPS con certificato self-signed generato da `install.sh`: il browser lo richiede per dare accesso al microfono.
- Lo speaker del ReSpeaker è condiviso con `bt-speaker.service` (Bluetooth) e col soundboard: su `plughw` l'accesso è esclusivo, quindi voce e Bluetooth non suonano insieme. Soluzione: `dmix` in `~/.asoundrc`.
- Nessuna cancellazione d'eco: se ti senti tornare indietro, abbassa il volume dello speaker o mutati mentre parla lui.
