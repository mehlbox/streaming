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
- `AUDIO_INPUT_URL` (optional: input URL for audio-only ffmpeg; defaults to the shared live HLS playlist file)

## Audio-only stream (server-generated)

The docker compose setup includes an `audio` ffmpeg container that reads the shared live HLS playlist by default (or `AUDIO_INPUT_URL` if set) and writes an audio-only HLS playlist at `/hls/audio.m3u8` using AAC. The UI toggle is enabled by `AUDIO_STREAM_NAME=audio`.

If you prefer RTMP input or need a different source, set `AUDIO_INPUT_URL` to your desired URL.

## Satellite bootstrap

The main server can expose a one-shot installer script for fresh satellite hosts.

Set these on the main server:

- `SATELLITE_API_KEY` with the shared satellite secret
- `SATELLITE_BOOTSTRAP_TOKEN` with a separate download token for the installer endpoint
- `SATELLITE_BOOTSTRAP_ORIGIN_URL=https://streaming.example.com` if the main app is behind Traefik or another TLS terminator
- `SATELLITE_BOOTSTRAP_PUBLIC_URL_TEMPLATE=https://{name}.example.com/hls` so the main app can assign `node1`, `node2`, ...

Then a new server can join with a single command:

```bash
curl -fsSL "https://streaming.example.com/api/satellite/bootstrap.sh?token=BOOTSTRAP_TOKEN" | sh
```

For cloud-init based provisioning, see [satellite/cloud-init.example.yaml](satellite/cloud-init.example.yaml).

Notes:

- Docker is not required on the satellite VM. The installer sets up `nginx`, `caddy`, `python3`, a virtualenv, and a system service directly on the host.
- The bootstrap endpoint assigns the node name and `public_url` itself and reuses the same node number for known IPs via the persisted JSON node map.
- During bootstrap, the installer runs 3 upload speed tests, averages them, subtracts 20%, and derives `SATELLITE_MAX_VIEWERS` from `SATELLITE_VIEWER_MBPS` (default `3.5`). If the speed test fails, it falls back to the configured `SATELLITE_MAX_VIEWERS`.
- `public_url` must use `https://`. The satellite obtains and renews its own certificate locally through Caddy.
- If the main app is behind a reverse proxy, set `SATELLITE_BOOTSTRAP_ORIGIN_URL` so satellites pull HLS from the public HTTPS origin instead of an internal HTTP URL that may redirect.
- The installer writes its files to `/opt/streaming-satellite`, configures local nginx on loopback, configures Caddy for public HTTPS, and starts the agent automatically.
- You can still override installer-local defaults at execution time, for example `INSTALL_DIR=/srv/sat1 SATELLITE_PORT=8088 ... | sh`.

## Notes

- The schedule and offline messages are configured in `flask/static/js/app.js`.
- The stats graph reads `/stats?minutes=60` and draws a small canvas chart.
