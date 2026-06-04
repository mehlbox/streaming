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

- `RTMP_PORT` (default: `1935`; each main profile on one machine needs a distinct port)
- `STREAMING_HOST` (default: `streaming.example.com`; used in Traefik labels)
- `SITE_TITLE` (default: `docker streaming`)
- `SITE_SUBTITLE` (optional: subtitle shown below the site title)
- `STREAM_NAME` (default: `live`)
- `STREAM_KEY` (default: empty; if set, must match the `key` in OBS)
- `HLS_DIR` (default: `/var/www/hls`)
- `HLS_STALE_SECONDS` (default: `15`)
- `SOCKETIO_POLL_SECONDS` (default: `2`)
- `STATE_DB` (default: `/app/state.db`)
- `STATS_SAMPLE_SECONDS` (default: `60`)
- `AUDIO_STREAM_NAME` (optional: name of an audio-only stream; enables the "Nur Audio" toggle)
- `AUDIO_HLS_URL` (optional: full URL to an audio-only HLS playlist; overrides `AUDIO_STREAM_NAME`)
- `AUDIO_INPUT_URL` (optional: input URL for audio-only ffmpeg; defaults to the shared live HLS playlist file)

## Theme

The site uses a single theme defined in `flask/static/css/theme.css`. To change
the look, edit the CSS variables in the `:root` block there (a few alternative
palettes are included, commented out, to copy from). There is no theme setting in
`.env` and no per-host selection.

## Multiple Compose instances

Use a separate folder for each site. Compose derives the project name from the
folder name, so containers, networks, volumes, local state, certificate caches,
and Traefik resources remain separate automatically. Use distinct folder base
names, such as `streaming` and `stream2`.

For example, keep the existing site in `/docker/streaming` and clone or copy the
repository into `/docker/stream2`. In `/docker/stream2/.env`, change at least:

```dotenv
STREAMING_HOST=stream2.bethaus-speyer.de
RTMP_PORT=1936
SECRET_KEY=use-a-distinct-random-secret
STREAM_KEY=use-a-distinct-obs-stream-key
```

If the second site uses satellites, also set:

```dotenv
SATELLITE_BOOTSTRAP_ORIGIN_URL=https://stream2.bethaus-speyer.de
SATELLITE_BOOTSTRAP_NAME_PREFIX=stream2-node
SATELLITE_BOOTSTRAP_PUBLIC_URL_TEMPLATE=https://{name}.bethaus-speyer.de/hls
SATELLITE_API_KEY=use-a-distinct-satellite-secret
SCW_SERVER_NAME_PREFIX=stream2-instance
```

Start each site from its own folder:

```bash
cd /docker/streaming
docker compose up -d

cd /docker/stream2
docker compose up -d
```

Publish OBS for the first site to port `1935` and the second site to port
`1936`, for example `rtmp://stream2.bethaus-speyer.de:1936/live?key=...`.

The external `traefik` network remains shared intentionally. Satellite-profile
instances must also use distinct `SATELLITE_PORT` values when they bind ports
on the same machine. Set a distinct `CADDY_CERTIFICATES_DIR` for each edge
instance when multiple edge agents run on the same machine.

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
- `public_url` must use `https://`. The satellite obtains and renews its own certificate locally through Caddy. After Caddy creates the cert, the satellite agent uploads the Caddy certificate storage files back to the main server cache (`SATELLITE_CERT_CACHE_DIR`, default next to `STATE_DB`). In Docker Compose, `./satellite-certs` is mounted into the web container as `/satellite-certs`, and the satellite profile mounts the host Caddy cert directory into the agent container read-only. Future bootstraps restore that cache before starting Caddy, so a recreated node can reuse the certificate.
- If the main app is behind a reverse proxy, set `SATELLITE_BOOTSTRAP_ORIGIN_URL` so satellites pull HLS from the public HTTPS origin instead of an internal HTTP URL that may redirect.
- The installer writes its files to `/opt/streaming-satellite`, configures local nginx on loopback, configures Caddy for public HTTPS, and starts the agent automatically.
- You can still override installer-local defaults at execution time, for example `INSTALL_DIR=/srv/sat1 SATELLITE_PORT=8088 ... | sh`.

## Scaleway provisioning

The `/debug` page now includes a Scaleway section that can create and delete managed Instances for satellite nodes.

Set these on the main server to enable it:

- `SCW_MANAGE_TOKEN` to enable the feature on the server side
- `SCW_SECRET_KEY` with your Scaleway API secret
- `SCW_DEFAULT_PROJECT_ID` for the target Scaleway project
- optionally `SCW_DEFAULT_ZONE`, `SCW_DEFAULT_COMMERCIAL_TYPE`, `SCW_DEFAULT_IMAGE`, `SCW_ROOT_VOLUME_TYPE`, `SCW_ROOT_VOLUME_SIZE_GB`, and `SCW_SERVER_NAME_PREFIX`

Behavior:

- The backend enforces a hard limit of `5` managed Scaleway servers.
- Created instances are named automatically as `instance1`, `instance2`, ...
- New Instances receive the current [satellite/cloud-init.example.yaml](satellite/cloud-init.example.yaml) as Scaleway `cloud-init` user-data, and the backend reads the `cloud-init` key back from the Scaleway API before reporting success.
- The created VM still uses the existing bootstrap flow, so the main server auto-assigns `node1`, `node2`, ... by IP and persists the mapping in the JSON node map.
- Root storage defaults to cheap local SSD (`l_ssd`) with `10 GB`, which matches the low-cost local-storage setup better than block storage.
- The UI does not expose image selection; it always creates Debian 12 (`debian_bookworm`) nodes.
- This integration currently uses the Scaleway secret key for API requests; the access key is not required by the HTTP calls themselves.

## Notes

- The schedule and offline messages are configured in `flask/static/js/app.js`.
- The stats graph reads `/stats?minutes=60` and draws a small canvas chart.
