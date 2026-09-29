# Snip

A URL shortener with click tracking. FastAPI + Postgres + Redis, fully containerized.

Built as a Docker learning project — meant to exercise multi-stage builds, Compose
networking, volumes, healthchecks, and cache invalidation on a real (if small) app.

## Stack

| Component | Role |
|-----------|------|
| FastAPI   | HTTP API |
| Postgres  | Source of truth (links + click events) |
| Redis     | Cache for the code → URL lookup |
| Docker Compose | Orchestrates all three |

## Quickstart

```bash
cp .env.example .env     # then edit values
make up
make health
```

### API: http://localhost:8000
### Interactive docs: http://localhost:8000/docs

## Endpoints
| Method | Path | Description |
| --- | --- | --- |
| `POST` | `/links` | Create a short link from a URL |
| `GET` | `/links` | List all links |
| `GET` | `/links/{code}/stats` | Click count + recent click times |
| `DELETE` | `/links/{code}` | Delete a link (and its clicks) |
| `GET` | `/{code}` | Redirect to the target URL |
| `GET` | `/health` | Liveness check |



### Example
```bash
curl -X POST localhost:8000/links \
  -H 'Content-Type: application/json' \
  -d '{"url":"https://example.com/some/long/path"}'
# {"code":"aB3xK9z","target_url":"https://example.com/some/long/path","clicks":0,"created_at":"..."}
```

```bash
curl -i localhost:8000/aB3xK9z
# HTTP/1.1 307 Temporary Redirect
# location: https://example.com/some/long/path
```

## Layout
```
app/
  main.py       routes + app setup
  models.py     SQLAlchemy tables
  schemas.py    request/response shapes
  db.py         engine + session
  cache.py      Redis helpers
  config.py     env-driven settings
Dockerfile      multi-stage build
docker-compose.yml
Makefile
```


## Design notes
- Cache-aside: the redirect reads Redis first, falls back to Postgres, then warms the cache.
- Cache invalidation: deleting a link invalidates its Redis key, so a deleted code can't keep resolving.
- Click events: every redirect inserts a click_events row (for stats) and bumps a counter on links (for cheap totals).
- Volumes: Postgres data lives in the snip_pgdata named volume and survives restarts. Redis is intentionally not persisted — it's a cache.


## Known limitations (Phase 1)
- Tables are created on boot via create_all. Production should use migrations (Alembic).
- The redirect path still hits Postgres to record the click, so the cache doesn't yet skip the DB. Batching click writes in Redis is the next step.
- No auth. Anyone can create or delete links.

Further details regarding design convention and limitations will be covered in later `.md` notes, for reference.
