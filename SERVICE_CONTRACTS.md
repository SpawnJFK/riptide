# Riptide service contract

Contract version: 1.0.0

The single contract between the Python engine (`backend/`) and the web UI (`web/`).
Both sides carry the same version string. `scripts/verify.py` fails if they drift and prints a
`sync_hash` (first 12 hex chars of this file's SHA-256) for the session report.

Rules:
- Change this file in the same commit as the code that changes the API.
- Additive change (new field, new endpoint): bump minor. Breaking change: bump major.
- Never bump quietly. Every bump gets a line in the changelog at the bottom.

## Transport

- HTTP JSON under `/api`, WebSocket at `/ws`. Default bind `127.0.0.1:8787`.
- Auth: when `RIPTIDE_TOKEN` is set, send header `x-riptide-token` (or `?token=` for `/ws` and file downloads).
  Non-loopback binds without a token generate one at startup and print it. Unauthenticated network exposure is not allowed.
- Errors: non-2xx with `{"detail": "<human readable reason>"}`. 404 unknown torrent, 409 duplicate or not-ready, 401 auth.
- Torrent id: lowercase hex of the best info-hash (v1 SHA-1, or truncated v2). Stable across restarts.

## Torrent object (list rows and WS ticks)

| field | type | notes |
|---|---|---|
| id | string | info-hash hex |
| name | string | falls back to id before metadata |
| state | enum | downloading, seeding, paused, completed, queued, checking, checking_resume, metadata, allocating, finished, error |
| progress | float 0..1 | wanted pieces only |
| size, done | int bytes | wanted bytes, wanted bytes done |
| dl_rate, ul_rate | int B/s | payload rates |
| eta | int seconds | -1 when unknown |
| peers, seeds | int | connected (peers excludes seeds) |
| peers_total, seeds_total | int | from tracker scrape, -1 unknown |
| ratio | float | all-time upload / download |
| uploaded, downloaded | int bytes | all-time |
| save_path | string | |
| queue | int | -1 when not queued (seeding) |
| added, completed | int unix | 0 when not set |
| sequential, auto_managed, has_metadata | bool | |
| error | string | empty when healthy |
| category | string | empty for none |
| tags | string[] | sorted |
| dl_limit, ul_limit | int B/s | per-torrent, 0 or -1 unlimited |
| availability | float | distributed copies |
| tracker | string | current tracker URL |

Detail (`GET /torrents/{id}`) adds: `pieces, piece_size, comment, creator, created, private, files_count, magnet`.

## Endpoints

| method | path | body / query | returns |
|---|---|---|---|
| GET | /health | | `{ok, version, contract, libtorrent, auth}` (no auth) |
| GET | /torrents | | Torrent[] |
| POST | /torrents/magnet | `{uris[], save_path?, paused?, sequential?, category?, tags?, add_default_trackers?}` | `{added: id[], errors: {uri, error}[]}` |
| POST | /torrents/file | multipart `files[]` + same options as form fields (`tags` comma separated) | `{added, errors: {file, error}[]}` |
| POST | /torrents/bulk | `{ids[], action}` action: pause, resume, force_resume, recheck, reannounce, remove, remove_data, top, up, down, bottom | `{ok, failed[]}` |
| POST | /torrents/pause-all, /torrents/resume-all | | `{ok}` |
| GET | /torrents/{id} | | TorrentDetail |
| PATCH | /torrents/{id} | `{sequential?, dl_limit?, ul_limit?, super_seeding?, category?, tags?, add_trackers?}` | TorrentDetail |
| DELETE | /torrents/{id} | `?delete_files=bool` | `{ok}` |
| GET | /torrents/{id}/files | | `{index, path, size, done, progress, priority}[]` (pad files excluded, index is libtorrent's) |
| PUT | /torrents/{id}/files | `{priorities: {index: 0..7}}` | files |
| POST | /torrents/{id}/rename | `{index, path}` | `{ok}` |
| POST | /torrents/{id}/move | `{path}` | `{ok}` |
| GET | /torrents/{id}/peers | | `{ip, port, client, progress, dl_rate, ul_rate, downloaded, uploaded, seed, encrypted, utp, kind, source}[]` |
| POST | /torrents/{id}/peers | `{ip, port}` | `{ok}` connect a peer manually |
| GET | /torrents/{id}/trackers | | `{url, tier, status, message, seeds, peers}[]` |
| GET | /torrents/{id}/pieces | | `{total, buckets: float[]}` max 1024 buckets, 0..1 fill |
| GET | /torrents/{id}/export | | .torrent bytes |
| GET | /stats | | `{stats, history: [t, dl, ul][]}` |
| GET | /events | | engine events |
| GET, PATCH | /settings | libtorrent settings_pack keys from `SETTABLE` plus `save_path`, `listen_port` | settings |
| POST | /alt-speed | `?on=bool` | `{ok, on}` |
| GET, POST | /categories | `{name, save_path}` | `{categories, tags}` |
| DELETE | /categories/{name} | | `{categories}` |
| POST | /create | `{source, trackers[], web_seeds[], comment, private, piece_size, start_seeding}` | .torrent bytes, header `x-riptide-id` |

## WebSocket `/ws`

Server pushes JSON frames:
- `hello` on connect: `{torrents, stats, events, history}`
- `tick` every second: `{torrents, stats, t}` (full list; diffing is a later optimisation, see phase plan)
- `event`: `{event: {ts, level: info|success|error, text, id}}`
- `added` / `removed` / `finished`: `{id}`

Stats: `{dl_rate, ul_rate, dht_nodes, peers, torrents, active, listening, port, paused, dl_limit, ul_limit, free_space}`.

## Changelog

- 1.0.0 (2026-10-11): initial contract, shipped with v0.1.0.
