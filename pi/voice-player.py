#!/usr/bin/env python3
"""Player della voce di Emiglio: path "voice" di MediaMTX -> speaker del ReSpeaker.

Perche' non GStreamer: lo stream arriva via TCP su Wi-Fi. Quando il Wi-Fi del Pi Zero 2 si blocca
(fino a ~1.5 s) i pacchetti arrivano poi a raffica, e il jitter buffer RTP di GStreamer li considera
"in ritardo" e li butta: la voce esce a pezzi. Qui invece non si scarta mai il parlato:

  ffmpeg (RTSP/TCP, decode Opus) -> coda -> aplay

  - si parte dopo TARGET di prebuffer;
  - se la coda si svuota (stallo di rete) si manda silenzio: Emiglio fa una pausa, non perde parole;
  - quando arriva la raffica la coda cresce: si recupera saltando solo i blocchi di SILENZIO (le
    pause tra le parole) finche' il ritardo torna al minimo;
  - taglio netto solo oltre HARD (raro).

Lanciato da play-voice.sh (a sua volta da MediaMTX, runOnAvailable). Variabili: RTSP_PORT, MTX_PATH,
OUT_DEV, JITTER_MS. Per i test: VOICE_URL e VOICE_SINK_CMD sostituiscono sorgente e aplay.
"""
import array
import collections
import os
import shlex
import signal
import subprocess
import sys
import threading
import time

RATE, CH = 48000, 2
CHUNK = RATE // 50 * CH * 2            # 20 ms, stereo, s16
SILENCE = bytes(CHUNK)
QUIET = 200                            # picco sotto cui un blocco e' "pausa" (la voce TTS e' pulita)


class Jitter:
    """Coda con prebuffer e recupero del ritardo sui silenzi. push() dal thread lettore, pull() dal main."""

    def __init__(self, target=10, resume=5, soft=15, hard=150):
        self.target, self.resume, self.soft, self.hard = target, resume, soft, hard
        self.dq = collections.deque()
        self.buffering, self.need = True, target
        self.skipped = self.cuts = self.underruns = 0

    def push(self, chunk):
        self.dq.append(chunk)

    @staticmethod
    def quiet(chunk):
        a = array.array("h")
        a.frombytes(chunk)
        return max(a) < QUIET and min(a) > -QUIET

    def pull(self):
        dq = self.dq
        n = len(dq)
        if self.buffering:
            if n < self.need:
                return SILENCE
            self.buffering = False
        if n == 0:
            self.buffering, self.need = True, self.resume
            self.underruns += 1
            return SILENCE
        if n > self.hard:
            for _ in range(n - self.target):
                dq.popleft()
            self.cuts += n - self.target
        chunk = dq.popleft()
        while len(dq) > self.soft and self.quiet(chunk):
            self.skipped += 1
            chunk = dq.popleft()
        return chunk


def main():
    url = os.environ.get("VOICE_URL") or "rtsp://localhost:%s/%s" % (
        os.environ.get("RTSP_PORT", "8554"), os.environ.get("MTX_PATH", "voice"))
    out_dev = os.environ.get("OUT_DEV", "plughw:CARD=seeed2micvoicec,DEV=0")
    target = max(2, int(os.environ.get("JITTER_MS", "200")) // 20)

    src_cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin",
               "-rtsp_transport", "tcp", "-timeout", "5000000", "-fflags", "nobuffer", "-flags", "low_delay",
               "-i", url, "-vn", "-ac", str(CH), "-ar", str(RATE), "-f", "s16le", "-"]
    sink_cmd = shlex.split(os.environ["VOICE_SINK_CMD"]) if os.environ.get("VOICE_SINK_CMD") else [
        "aplay", "-q", "-D", out_dev, "-t", "raw", "-f", "S16_LE", "-r", str(RATE), "-c", str(CH),
        "--buffer-time=120000"]

    src = subprocess.Popen(src_cmd, stdout=subprocess.PIPE, bufsize=CHUNK * 8)
    sink = subprocess.Popen(sink_cmd, stdin=subprocess.PIPE, bufsize=0)
    try:    # pipe verso aplay piccola: di default sono 64 KB = 340 ms di ritardo nascosto
        import fcntl
        fcntl.fcntl(sink.stdin.fileno(), 1031, 4096)      # F_SETPIPE_SZ
    except Exception:
        pass

    def stop(*_):
        for p in (src, sink):
            try:
                p.kill()
            except Exception:
                pass
        os._exit(0)

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)

    j = Jitter(target=target, resume=max(2, target // 2), soft=target + 5)
    alive = threading.Event()
    alive.set()

    def reader():
        try:
            while True:
                b = src.stdout.read(CHUNK)
                if len(b) < CHUNK:
                    break
                j.push(b)
        finally:
            alive.clear()

    threading.Thread(target=reader, daemon=True).start()
    print("voice-player: %s -> %s (prebuffer %d ms)" % (url, " ".join(sink_cmd[:3]), target * 20), flush=True)

    last = time.monotonic()
    try:
        # aplay consuma al ritmo del clock audio: la write bloccante fa da metronomo
        while alive.is_set() or j.dq:
            sink.stdin.write(j.pull())
            if time.monotonic() - last > 30:
                print("voice-player: coda %d ms | silenzi saltati %d ms | tagli %d ms | underrun %d  (ultimi 30 s)"
                      % (len(j.dq) * 20, j.skipped * 20, j.cuts * 20, j.underruns), flush=True)
                j.skipped = j.cuts = j.underruns = 0
                last = time.monotonic()
            if sink.poll() is not None:
                print("voice-player: il sink audio e' uscito (device busy?)", file=sys.stderr, flush=True)
                break
    except (BrokenPipeError, OSError):
        pass
    stop()


if __name__ == "__main__":
    main()
