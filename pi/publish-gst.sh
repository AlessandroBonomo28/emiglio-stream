#!/bin/sh
# Versione GStreamer di publish.sh: la conversione YUYV -> I420 la fa l'ISP hardware (v4l2convert)
# e l'encode H.264 il VideoCore (v4l2h264enc). CPU quasi a zero.
# Richiede: gstreamer1.0-tools gstreamer1.0-plugins-good gstreamer1.0-plugins-bad gstreamer1.0-alsa gstreamer1.0-rtsp
# Lanciato da MediaMTX (runOnInit), che esporta RTSP_PORT e MTX_PATH.

VIDEO_DEV="${VIDEO_DEV:-/dev/video0}"
AUDIO_DEV="${AUDIO_DEV:-plughw:CARD=seeed2micvoicec,DEV=0}"
FPS="${FPS:-30}"
VBITRATE="${VBITRATE:-1000000}"   # bit/s
URL="rtsp://localhost:${RTSP_PORT:-8554}/${MTX_PATH:-emiglio}"

exec gst-launch-1.0 -e \
  rtspclientsink name=sink location="$URL" protocols=tcp latency=0 \
  v4l2src device="$VIDEO_DEV" io-mode=dmabuf \
    ! "video/x-raw,format=YUY2,width=640,height=480,framerate=${FPS}/1" \
    ! v4l2convert output-io-mode=dmabuf-import \
    ! "video/x-raw,format=I420" \
    ! v4l2h264enc extra-controls="controls,video_bitrate=${VBITRATE},h264_i_frame_period=$((FPS * 2)),repeat_sequence_header=1,h264_profile=1,h264_level=11" \
    ! "video/x-h264,level=(string)4" \
    ! h264parse config-interval=1 \
    ! queue ! sink. \
  alsasrc device="$AUDIO_DEV" \
    ! audioconvert ! audioresample \
    ! "audio/x-raw,rate=48000,channels=1" \
    ! opusenc bitrate=32000 audio-type=voice frame-size=20 \
    ! queue ! sink.
