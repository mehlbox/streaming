#!/usr/bin/env sh
set -eu

while true; do
  if [ -z "${AUDIO_STREAM_NAME:-}" ]; then
    sleep 5
    continue
  fi

  LIVE="/var/www/hls/${STREAM_NAME:-live}.m3u8"
  if [ ! -f "${LIVE}" ]; then
    sleep 2
    continue
  fi

  NOW=$(date +%s)
  MOD=$(stat -c %Y "${LIVE}" 2>/dev/null || stat -f %m "${LIVE}" 2>/dev/null || echo 0)
  STALE=${HLS_STALE_SECONDS:-15}
  if [ "${MOD}" -eq 0 ] || [ $((NOW - MOD)) -gt "${STALE}" ]; then
    sleep 2
    continue
  fi

  LAST_SEG=$(grep -v '^#' "${LIVE}" | tail -n 1)
  if [ -z "${LAST_SEG}" ]; then
    sleep 2
    continue
  fi

  SEG_PATH="/var/www/hls/${LAST_SEG}"
  if [ ! -f "${SEG_PATH}" ]; then
    sleep 2
    continue
  fi

  SEG_MOD=$(stat -c %Y "${SEG_PATH}" 2>/dev/null || stat -f %m "${SEG_PATH}" 2>/dev/null || echo 0)
  if [ "${SEG_MOD}" -eq 0 ] || [ $((NOW - SEG_MOD)) -gt "${STALE}" ]; then
    sleep 2
    continue
  fi

  OUT="/var/www/hls/${AUDIO_STREAM_NAME}.m3u8"
  SEG="/var/www/hls/${AUDIO_STREAM_NAME}_%03d.m4s"

  if [ -n "${AUDIO_INPUT_URL:-}" ]; then
    INPUT="${AUDIO_INPUT_URL}"
  else
    # Read the live playlist directly from the shared HLS volume so the
    # audio sidecar does not show up as an extra viewer in nginx access logs.
    INPUT="/var/www/hls/${STREAM_NAME:-live}.m3u8"
  fi

  set -- -hide_banner -loglevel error -nostdin -fflags +genpts -rw_timeout 5000000
  case "${INPUT}" in
    rtmp://*|rtmps://*)
      set -- "$@" -use_wallclock_as_timestamps 1
      ;;
    http://*|https://*)
      set -- "$@" -reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5
      ;;
  esac
  set -- "$@" -i "${INPUT}" -vn -c:a aac -b:a 160k -ar 48000 -ac 2 \
    -f hls -hls_time 4 -hls_list_size 6 -hls_flags delete_segments+independent_segments \
    -hls_segment_type fmp4 -hls_fmp4_init_filename audio_init.mp4 \
    -hls_segment_filename "${SEG}" "${OUT}"

  if ! ffmpeg "$@"; then
    sleep 2
    continue
  fi

  sleep 2
done
