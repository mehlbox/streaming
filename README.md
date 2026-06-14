# Live Stream

Simple Flask frontend for a live HLS stream with polling-based viewer count, schedule, and a 1‑hour stats graph.

## Quick start

```bash
cp .env.example .env   # then edit SECRET_KEY, STREAMING_HOST, STREAM_KEY, …
docker compose up -d   # COMPOSE_PROFILES in .env selects main/satellite
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

- **Server**: `rtmp://streaming.example.com/stream?key=YOUR_SECRET`
- **Stream Key**: `live`

The `stream` path is the fixed RTMP application name (defined in `nginx.conf`);
the stream key field is the `STREAM_NAME` (default `live`), and the `?key=` value
must match `STREAM_KEY` when one is set. If you publish to a non-default
`RTMP_PORT`, include it in the server URL, e.g.
`rtmp://streaming.example.com:1936/stream?key=YOUR_SECRET`.

### 2) Start streaming

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
- `ADMIN_TOKEN` (default: empty; required to access the `/admin` page; when unset the page is unavailable)
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
STREAMING_HOST=stream2.example.com
RTMP_PORT=1936
SECRET_KEY=use-a-distinct-random-secret
STREAM_KEY=use-a-distinct-obs-stream-key
```

If the second site uses satellites, also set:

```dotenv
SATELLITE_BOOTSTRAP_ORIGIN_URL=https://stream2.example.com
SATELLITE_BOOTSTRAP_NAME_PREFIX=stream2-node
SATELLITE_BOOTSTRAP_PUBLIC_URL_TEMPLATE=https://{name}.example.com/hls
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
`1936`, for example `rtmp://stream2.example.com:1936/stream?key=...`.

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
- During bootstrap, the installer sizes `SATELLITE_MAX_VIEWERS` from the node's designed/booked uplink (`SATELLITE_DESIGNED_BANDWIDTH_MBPS`): it subtracts the safety margin (`SATELLITE_CAPACITY_MARGIN_PERCENT`, default `20`%) and divides by the assumed per-viewer bandwidth (`SATELLITE_VIEWER_MBPS`, default `3.5`). For Scaleway nodes the main app injects the designed uplink per server via cloud-init. If the designed uplink is unknown (`0`) or you set `SATELLITE_MAX_VIEWERS` explicitly before running the installer, that static value is used instead.
- `public_url` must use `https://`. The satellite obtains and renews its own certificate locally through Caddy. After Caddy creates the cert, the satellite agent uploads the Caddy certificate storage files back to the main server cache (`SATELLITE_CERT_CACHE_DIR`, default next to `STATE_DB`). In Docker Compose, `./satellite-certs` is mounted into the web container as `/satellite-certs`, and the satellite profile mounts the host Caddy cert directory into the agent container read-only. Future bootstraps restore that cache before starting Caddy, so a recreated node can reuse the certificate.
- If the main app is behind a reverse proxy, set `SATELLITE_BOOTSTRAP_ORIGIN_URL` so satellites pull HLS from the public HTTPS origin instead of an internal HTTP URL that may redirect.
- The installer writes its files to `/opt/streaming-satellite`, configures local nginx on loopback, configures Caddy for public HTTPS, and starts the agent automatically.
- You can still override installer-local defaults at execution time, for example `INSTALL_DIR=/srv/sat1 SATELLITE_PORT=8088 ... | sh`.

## Scaleway provisioning

The `/admin` page (gated by `ADMIN_TOKEN`) includes a Scaleway section that can create and delete managed Instances for satellite nodes.

Set these on the main server to enable it:

- `SCW_MANAGE_TOKEN` to enable the feature on the server side
- `SCW_SECRET_KEY` with your Scaleway API secret
- `SCW_DEFAULT_PROJECT_ID` for the target Scaleway project
- optionally `SCW_DEFAULT_ZONE`, `SCW_DEFAULT_COMMERCIAL_TYPE`, `SCW_DEFAULT_IMAGE`, `SCW_ROOT_VOLUME_TYPE`, `SCW_ROOT_VOLUME_SIZE_GB`, `SCW_SERVER_NAME_PREFIX`, and `SCW_SERVER_LIMIT`

Behavior:

- The backend caps the number of managed Scaleway servers at `SCW_SERVER_LIMIT` (default `100`, clamped to `1`–`100`).
- Created instances are named automatically as `instance1`, `instance2`, ...
- New Instances receive the current [satellite/cloud-init.example.yaml](satellite/cloud-init.example.yaml) as Scaleway `cloud-init` user-data, and the backend reads the `cloud-init` key back from the Scaleway API before reporting success.
- The created VM still uses the existing bootstrap flow, so the main server auto-assigns `node1`, `node2`, ... by IP and persists the mapping in the JSON node map.
- Root storage defaults to cheap local SSD (`l_ssd`) with `10 GB`, which matches the low-cost local-storage setup better than block storage.
- The UI does not expose image selection; it always creates nodes from `SCW_DEFAULT_IMAGE` (default `ubuntu_noble`, i.e. Ubuntu 24.04).
- This integration currently uses the Scaleway secret key for API requests; the access key is not required by the HTTP calls themselves.

## Notes

- The schedule is edited on the `/admin` page ("Programm" editor) and persisted as JSON at `/var/www/data/schedule-<SCHEDULE_NAME>.json` (default `schedule-default.json`), served from `/data/`. The viewer page fetches it and renders upcoming entries; the `/api/schedule` endpoints back the editor.
- The offline-overlay messages are still defined in `flask/static/js/app.js` (`updateOfflineMessage`).
- The stats graph reads `/stats?minutes=60` and draws a small canvas chart.
