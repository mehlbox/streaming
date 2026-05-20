# Live Stream

Simple Flask frontend for a live HLS stream with polling-based viewer count, schedule, and a 1‑hour stats graph.

## Quick start

```bash
# cd /docker/streaming
# docker compose up -d
```

## OBS Studio setup

### 1) Set OBS “Stream” settings

In OBS Studio:

1. **Settings → Stream**
2. **Service**: `Custom...`
3. **Server**: your RTMP ingest URL (example below)
4. **Stream Key**: your stream name (example below)

**Example**

Settings in OBS:

- **Server**: `rtmp://streaming.example.com/live?key=YOUR_SECRET`
- **Stream Key**: `live`

### 3) Start streaming

Click **Start Streaming** in OBS. The page will show **Online** once HLS segments are produced.

## Environment variables

- `STREAMING_HOST` (default: `streaming.example.com`; used in Traefik labels)
- `SITE_TITLE` (default: `docker streaming`)
- `SITE_SUBTITLE` (optional: subtitle shown below the site title)
- `STREAM_NAME` (default: `live`)
- `STREAM_KEY` (default: empty; if set, must match the `key` in OBS)
- `HLS_DIR` (default: `/var/www/hls`)
- `HLS_STALE_SECONDS` (default: `15`)
- `SOCKETIO_POLL_SECONDS` (default: `2`)
- `STATS_DB` (default: `/docker/streaming/flask/stats.db`)
- `STATS_SAMPLE_SECONDS` (default: `60`)
- `AUDIO_STREAM_NAME` (optional: name of an audio-only stream; enables the "Nur Audio" toggle)
- `AUDIO_HLS_URL` (optional: full URL to an audio-only HLS playlist; overrides `AUDIO_STREAM_NAME`)
- `AUDIO_INPUT_URL` (optional: input URL for audio-only ffmpeg; defaults to the live HLS playlist)

## Audio-only stream (server-generated)

The docker compose setup includes an `audio` ffmpeg container that reads the live HLS playlist by default (or `AUDIO_INPUT_URL` if set) and writes an audio-only HLS playlist at `/hls/audio.m3u8` using AAC. The UI toggle is enabled by `AUDIO_STREAM_NAME=audio`.

If you prefer RTMP input or need a different source, set `AUDIO_INPUT_URL` to your desired URL.

## Notes

- The schedule and offline messages are configured in `flask/static/js/app.js`.
- The stats graph reads `/stats?minutes=60` and draws a small canvas chart.
