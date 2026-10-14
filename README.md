# Snip

A URL shortener with click tracking. 

FastAPI + Postgres + Redis, fully containerized with Docker Compose.

- Paste in a long URL, get back a short code. 
- Visiting the code redirects to the target and records a click. 
- Per-link click counts and recent click times are available on demand.

-----

## Stack

| Component | Role |
|-----------|------|
| FastAPI | HTTP API |
| Postgres | Source of truth: links, click events, API keys |
| Redis | Code → URL cache · click queue · rate-limit counters |
| Docker Compose | Orchestrates four services: `api`, `db`, `cache`, `migrate` |


----

## Quickstart

Requires Docker with the Compose v2 plugin (`docker compose`, not `docker-compose`).
Install: https://docs.docker.com/get-docker/

Nothing else. Python runs inside the container.

```bash
cp .env.example .env
make up
curl localhost:8000/health     # {"status":"ok"}
```

<br>

#### API: http://localhost:8000 
#### Interactive docs: http://localhost:8000/docs

<br>

Create an API key, then create a link:

```bash
make key n="laptop"              # prints the key once — save it

curl -X POST localhost:8000/links \
  -H 'Content-Type: application/json' \
  -H 'X-API-Key: snip_your_key_here' \
  -d '{"url":"https://example.com/some/long/path"}'
# {"code":"aB3xK9z","target_url":"https://example.com/some/long/path","clicks":0,"created_at":"..."}

curl -i localhost:8000/aB3xK9z
# HTTP/1.1 307 Temporary Redirect
# location: https://example.com/some/long/path
```


---------

### Configuration

`.env` (copy from `.env.example`):

| Variable | Default | Purpose |
|---|---|---|
| `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` | `snip` / — / `snip` | Postgres credentials; also assembled into `DATABASE_URL` |
| `CODE_LENGTH` | `7` | Length of generated short codes |
| `FLUSH_INTERVAL_SECONDS` | `5` | How often queued clicks are flushed to Postgres |
| `RATE_LIMIT_PER_MINUTE` | `60` | Per-key request limit |

<br>

**Note: `DATABASE_URL` and `REDIS_URL` are constructed in `docker-compose.yml`, not set in `.env`.**


-----------

## Endpoints

| Method | Path | Auth | Description |
|---|---|---|---|
| `POST` | `/links` | ✅ | Create a short link |
| `GET` | `/links` | – | List all links, newest first |
| `GET` | `/links/{code}/stats` | – | Click count + recent click times |
| `DELETE` | `/links/{code}` | ✅ | Delete a link and its click events |
| `GET` | `/{code}` | – | Redirect (`307`) to the target URL |
| `GET` | `/health` | – | Liveness check |

<br>
Writes require auth. Reads and redirects are open.


### Error codes: 
- `401` missing/invalid key
- `404` unknown code 
- `405` wrong method
- `422` invalid body (URL validation) 
- `429` rate limit exceeded.


----------


## Authentication

`POST /links` and `DELETE /links/{code}` require an `X-API-Key` header.

Keys are `snip_` + 32 random bytes (`secrets.token_urlsafe(32)`). 
**Only the sha256 hash is stored**: the raw key is printed once at creation and cannot be recovered.

```bash
make key n="laptop"
```

Key creation runs a one-off container invoking `python -m app.createkey`. 

There is deliberately **no HTTP endpoint** for minting keys: it would have to be either unauthenticated (anyone can mint keys) or authenticated (need a key to get a key). 

Requiring shell access to the host is the correct trust boundary.

Why plain sha256 and not bcrypt: password hashing exists to make *guessing* expensive against low-entropy inputs. 

A 256-bit random string isn't guessable at any speed, so a fast one-way hash is the right tool.


-------

## Rate limiting

60 requests/minute **per key**, fixed-window, counted in Redis at `ratelimit:<key-hash>:<window>`.

Over the limit returns `429`.

