# Pubblica il microfono del PC sul path "voice" del Pi (alternativa al browser).
# Elenca i device con:  ffmpeg -list_devices true -f dshow -i dummy
param(
  [string]$Mic = "Microphone (Realtek(R) Audio)",
  [string]$Host = "ronaldo.local"
)
ffmpeg -hide_banner -f dshow -i "audio=$Mic" `
  -c:a libopus -b:a 32k -application voip -ac 1 `
  -f rtsp -rtsp_transport tcp "rtsp://${Host}:8554/voice"
