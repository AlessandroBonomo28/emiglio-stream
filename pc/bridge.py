#!/usr/bin/env python3
"""Bridge: stream RTSP di Emiglio <-> webcam virtuale, microfono virtuale e ritorno audio del PC.

Andata (path "emiglio"):
  Video: ffmpeg decodifica l'RTSP e passa i frame a pyvirtualcam (driver "OBS Virtual Camera").
  Audio: ffmpeg decodifica l'audio e lo scrive sul device "CABLE Input" di VB-Cable;
         le app lo leggono come microfono "CABLE Output".
Ritorno (path "voice", opzionale con --return):
  Cattura in loopback WASAPI tutto cio' che il PC riproduce su un dispositivo di output a scelta
  (anche virtuale) e lo pubblica su MediaMTX; sul Pi play-voice.sh lo manda allo speaker di Emiglio.
  Per far parlare un'app a Emiglio: Impostazioni Windows > Sistema > Audio > Mixer volume >
  output dell'app = quel dispositivo.
Anti-eco half-duplex (--aec, insieme a --return):
  mentre il ritorno trasmette voce, e per --gate-hold secondi dopo (il giro di rete), il mic di
  Emiglio verso il PC viene messo a zero (mute) o attenuato (duck). Cosi' l'app non sente la propria
  voce rientrare dallo speaker di Emiglio. Non e' cancellazione d'eco: e' un interruttore, quindi
  mentre Emiglio parla e' sordo.

Prerequisiti (una volta sola):
  - ffmpeg nel PATH
  - OBS Studio installato (serve solo per il driver della virtual camera, non va aperto)
  - VB-Cable installato (https://vb-audio.com/Cable/)
  - pip install -r requirements.txt

Uso:  python bridge.py [--host ronaldo.local] [--return "Cuffie (Oculus"] [--aec] [--gate mute|duck]
                       [--gate-hold 1.5] [--no-audio] [--no-video] [--list-devices]
"""
import argparse
import subprocess
import sys
import threading
import time

WIDTH, HEIGHT, FPS = 640, 480, 30
RATE, CHANNELS = 48000, 2
PROCS = set()   # ffmpeg figli, da uccidere all'uscita

# Stato del gate anti-eco: istante (monotonic) in cui il ritorno ha trasmesso voce l'ultima volta.
GATE = {"last_voice": 0.0, "mode": "off", "hold": 1.5, "duck": 30.0}
# Watchdog del ritorno: se la scrittura verso ffmpeg resta bloccata (rete ferma, socket pieno),
# il main loop uccide ffmpeg e il thread si riconnette invece di restare appeso per minuti.
RETURN_WD = {"t": 0.0, "p": None}
VOICE_PEAK = 300   # ~ -41 dBFS: sopra e' voce, sotto e' il silenzio del keepalive


def spawn(cmd, **kw):
    p = subprocess.Popen(cmd, **kw)
    PROCS.add(p)
    return p


def kill_tree(p):
    """Uccide ffmpeg e i suoi figli: su Windows 'ffmpeg' puo' essere uno shim (es. Chocolatey)
    che lancia il vero ffmpeg.exe come processo figlio, e p.kill() da solo lo lascerebbe vivo."""
    PROCS.discard(p)
    if p.poll() is not None:
        return
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        p.kill()
    except Exception:
        pass


def kill_all():
    for p in list(PROCS):
        kill_tree(p)


def ffmpeg_base(url, media):
    """Lettore RTSP. media = "video" | "audio": ogni connessione si abbona solo alla traccia che le
    serve (l'audio da solo sono 32 kbit/s e regge anche con poca banda; senza, ogni connessione
    scaricherebbe anche il video). -timeout: se la rete si blocca per 5 s ffmpeg esce e il loop si
    riconnette, invece di restare appeso su una connessione morta."""
    return ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin",
            "-rtsp_transport", "tcp", "-timeout", "5000000", "-allowed_media_types", media,
            "-fflags", "nobuffer", "-flags", "low_delay", "-i", url]


