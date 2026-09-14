#!/bin/bash
# Installa MediaMTX + script + servizio sul Pi. Lanciare dalla cartella "pi/" con: sudo ./install.sh
set -euo pipefail
cd "$(dirname "$0")"

MTX_VER="${MTX_VER:-v1.21.0}"
ARCH=arm64

apt-get update
apt-get install -y --no-install-recommends ffmpeg curl openssl gstreamer1.0-tools gstreamer1.0-plugins-good gstreamer1.0-plugins-bad gstreamer1.0-alsa

if ! command -v mediamtx >/dev/null; then
  echo "== Scarico MediaMTX $MTX_VER"
  tmp=$(mktemp -d)
  curl -fsSL "https://github.com/bluenviron/mediamtx/releases/download/${MTX_VER}/mediamtx_${MTX_VER}_linux_${ARCH}.tar.gz" \
    | tar -xz -C "$tmp"
  install -m 755 "$tmp/mediamtx" /usr/local/bin/mediamtx
  rm -rf "$tmp"
fi

echo "== Config e script"
install -d /etc/mediamtx /opt/emiglio
install -m 644 mediamtx.yml /etc/mediamtx/mediamtx.yml
install -m 755 publish.sh publish-gst.sh play-voice.sh /opt/emiglio/

if [ ! -f /etc/mediamtx/server.crt ]; then
  echo "== Certificato self-signed per WebRTC (HTTPS)"
  openssl req -x509 -newkey rsa:2048 -nodes -days 3650 \
    -keyout /etc/mediamtx/server.key -out /etc/mediamtx/server.crt \
    -subj "/CN=$(hostname)" -addext "subjectAltName=DNS:$(hostname),DNS:$(hostname).local"
  chown pi:pi /etc/mediamtx/server.key /etc/mediamtx/server.crt
fi

install -m 644 systemd/mediamtx.service /etc/systemd/system/mediamtx.service
systemctl daemon-reload
systemctl enable --now mediamtx.service

echo
echo "Fatto. Controlla con:  journalctl -u mediamtx -f"
echo "Video+audio Emiglio:   https://$(hostname).local:8889/emiglio"
echo "Parla a Emiglio:       https://$(hostname).local:8889/voice/publish"
echo "RTSP per OBS:          rtsp://$(hostname).local:8554/emiglio"
