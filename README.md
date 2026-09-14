# emgilio-stream

Stream bidirezionale video/audio tra il Raspberry Pi Zero 2 W di Emiglio e il PC.

```
Pi:  webcam USB (YUYV 640x480) ─┐
                                ├─ ffmpeg (h264_v4l2m2m + Opus) ─RTSP─► MediaMTX ─┬─► browser (WebRTC/WHEP)
     ReSpeaker mic ─────────────┘                        path "emiglio"           └─► PC OBS (RTSP)

PC:  browser (WHIP) o publish-mic.ps1 ─RTSP─► MediaMTX path "voice" ─► ffmpeg ─► ReSpeaker speaker
```

## Pi

```bash
git clone <repo> && cd emgilio-stream/pi && sudo ./install.sh
```

- `https://ronaldo.local:8889/emiglio` — vedi/ascolti Emiglio (accetta il certificato self-signed)
- `https://ronaldo.local:8889/voice/publish` — parli a Emiglio dal browser
- `rtsp://ronaldo.local:8554/emiglio` — stream per OBS / ffmpeg
- Log: `journalctl -u mediamtx -f`
- Tuning: variabili `FPS`, `VBITRATE` in `publish.sh` (default 20 fps, 1 Mbit/s)

## PC (virtual cam + virtual mic)

1. Installa OBS e [VB-Cable](https://vb-audio.com/Cable/).
2. OBS → Sorgenti → **Media Source**: input `rtsp://ronaldo.local:8554/emiglio`, disattiva buffering, spunta "Restart playback when source becomes active".
3. OBS → **Start Virtual Camera**.
4. OBS → Impostazioni → Audio → Monitoring Device: **CABLE Input**. Nel mixer audio della Media Source imposta "Monitor and Output".
5. Nell'app di destinazione scegli **OBS Virtual Camera** come webcam e **CABLE Output** come microfono.

Per parlare a Emiglio senza browser: `pc/publish-mic.ps1 -Mic "<nome device dshow>"`.

## Note

- Solo il ReSpeaker può stare aperto da un processo alla volta su `plughw`. Se gira anche il soundboard con `aplay`,
  uno dei due deve usare `dmix` (o il soundboard passa per `voice`).
- Nessuna cancellazione d'eco: se ti senti tornare indietro, abbassa il volume dello speaker o mutati mentre parla lui.