def video_loop(url, stop):
    import numpy as np
    import pyvirtualcam
    frame_bytes = WIDTH * HEIGHT * 3
    with pyvirtualcam.Camera(width=WIDTH, height=HEIGHT, fps=FPS, fmt=pyvirtualcam.PixelFormat.RGB) as cam:
        print(f"[video] webcam virtuale: {cam.device} ({WIDTH}x{HEIGHT}@{FPS})")
        while not stop.is_set():
            cmd = ffmpeg_base(url, "video") + ["-an", "-vf", f"scale={WIDTH}:{HEIGHT}", "-pix_fmt", "rgb24",
                                      "-f", "rawvideo", "-"]
            p = spawn(cmd, stdout=subprocess.PIPE, bufsize=frame_bytes * 4)
            print("[video] connesso")
            try:
                while not stop.is_set():
                    buf = p.stdout.read(frame_bytes)
                    if len(buf) < frame_bytes:
                        break
                    # Nessuna pausa tra i frame: si consuma alla velocita' con cui arrivano. Con una
                    # pausa fissa, dopo ogni singhiozzo di rete l'arretrato non verrebbe mai smaltito
                    # e il ritardo crescerebbe nel tempo.
                    cam.send(np.frombuffer(buf, dtype=np.uint8).reshape(HEIGHT, WIDTH, 3))
            finally:
                kill_tree(p)
            if not stop.is_set():
                print("[video] stream interrotto, riconnetto tra 2 s")
                time.sleep(2)


def find_output_device(substr):
    import sounddevice as sd
    for i, d in enumerate(sd.query_devices()):
        if substr.lower() in d["name"].lower() and d["max_output_channels"] >= 1:
            return i, d["name"]
    return None, None


def gate_apply(buf, np):
    """Applica il gate al blocco del mic di Emiglio in andata. Ritorna (buf, gate_chiuso)."""
    if GATE["mode"] == "off":
        return buf, False
    closed = (time.monotonic() - GATE["last_voice"]) < GATE["hold"]
    if not closed:
        return buf, False
    if GATE["mode"] == "mute":
        return bytes(len(buf)), True
    a = np.frombuffer(buf, dtype=np.int16).astype(np.float32) * (10 ** (-GATE["duck"] / 20))
    return a.astype(np.int16).tobytes(), True


