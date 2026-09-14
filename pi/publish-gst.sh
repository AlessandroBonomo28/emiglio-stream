#!/bin/sh
# Versione GStreamer di publish.sh: la conversione YUYV -> I420 la fa l'ISP hardware (v4l2convert)
# e l'encode H.264 il VideoCore (v4l2h264enc). CPU quasi a zero.
# Output: MPEG-TS su UDP locale, letto da MediaMTX (path con source: udp://127.0.0.1:5004).
# Richiede: gstreamer1.0-tools gstreamer1.0-plugins-good gstreamer1.0-plugins-bad gstreamer1.0-alsa
# Lanciato da MediaMTX (runOnInit).

VIDEO_DEV="${VIDEO_DEV:-/dev/video0}"
AUDIO_DEV="${AUDIO_DEV:-plughw:CARD=seeed2micvoicec,DEV=0}"
FPS="${FPS:-30}"
VBITRATE="${VBITRATE:-1000000}"   # bit/s
UDP_PORT="${UDP_PORT:-5004}"

exec gst-launch-1.0 -e \
  mpegtsmux name=mux ! queue ! udpsink host=127.0.0.1 port="$UDP_PORT" sync=false \
  v4l2src device="$VIDEO_DEV" \
    ! "video/x-raw,format=YUY2,width=640,height=480,framerate=${FPS}/1" \
    ! v4l2convert \
    ! "video/x-raw,format=I420" \
    ! v4l2h264enc extra-controls="controls,video_bitrate=${VBITRATE},h264_i_frame_period=${FPS},repeat_sequence_header=1" \
    ! "video/x-h264,level=(string)4" \
    ! h264parse config-interval=1 \
    ! queue ! mux. \
  alsasrc device="$AUDIO_DEV" \
    ! audioconvert ! audioresample \
    ! "audio/x-raw,rate=48000,channels=1" \
    ! opusenc bitrate=32000 audio-type=voice frame-size=20 \
    ! opusparse \
    ! queue ! mux.
