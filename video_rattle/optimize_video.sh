#!/usr/bin/env bash
# Normaliza y comprime el video renderizado para que lo acepten TikTok / IG Reels / YouTube Shorts.
#  - H.264 High, yuv420p, 30fps, 1080x1920, CRF 23 con tope de bitrate
#  - AAC 128k 44.1kHz estéreo
#  - +faststart (moov al inicio; Meta lo prefiere)
# Uso: bash optimize_video.sh [input] [output]
set -euo pipefail

IN="${1:-out/rattle_video.mp4}"
OUT="${2:-out/rattle_video_optimized.mp4}"

if [ ! -f "$IN" ]; then
  echo "❌ No existe $IN" >&2
  exit 1
fi

ffmpeg -y -hide_banner -loglevel error -i "$IN" \
  -vf "scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2,fps=30,format=yuv420p" \
  -c:v libx264 -profile:v high -level 4.1 -preset medium -crf 23 -maxrate 6M -bufsize 12M \
  -c:a aac -b:a 128k -ar 44100 -ac 2 \
  -movflags +faststart \
  "$OUT"

in_mb=$(du -m "$IN" | cut -f1)
out_mb=$(du -m "$OUT" | cut -f1)
dur=$(ffprobe -v error -show_entries format=duration -of default=nw=1:nk=1 "$OUT")
echo "✅ Video optimizado: ${in_mb}MB → ${out_mb}MB (duración ${dur}s)"

# Reemplazar el original para que el publicador use la versión optimizada
mv "$OUT" "$IN"