def audio_loop(url, device_substr, stop):
    import collections
    import numpy as np
    import sounddevice as sd
    # Jitter buffer, in blocchi da 20 ms. Il Wi-Fi del Pi Zero 2 ha stalli fino a ~1.5 s (TCP che
    # ritrasmette), dopo i quali i dati arrivano a raffica:
    #  - senza limiti la raffica resta in coda per sempre  -> ritardo che cresce nel tempo;
    #  - con un tetto rigido la raffica viene tagliata     -> audio a singhiozzo.
    # Quindi: si parte dopo TARGET di prebuffer, e quando la coda supera SOFT si recupera saltando
    # solo i blocchi di SILENZIO (le pause tra le parole), mai il parlato. Taglio netto solo oltre HARD.
    TARGET, RESUME, SOFT, HARD = 10, 5, 15, 150   # 200 ms, 100 ms, 300 ms, 3 s
    idx, name = find_output_device(device_substr)
    if idx is None:
        print(f"[audio] device di output contenente '{device_substr}' non trovato. VB-Cable installato?")
        return
    print(f"[audio] scrivo su: {name}")
    chunk = 960 * CHANNELS * 2  # 20 ms
    with sd.RawOutputStream(device=idx, samplerate=RATE, channels=CHANNELS, dtype="int16",
                            blocksize=960, latency="low") as out:
        while not stop.is_set():
            cmd = ffmpeg_base(url, "audio") + ["-vn", "-ac", str(CHANNELS), "-ar", str(RATE),
                                      "-f", "s16le", "-"]
            p = spawn(cmd, stdout=subprocess.PIPE, bufsize=chunk * 8)
            print("[audio] connesso")
            # La riproduzione va in tempo reale, quindi dopo uno stallo di rete i dati arrivano a
            # raffica e resterebbero in coda per sempre: ritardo che cresce a ogni singhiozzo.
            # Un thread svuota ffmpeg appena i dati arrivano; qui si tengono solo gli ultimi
            # MAX_BACKLOG blocchi e il resto si scarta, cosi' il ritardo torna sempre al minimo.
            dq = collections.deque()
            alive = threading.Event()
            alive.set()

            def reader(proc=p, dq=dq, alive=alive):
                try:
                    while not stop.is_set():
                        b = proc.stdout.read(chunk)
                        if len(b) < chunk:
                            break
                        dq.append(b)
                finally:
                    alive.clear()

            threading.Thread(target=reader, daemon=True).start()
            was_closed = False
            peaks = collections.deque(maxlen=250)     # picchi degli ultimi 5 s, per stimare il fondo
            buffering, need = True, TARGET
            skipped = cuts = underruns = 0
            last_rep = time.monotonic()
            try:
                while not stop.is_set() and (alive.is_set() or dq):
                    n = len(dq)
                    if buffering:
                        # Si aspetta il prebuffer solo finche' il lettore e' vivo: se ffmpeg e' uscito
                        # non arrivera' altro, e restare ad aspettare con la coda non vuota vorrebbe
                        # dire non uscire mai dal ciclo (niente riconnessione, silenzio sul CABLE).
                        if n < need and alive.is_set():
                            time.sleep(0.005)
                            continue
                        buffering = False
                    if n == 0:                        # coda vuota: si ricarica un minimo e si riparte
                        buffering, need = True, RESUME
                        underruns += 1
                        continue
                    if n > HARD:                      # arretrato enorme: taglio netto (raro)
                        for _ in range(n - TARGET):
                            dq.popleft()
                        cuts += n - TARGET
                    buf = dq.popleft()
                    pk = int(np.abs(np.frombuffer(buf, dtype=np.int16)).max(initial=0))
                    peaks.append(pk)
                    if len(dq) > SOFT and len(peaks) >= 50:
                        floor = float(np.percentile(peaks, 20))
                        if pk <= max(400.0, 2.5 * floor):
                            skipped += 1              # pausa tra le parole: si salta per recuperare
                            continue
                    buf, closed = gate_apply(buf, np)
                    if closed != was_closed:
                        print("[gate] mic di Emiglio " + ("CHIUSO (Emiglio sta parlando)" if closed else "aperto"))
                        was_closed = closed
                    out.write(buf)
                    if time.monotonic() - last_rep > 30:
                        print(f"[audio] coda {len(dq) * 20} ms | silenzi saltati {skipped * 20} ms | "
                              f"tagli {cuts * 20} ms | underrun {underruns}   (ultimi 30 s)")
                        skipped = cuts = underruns = 0
                        last_rep = time.monotonic()
            finally:
                kill_tree(p)
            if not stop.is_set():
                print("[audio] stream interrotto, riconnetto tra 2 s")
                time.sleep(2)


def find_loopback(substr):
    import pyaudiowpatch as pyaudio
    pa = pyaudio.PyAudio()
    for d in pa.get_loopback_device_info_generator():
        if substr.lower() in d["name"].lower():
            return pa, d
    pa.terminate()
    return None, None


def find_render_device(substr):
    """Indice sounddevice del dispositivo di OUTPUT (preferendo WASAPI, che ha i nomi completi)."""
    import sounddevice as sd
    devs = sd.query_devices()
    apis = sd.query_hostapis()
    best = None
    for i, d in enumerate(devs):
        if substr.lower() in d["name"].lower() and d["max_output_channels"] >= 1:
            if "WASAPI" in apis[d["hostapi"]]["name"]:
                return i
            best = best if best is not None else i
    return best


