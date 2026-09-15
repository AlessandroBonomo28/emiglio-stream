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

**Ritorno audio verso Emiglio.** Con `--return "<dispositivo>"` il bridge cattura in loopback tutto
quello che il PC riproduce su quel dispositivo di output (anche virtuale) e lo manda allo speaker di
Emiglio tramite il path `voice`. Per far parlare un'app a Emiglio, in Windows: Impostazioni →
Sistema → Audio → Mixer volume → output dell'app = quel dispositivo. Esempio con un output
virtuale inutilizzato:

```bash
python pc/bridge.py --return "Cuffie (Oculus"
```

`--list-devices` elenca i dispositivi catturabili. Il ritorno e la pagina voce usano lo stesso
path `voice`, quindi uno alla volta.

Opzioni: `--host <pi>`, `--return <dispositivo>`, `--return-gain <dB>` (alza o abbassa il ritorno),
`--no-audio`, `--no-video`, `--audio-device <sottostringa>`, `--list-devices`. Il bridge riconnette
da solo se lo stream cade e stampa ogni 5 s il livello catturato sul ritorno.

Se Windows abbassa il volume dell'app che parla a Emiglio (attenuazione "comunicazioni"), Pannello di
controllo → Audio → Comunicazioni → "Non intervenire".

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

## Cancellazione d'eco (AEC): provata, NON funziona

I mic del ReSpeaker stanno a pochi cm dallo speaker: tutto quello che Emiglio dice rientra nel suo
microfono. Con un assistente vocale (MiniCPM-o) il risultato è che risponde a se stesso.

Nel ramo `dev-AEC` c'è un tentativo completo con il modulo `echo-cancel` di PipeWire (motore WebRTC)
sul Pi. In laboratorio cancellava 21-27 dB di eco (misura in `pi/aec-test.sh` di quel ramo), ma
**in uso reale con MiniCPM non ha mai funzionato**: la voce arrivava disturbata o troppo bassa e il
modello non rispondeva. Il ramo è lasciato per riferimento, `master` non lo usa. Lezioni imparate,
se qualcuno ci riprova:

- lo speaker del ReSpeaker è sul canale **destro**: un modulo mono si aggancia solo al sinistro (silenzio);
- l'AGC di WebRTC va tenuto **spento**: amplifica l'eco residuo (da 21 dB di cancellazione a 11);
- il driver Seeed mette 59 dB di guadagno in ingresso: il mic satura e l'AEC non può lavorare;
- sul Pi Zero 2 serve un quantum minimo di 1024 campioni, altrimenti xrun e audio che gracchia;
- `pulsesrc` con un device inesistente ripiega in silenzio sul mic della webcam;
- il playback del modulo esce solo se il lato capture sta girando (non e' un bug, e' by design);
- WirePlumber, quando adotta la scheda, può riportare il volume dello speaker a un valore memorizzato
  (23%): se Emiglio suona pianissimo, `wpctl set-volume <id sink> 1.0`.

Alternativa semplice mai implementata: gate half-duplex nel bridge sul PC (mic di Emiglio muto
mentre il ritorno trasmette). Zero calcolo sul Pi, ma niente interruzioni a voce.

## Note

- WebRTC è in HTTPS con certificato self-signed generato da `install.sh`: il browser lo richiede per dare accesso al microfono.
- Lo speaker del ReSpeaker è condiviso con `bt-speaker.service` (Bluetooth) e col soundboard: su `plughw` l'accesso è esclusivo, quindi voce e Bluetooth non suonano insieme. Soluzione: `dmix` in `~/.asoundrc`.
- PipeWire gira sul Pi ma la scheda del ReSpeaker viene usata in ALSA diretto: PipeWire la rilascia quando è inattivo. Se un player fallisce con "device busy", `sudo fuser -v /dev/snd/*` dice chi la tiene.
- Non usare `!!` nei comandi bash sul Pi: bash lo sostituisce con l'ultimo comando.
