#!/bin/sh
# Cattura webcam USB (YUYV 640x480) + mic ReSpeaker, encode H.264 hardware + Opus,
# e pubblica in RTSP su MediaMTX. Lanciato da MediaMTX (runOnInit), che esporta RTSP_PORT e MTX_PATH.

VIDEO_DEV="${VIDEO_DEV:-/dev/video0}"
AUDIO_DEV="${AUDIO_DEV:-plughw:CARD=seeed2micvoicec,DEV=0}"
FPS="${FPS:-20}"
VBITRATE="${VBITRATE:-1M}"

exec ffmpeg -hide_banner -loglevel warning \
  -f v4l2 -input_format yuyv422 -video_size 640x480 -framerate "$FPS" \
    -thread_queue_size 512 -i "$VIDEO_DEV" \
  -f alsa -thread_queue_size 1024 -channels 1 -sample_rate 16000 -i "$AUDIO_DEV" \
  -pix_fmt yuv420p -c:v h264_v4l2m2m -b:v "$VBITRATE" -g "$((FPS * 2))" -bf 0 \
  -c:a libopus -b:a 32k -application voip -ac 1 \
  -f rtsp -rtsp_transport tcp "rtsp://localhost:${RTSP_PORT:-8554}/${MTX_PATH:-emiglio}"