def return_loop(host, path, device_substr, stop, gain_db=0.0):
    """Loopback WASAPI del dispositivo di output scelto -> Opus -> RTSP publish su MediaMTX.

    Due accorgimenti perche' il loopback di Windows consegna campioni solo mentre qualcuno
    riproduce sul dispositivo:
      1. il bridge stesso riproduce silenzio in continuo su quel dispositivo (keepalive);
      2. un thread con orologio proprio scrive a ffmpeg 20 ms ogni 20 ms, zeri se non e' arrivato
         nulla, cosi' la sessione RTSP resta viva dall'avvio e nel silenzio.
    """
    import collections
    import numpy as np
    import pyaudiowpatch as pyaudio
    import sounddevice as sd

    pa, dev = find_loopback(device_substr)
    if dev is None:
        print(f"[ritorno] nessun dispositivo di output contenente '{device_substr}'. Vedi --list-devices")
        return
    rate, ch = int(dev["defaultSampleRate"]), dev["maxInputChannels"]
    url = f"rtsp://{host}:8554/{path}"
    print(f"[ritorno] catturo: {dev['name']} ({rate} Hz, {ch} ch) -> {url}")

    # 1. keepalive: silenzio continuo sul dispositivo
    ridx = find_render_device(device_substr)
    keep = None
    if ridx is not None:
        keep = sd.OutputStream(device=ridx, samplerate=rate, channels=2, dtype="int16",
                               callback=lambda out, n, t, st: out.fill(0))
        keep.start()
        print(f"[ritorno] keepalive attivo su: {sd.query_devices(ridx)['name']}")
    else:
        print("[ritorno] keepalive non disponibile: il dispositivo di render non e' stato trovato")

    frames = int(rate * 0.02)  # 20 ms
    q = collections.deque()

    def on_capture(in_data, n, t, status):
        a = np.frombuffer(in_data, dtype=np.int16)
        if ch > 1:
            a = a.reshape(-1, ch).mean(axis=1).astype(np.int16)
        q.append(a.tobytes())
        return (None, pyaudio.paContinue)

    silence = bytes(frames * 2)
    while not stop.is_set():
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin",
               "-f", "s16le", "-ar", str(rate), "-ac", "1", "-i", "-"]
        if gain_db:
            cmd += ["-af", f"volume={gain_db}dB"]
        cmd += ["-c:a", "libopus", "-b:a", "32k", "-application", "voip",
                "-f", "rtsp", "-rtsp_transport", "tcp", url]
        p = spawn(cmd, stdin=subprocess.PIPE)
        q.clear()
        st = pa.open(format=pyaudio.paInt16, channels=ch, rate=rate, input=True,
                     input_device_index=dev["index"], frames_per_buffer=frames,
                     stream_callback=on_capture)
        print("[ritorno] in onda")
        RETURN_WD.update(t=time.monotonic(), p=p)
        # 2. writer a ritmo costante
        next_t = time.perf_counter()
        empty_ticks = 0
        peak, last_report = 0, time.perf_counter()
        try:
            while not stop.is_set() and p.poll() is None:
                if q:
                    empty_ticks = 0
                    while len(q) > 75:      # arretrato dopo uno stallo lungo: tieni gli ultimi 1.5 s
                        q.popleft()
                    while q:
                        b = q.popleft()
                        bp = int(np.abs(np.frombuffer(b, dtype=np.int16)).max(initial=0))
                        peak = max(peak, bp)
                        if bp > VOICE_PEAK:
                            GATE["last_voice"] = time.monotonic()
                        p.stdin.write(b)
                        RETURN_WD["t"] = time.monotonic()
                # ogni 5 s: quanto audio sta arrivando dal dispositivo (0 = solo silenzio)
                if time.perf_counter() - last_report >= 5:
                    print(f"[ritorno] livello ultimi 5 s: {peak} {'(silenzio)' if peak < 50 else ''}")
                    peak, last_report = 0, time.perf_counter()
                else:
                    empty_ticks += 1
                    if empty_ticks > 2:   # gap reale, non jitter: riempio di silenzio
                        p.stdin.write(silence)
                        RETURN_WD["t"] = time.monotonic()
                next_t += 0.02
                d = next_t - time.perf_counter()
                if d > 0:
                    time.sleep(d)
                elif d < -0.5:
                    next_t = time.perf_counter()
        except (BrokenPipeError, OSError):
            pass
        finally:
            RETURN_WD["p"] = None
            st.close()
            kill_tree(p)
        if not stop.is_set():
            print("[ritorno] publish interrotto (rete o path ancora occupato), riprovo tra 2 s")
            time.sleep(2)
    if keep:
        keep.stop(); keep.close()
    pa.terminate()


