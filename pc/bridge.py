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

Prerequisiti (una volta sola):
  - ffmpeg nel PATH
  - OBS Studio installato (serve solo per il driver della virtual camera, non va aperto)
  - VB-Cable installato (https://vb-audio.com/Cable/)
  - pip install -r requirements.txt

Uso:  python bridge.py [--host ronaldo.local] [--return "Cuffie (Oculus"] [--no-audio] [--no-video] [--list-devices]
"""
import argparse
import subprocess
import sys
import threading
import time

WIDTH, HEIGHT, FPS = 640, 480, 30
RATE, CHANNELS = 48000, 2
PROCS = set()   # ffmpeg figli, da uccidere all'uscita


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


def ffmpeg_base(url):
    return ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin",
            "-rtsp_transport", "tcp", "-fflags", "nobuffer", "-flags", "low_delay",
            "-i", url]


def video_loop(url, stop):
    import numpy as np
    import pyvirtualcam
    frame_bytes = WIDTH * HEIGHT * 3
    with pyvirtualcam.Camera(width=WIDTH, height=HEIGHT, fps=FPS, fmt=pyvirtualcam.PixelFormat.RGB) as cam:
        print(f"[video] webcam virtuale: {cam.device} ({WIDTH}x{HEIGHT}@{FPS})")
        while not stop.is_set():
            cmd = ffmpeg_base(url) + ["-an", "-vf", f"scale={WIDTH}:{HEIGHT}", "-pix_fmt", "rgb24",
                                      "-f", "rawvideo", "-"]
            p = spawn(cmd, stdout=subprocess.PIPE, bufsize=frame_bytes * 4)
            print("[video] connesso")
            try:
                while not stop.is_set():
                    buf = p.stdout.read(frame_bytes)
                    if len(buf) < frame_bytes:
                        break
                    cam.send(np.frombuffer(buf, dtype=np.uint8).reshape(HEIGHT, WIDTH, 3))
                    cam.sleep_until_next_frame()
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


def audio_loop(url, device_substr, stop):
    import sounddevice as sd
    idx, name = find_output_device(device_substr)
    if idx is None:
        print(f"[audio] device di output contenente '{device_substr}' non trovato. VB-Cable installato?")
        return
    print(f"[audio] scrivo su: {name}")
    chunk = 960 * CHANNELS * 2  # 20 ms
    with sd.RawOutputStream(device=idx, samplerate=RATE, channels=CHANNELS, dtype="int16",
                            blocksize=960, latency="low") as out:
        while not stop.is_set():
            cmd = ffmpeg_base(url) + ["-vn", "-ac", str(CHANNELS), "-ar", str(RATE),
                                      "-f", "s16le", "-"]
            p = spawn(cmd, stdout=subprocess.PIPE, bufsize=chunk * 8)
            print("[audio] connesso")
            try:
                while not stop.is_set():
                    buf = p.stdout.read(chunk)
                    if len(buf) < chunk:
                        break
                    out.write(buf)
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
        # 2. writer a ritmo costante
        next_t = time.perf_counter()
        empty_ticks = 0
        peak, last_report = 0, time.perf_counter()
        try:
            while not stop.is_set() and p.poll() is None:
                if q:
                    empty_ticks = 0
                    while q:
                        b = q.popleft()
                        peak = max(peak, int(np.abs(np.frombuffer(b, dtype=np.int16)).max(initial=0)))
                        p.stdin.write(b)
                # ogni 5 s: quanto audio sta arrivando dal dispositivo (0 = solo silenzio)
                if time.perf_counter() - last_report >= 5:
                    print(f"[ritorno] livello ultimi 5 s: {peak} {'(silenzio)' if peak < 50 else ''}")
                    peak, last_report = 0, time.perf_counter()
                else:
                    empty_ticks += 1
                    if empty_ticks > 2:   # gap reale, non jitter: riempio di silenzio
                        p.stdin.write(silence)
                next_t += 0.02
                d = next_t - time.perf_counter()
                if d > 0:
                    time.sleep(d)
                elif d < -0.5:
                    next_t = time.perf_counter()
        except (BrokenPipeError, OSError):
            pass
        finally:
            st.close()
            kill_tree(p)
        if not stop.is_set():
            print("[ritorno] publish interrotto (path occupato da un altro publisher?), riprovo tra 3 s")
            time.sleep(3)
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
    ap.add_argument("--no-audio", action="store_true")
    ap.add_argument("--no-video", action="store_true")
    ap.add_argument("--list-devices", action="store_true")
    args = ap.parse_args()

    if args.list_devices:
        list_devices()
        return

    url = f"rtsp://{args.host}:8554/{args.path}"
    print(f"Emiglio bridge: {url}  (Ctrl+C per uscire)")
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
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        print("chiudo...")
        kill_all()


if __name__ == "__main__":
    sys.exit(main())
