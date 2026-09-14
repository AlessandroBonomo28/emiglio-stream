#!/usr/bin/env python3
"""Apre la pagina Emiglio Voice (pc/voice.html) nel browser.

Serve la cartella pc/ su http://localhost:<port> (contesto sicuro: il browser concede il microfono)
e apre il browser. L'audio va dal browser al Pi direttamente via WHIP: questo script non lo tocca.

Uso:  python voice.py [--host ronaldo.local] [--port 8765]
"""
import argparse
import http.server
import os
import webbrowser

HERE = os.path.dirname(os.path.abspath(__file__))


class Quiet(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=HERE, **kw)

    def log_message(self, *a):
        pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="ronaldo.local", help="hostname del Pi con MediaMTX")
    ap.add_argument("--port", type=int, default=8765, help="porta locale della pagina")
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", args.port), Quiet)
    srv.daemon_threads = True
    url = f"http://localhost:{args.port}/voice.html?host={args.host}"
    print(f"Emiglio Voice: {url}\nCtrl+C per chiudere.")
    if not args.no_browser:
        webbrowser.open(url)
    try:
        # serve_forever nel thread principale: su Windows Ctrl+C interrompe solo qui
        srv.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
        print("chiuso.")


if __name__ == "__main__":
    main()