def list_devices():
    import sounddevice as sd
    import pyaudiowpatch as pyaudio
    print("== Dispositivi di OUTPUT catturabili in loopback (per --return):")
    pa = pyaudio.PyAudio()
    for d in pa.get_loopback_device_info_generator():
        print("  -", d["name"].replace(" [Loopback]", ""))
    pa.terminate()
    print("\n== Tutti i dispositivi audio (per --audio-device):")
    print(sd.query_devices())


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="ronaldo.local")
    ap.add_argument("--path", default="emiglio")
    ap.add_argument("--audio-device", default="CABLE Input", help="sottostringa del nome del device di output")
    ap.add_argument("--return", dest="ret", metavar="DEVICE",
                    help="sottostringa del dispositivo di OUTPUT da catturare e mandare allo speaker di Emiglio")
    ap.add_argument("--return-path", default="voice")
    ap.add_argument("--return-gain", type=float, default=0.0, metavar="DB",
                    help="guadagno in dB sul ritorno (es. 6 per alzare, -6 per abbassare)")
    ap.add_argument("--aec", action="store_true",
                    help="attiva l'anti-eco: mentre il ritorno trasmette, il mic di Emiglio verso il PC "
                         "viene chiuso (vedi --gate). Richiede --return")
    ap.add_argument("--gate", choices=["mute", "duck"], default="mute",
                    help="con --aec: mic azzerato (mute, default) o attenuato di --gate-duck-db (duck)")
    ap.add_argument("--gate-hold", type=float, default=1.5, metavar="S",
                    help="secondi di gate dopo l'ultima voce sul ritorno (deve coprire il giro di rete)")
    ap.add_argument("--gate-duck-db", type=float, default=30.0, metavar="DB",
                    help="attenuazione per --gate duck")
    ap.add_argument("--no-audio", action="store_true")
    ap.add_argument("--no-video", action="store_true")
    ap.add_argument("--list-devices", action="store_true")
    args = ap.parse_args()

    if args.list_devices:
        list_devices()
        return

    url = f"rtsp://{args.host}:8554/{args.path}"
    print(f"Emiglio bridge: {url}  (Ctrl+C per uscire)")
    if args.aec and not args.ret:
        print("--aec richiede --return (senza ritorno non c'e' eco da gestire)")
        return 2
    if args.aec:
        GATE.update(mode=args.gate, hold=args.gate_hold, duck=args.gate_duck_db)
        print(f"[aec] anti-eco attivo: {args.gate}, hold {args.gate_hold}s"
              + (f", -{args.gate_duck_db:g} dB" if args.gate == "duck" else ""))
    else:
        print("[aec] anti-eco spento (aggiungi --aec)")
    stop = threading.Event()
    threads = []
    if not args.no_video:
        threads.append(threading.Thread(target=video_loop, args=(url, stop), daemon=True))
    if not args.no_audio:
        threads.append(threading.Thread(target=audio_loop, args=(url, args.audio_device, stop), daemon=True))
    if args.ret:
        threads.append(threading.Thread(target=return_loop, args=(args.host, args.return_path, args.ret, stop, args.return_gain), daemon=True))
    for t in threads:
        t.start()
    try:
        while any(t.is_alive() for t in threads):
            time.sleep(0.5)
            wp = RETURN_WD["p"]
            if wp is not None and time.monotonic() - RETURN_WD["t"] > 6:
                print("[ritorno] bloccato da 6 s (rete ferma?): forzo la riconnessione")
                RETURN_WD["p"] = None
                kill_tree(wp)
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        print("chiudo...")
        kill_all()


if __name__ == "__main__":
    sys.exit(main())