**Fixed-window means the counter resets on a wall-clock minute boundary — see Known limitations.**


------

## Click tracking

Redirects never write to Postgres.

`GET /{code}` resolves the target URL (Redis first, Postgres on a cache miss), then records the
click **entirely in Redis**:

- `INCR clicks:pending:{code}`
- `RPUSH clicks:queue {event_id, code, clicked_at}`

A background flusher drains the queue into Postgres: every `FLUSH_INTERVAL_SECONDS`, and once more during shutdown (from the app's lifespan hook, so `docker compose stop` delivers pending clicks).

<br>

The flush is crash-safe by construction:

1. **Peek, then trim but never pop.** `LRANGE` the first batch, write it, commit, *then* `LTRIM`.
   A crash at any point leaves the entries on the queue for the next flush.
   
2. **Idempotent inserts.** `INSERT ... ON CONFLICT (event_id) DO NOTHING RETURNING link_id`.
   
   A re-delivered batch inserts nothing and returns nothing, so counters don't double-count.

<br>

Together that's **at-least-once delivery + idempotent writes**. `click_events.event_id` (unique) is what makes it safe. 
Without it, a mid-flush crash would double-count.

<br>

#### Consequences:

- `links.clicks` **lags** by up to `FLUSH_INTERVAL_SECONDS`.

- `GET /links/{code}/stats` returns `pending_clicks` alongside `clicks`, so the lag is visible:
  `clicks + pending_clicks` is the true total.

- Redis is **persisted** (`--appendonly yes`, `redisdata` volume), because it holds data that isn't in Postgres yet.

---------

## Migrations

Schema is owned by Alembic. 

Revisions live in `alembic/versions/` and are applied by the `migrate` service before the API starts:

```
db healthy  ->  migrate (alembic upgrade head) exits 0  ->  api starts
```

<br>

**If a migration fails, the API does not start. That is deliberate: a failed deploy beats a running
app against a half-migrated schema.**

```bash
make build                              # rebuild (picks up alembic/versions/)
make up                                 # migrate runs, then api
docker compose logs migrate             # what was applied

# after editing app/models.py, generate a revision:
make migration m="add thing to links"

# inspect / roll back
make migrate                            # apply pending revisions
make check                              # detect drift between models and DB
docker compose run --rm api alembic current
docker compose run --rm api alembic history
docker compose run --rm api alembic downgrade -1
```

`make migration` bind-mounts the project directory because the API runs with `read_only: true` and cannot write a revision file to the repository. 

Commands that only touch Postgres (`upgrade`, `current`, `check`) need no mount.

<br>

Two gotchas:

- Services that build the same Dockerfile must declare the same `image:` name (see `api` and
  `migrate`), or they build separate images and drift — the migration then silently no-ops.

- `alembic downgrade -1` means "one revision back from wherever the database *actually* is". Check `alembic current` first; downgrading past the baseline drops tables.

------------------

## Container hardening

| Control | api | db | cache | Why |
|---|---|---|---|---|
| Non-root user | ✅ `appuser` | ✅ `postgres` | ✅ `redis` | Removes the value of an escape |
| `no-new-privileges` | ✅ | ✅ | ✅ | setuid binaries can't elevate |
| `cap_drop: ALL` | ✅ | ❌ | ❌ | Only `api` needs no capabilities at all |
| `read_only` rootfs | ✅ + tmpfs `/tmp` | ❌ | ❌ | `api` never writes to disk; databases must |
| `init: true` | ✅ | – | – | `tini` as PID 1 forwards signals, reaps zombies |
| Memory / CPU limits | 256M / 1.0 | 256M / 1.0 | 256M / 1.0 | A container can't consume host RAM unbounded |
| Log rotation | 10m × 3 | 10m × 3 | 10m × 3 | Caps log growth at ~30MB per container |


<br>

`migrate` runs the same image as `api` with the same hardening (non-root, `cap_drop: ALL`,
`read_only`, `no-new-privileges`), at 0.5 CPU since it's a one-shot job.


<br>

### Why `db` and `cache` aren't `cap_drop: ALL`

The official Postgres and Redis images start as root, `chown` their data directories, then drop to
their service user — requiring `CAP_CHOWN`, `CAP_FOWNER`, `CAP_SETUID`, `CAP_SETGID`. 

Dropping all capabilities makes them exit at startup (`exited 127`).

They reach the same end state anyway — non-root, no effective capabilities — because their
entrypoints do it themselves:


| | CapBnd (ceiling) | CapEff (running) | User |
|---|---|---|---|
| api | `0x0` | `0x0` | appuser |
| db | `0xa80425fb` | `0x0` | postgres |


<br>

### Verifying

```bash
docker inspect snip-api-1 --format 'readonly={{.HostConfig.ReadonlyRootfs}} init={{.HostConfig.Init}} caps={{.HostConfig.CapDrop}}'

# writes must fail — EROFS on a read-only mount, EACCES where mode bits deny first
docker compose exec api sh -c 'touch /home/appuser/x'   # Read-only file system
docker compose exec api sh -c 'touch /tmp/x'            # ok (tmpfs)

# capabilities from the kernel, not from Docker
docker compose exec api cat /proc/1/status | grep -E '^Cap'
```

-----


## Design notes

- **Cache-aside** for the code → URL lookup: the redirect reads Redis first, falls back to Postgres on a miss, then warms the cache (`link:{code}`, 1 hour TTL).

- **Cache invalidation**: deleting a link deletes its Redis key, so a deleted code can't keep
  resolving. 
  Without that it would serve from cache for up to an hour.

- **Reads are open, writes are authenticated.** Short links exist to be shared; requiring a key to follow one defeats the purpose.

- **Auth runs before rate limiting**, because you can't limit by key before knowing the key is
  valid. 
  Consequence: an invalid key costs one indexed DB lookup before returning `401`.

- **Migrations gate startup.** `api` waits on `migrate` exiting 0, so a broken schema produces a failed deploy rather than a running app that fails on first request.

- **`read_only` works because of `PYTHONDONTWRITEBYTECODE=1`** in the Dockerfile. 
	Without it Python would try to write `.pyc` files on import and crash.

- **Environment variables take two hops.** 
	`.env` → Compose interpolation → the service's environment: → Python: A variable in `.env` that no service lists **never reaches the container**.

- **The container user is uid 1000**, matching a typical host user, which is what makes host
  bind-mounts (e.g. `make migration`) writable without permission juggling.

------

## Known limitations

- **Fixed-window rate limiting** allows up to ~2× the limit across a window boundary: 60 requests at the end of one window and 60 at the start of the next are all legal. 

- **Invalid keys cost a DB lookup before returning `401`**:  no IP-based limiting precedes auth.

- **The click flusher assumes a single `api` instance.** 
	Two replicas would peek the same queue window; idempotency protects the counts, but not the pending counter. 
	Real fix: Redis Streams with consumer groups, or `LMOVE` to a per-worker list.


- **`links.clicks` lags by up to `FLUSH_INTERVAL_SECONDS`.** `/stats` exposes `pending_clicks` so the lag is visible rather than silent.

- **No key rotation or revocation.** Keys are created by CLI; revoking one means deleting its row manually.

- **`logging.basicConfig` is applied in `app/main.py`**: effective, but blunt. Explicit uvicorn log configuration would be tidier.

-----

## Build history

- **Phase 1: Containerize.** Multi-stage build, Compose networking, volumes, cache-aside. See `docs/`.

- **Phase 2: Harden.** Non-root, capabilities, read-only rootfs, resource limits, log rotation. See `docs_phase_2/`.

- **Phase 3: Scale the design.** Alembic migrations, Redis click batching, API key auth + rate limiting. See`docs_phase_3/`.
