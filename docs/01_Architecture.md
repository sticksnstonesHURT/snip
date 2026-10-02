# Snip: Architecture

## 1. Purpose

Snip is a small URL-shortening service with click tracking.

- A user submits a long URL and receives a short code. 
- When someone requests the short code, Snip looks up the original URL, records the click, and redirects the client to the original destination.

**The application was deliberately built as a Docker learning project rather than as a production-ready URL shortener.**

The project was designed to exercise:

-   FastAPI application development
    
-   PostgreSQL persistence
    
-   Redis caching
    
-   Docker multi-stage builds
    
-   Docker Compose
    
-   container-to-container networking
    
-   healthchecks
    
-   named volumes
    
-   environment-based configuration
    
-   cache invalidation
    
-   basic container security
    
-   multi-service application debugging
    

The final Phase 1 system consists of three services:

```text
                         HTTP
                          │
                          ▼
                 ┌─────────────────┐
                 │      API        │
                 │    FastAPI      │
                 │    Uvicorn      │
                 └───────┬─────────┘
                         │
                ┌────────┴────────┐
                │                 │
                ▼                 ▼
        ┌──────────────┐   ┌──────────────┐
        │  PostgreSQL  │   │    Redis     │
        │              │   │              │
        │ Source of    │   │    Cache     │
        │ truth        │   │              │
        └──────┬───────┘   └──────────────┘
               │
               ▼
          pgdata volume

```

----------

## 2. Components

### 2.1 FastAPI

FastAPI is the application's HTTP/API layer.

It is responsible for:

-   receiving HTTP requests
    
-   validating request data
    
-   routing requests to the correct handler
    
-   interacting with PostgreSQL
    
-   interacting with Redis
    
-   generating short codes
    
-   recording click events
    
-   returning API responses
    
-   returning redirects
    

**Note: FastAPI itself is not the HTTP server.**

The application is served by Uvicorn:

```text
FastAPI
   │
   │ application definition
   ▼
Uvicorn
   │
   │ HTTP server
   ▼
Client
```

The container therefore starts:

```text
uvicorn app.main:app
```

rather than running `python main.py`.

----------

## 3. PostgreSQL

PostgreSQL is the **source of truth**.

It stores the application's persistent state:

```text
links
click_events

```

The `links` table represents the actual short links.

The `click_events` table records individual redirect events.

PostgreSQL was chosen as the authoritative store because Redis is intentionally treated as disposable cache state.

If Redis disappears:

```text
Redis data lost
       │
       ▼
No permanent data loss
       │
       ▼
PostgreSQL still contains the links

```

If PostgreSQL data disappears, however, the application loses its actual link data.

That distinction is fundamental to the architecture.

----------

## 4. Redis

Redis is used as a cache for:

```text
short code → target URL

```

For example:

```text
link:k6lOLa6
        │
        ▼
https://example.com/some/long/path

```

Redis is **not** the source of truth.

The cache follows a cache-aside pattern.

### Read

```text
Request
   │
   ▼
Redis
   │
   ├── HIT ──────► use cached URL
   │
   └── MISS
          │
          ▼
      PostgreSQL
          │
          ▼
       Redis
          │
          ▼
      return URL

```

### Write

When a link is created:

```text
Create link
    │
    ▼
PostgreSQL
    │
    ▼
Redis

```

The cache is seeded immediately so that the first redirect does not need to experience a cache miss.

### Delete

When a link is deleted:

```text
DELETE link
      │
      ├──► PostgreSQL
      │
      └──► Redis DELETE

```

The Redis key must be removed when the database record is deleted.

Otherwise Redis could continue returning the deleted URL until its TTL expires.

----------

## 5. Why PostgreSQL and Redis both exist

It would be possible to build a simple URL shortener using PostgreSQL alone.

Redis was introduced deliberately to create a realistic caching layer and to exercise:

-   service-to-service networking
    
-   cache-aside design
    
-   TTLs
    
-   cache invalidation
    
-   failure boundaries
    
-   multiple containers communicating through Compose
    

The important architectural rule is:

```text
PostgreSQL = truth
Redis      = performance optimization

```

Redis can be destroyed and recreated without destroying the application's authoritative data.

For that reason Redis intentionally has no persistent Docker volume.

----------

## 6. Docker services

Docker Compose defines three services:

```yaml
services:
  api:
  db:
  cache:

```

Their roles are:

**Service**

Image/source

Responsibility

`api`

- Built from local Dockerfile

- FastAPI application

`db`

- `postgres:16-alpine`

- Persistent database

`cache`

- `redis:7-alpine`

- Redis cache

The API is built locally:

```yaml
api:
  build: .
```

while PostgreSQL and Redis use existing images:

```yaml
db:
  image: postgres:16-alpine

cache:
  image: redis:7-alpine
```

This means the Compose file combines:

```text
locally built application
        +
official infrastructure images
```

----------

## 7. Docker Compose networking

Compose automatically creates a network for the project.

The services can communicate using their **service names as hostnames**.

Therefore the API connects to:

```text
PostgreSQL:
db:5432

Redis:
cache:6379
```

It does not use:

```text
localhost:5432
localhost:6379

```

Inside the API container, `localhost` means:

```text
the API container itself
```

not the PostgreSQL container.

The resulting communication model is:

```text
Host
 │
 │ localhost:8000
 ▼
api container
 │
 ├── db:5432 ──────► PostgreSQL
 │
 └── cache:6379 ───► Redis
```

This was one of the key Docker networking concepts exercised by the project.

The Compose service name `db` is resolved through Docker's internal DNS.

----------

## 8. Host-to-container networking

The API publishes port `8000`:

```yaml
ports:
  - "8000:8000"

```

This creates:

```text
Host port 8000
       │
       ▼
Container port 8000
       │
       ▼
Uvicorn
```

The application therefore becomes reachable from the host through:

```text
http://localhost:8000
```

The Dockerfile starts Uvicorn with:

```text
--host 0.0.0.0
```

This is important.

<br>

Inside a container:

```text
127.0.0.1
```

means the container's own loopback interface.

Binding to:

```text
0.0.0.0
```

makes Uvicorn listen on all container interfaces, allowing Docker's port publishing to reach the application.

<br>

This was verified in the actual container logs:

```text
Uvicorn running on http://0.0.0.0:8000

```

and:

```text
curl localhost:8000/health
→ {"status":"ok"}

```

<br>

The running container also showed:

```text
0.0.0.0:8000->8000/tcp
```

confirming the host-to-container port mapping.

----------

## 9. Startup ordering

A container being **running** does not necessarily mean the service inside it is ready.

**This matters particularly for PostgreSQL.**

The API performs database initialization during application startup:

```python
Base.metadata.create_all(bind=engine)
```

If the API starts before PostgreSQL is accepting connections, startup can fail.
<br>

Compose therefore uses healthchecks.

PostgreSQL:

```yaml
healthcheck:
  test: ["CMD-SHELL", "pg_isready ..."]

```

Redis:

```yaml
healthcheck:
  test: ["CMD", "redis-cli", "ping"]

```

<br>

The API depends on both being healthy:

```yaml
depends_on:
  db:
    condition: service_healthy
  cache:
    condition: service_healthy

```
<br>

The distinction is:

```text
container started
        ≠
service ready

```

`depends_on` with the health condition makes the API wait until its dependencies actually pass their readiness checks.

<br>

This was verified during startup:

```text
cache → Healthy
db    → Healthy
api   → Started

```

rather than the API starting immediately.

----------

## 10. PostgreSQL persistence

PostgreSQL uses a named Docker volume:

```yaml
volumes:
  - pgdata:/var/lib/postgresql/data

```

<br>

The relationship is:

```text
Docker volume: pgdata
        │
        ▼
/var/lib/postgresql/data
        │
        ▼
PostgreSQL data

```

The volume exists independently from the PostgreSQL container.

Therefore:

```text
docker compose down

```

removes the containers but keeps the database volume.
<br>
The data remains available when the stack is started again.

By contrast:

```text
docker compose down -v

```

also removes the named volume and therefore destroys the stored database data.
<br>
Redis does not have an equivalent volume because its contents are cache state rather than authoritative application state.

----------

## 11. Application data flow

### Creating a link

```text
POST /links
      │
      ▼
Pydantic validates URL
      │
      ▼
Generate random code
      │
      ▼
Check PostgreSQL for collision
      │
      ▼
Insert Link
      │
      ▼
Commit
      │
      ▼
Seed Redis
      │
      ▼
Return LinkOut

```

The generated code uses Python's `secrets` module rather than `random`, making codes less predictable.

The application attempts up to five times to generate a code that does not already exist.

----------

### Redirecting

```text
GET /{code}
      │
      ▼
Find link
      │
      ▼
Redis lookup
      │
   ┌──┴──┐
   │     │
 HIT    MISS
   │     │
   │     ▼
   │  PostgreSQL
   │     │
   │     ▼
   │    Redis
   │
   ▼
Increment clicks
      │
      ▼
Insert ClickEvent
      │
      ▼
Commit
      │
      ▼
HTTP 307 redirect

```

The current implementation still touches PostgreSQL during redirects because every click must be recorded.

Therefore Redis does **not yet completely remove PostgreSQL from the redirect path**.

A future design could batch click writes through Redis and periodically persist them to PostgreSQL.

That is deliberately a Phase 2 concern rather than something added to Phase 1.

----------

## 12. Deleting a link

```text
DELETE /links/{code}
          │
          ▼
Find Link
          │
          ▼
Delete Link
          │
          ├──────────────► ClickEvent rows cascade-delete
          │
          ▼
Commit PostgreSQL
          │
          ▼
Delete Redis key
          │
          ▼
204 No Content

```

The database relationship uses:

```text
ON DELETE CASCADE

```

and SQLAlchemy also defines:

```text
cascade="all, delete-orphan"

```

so the associated click events are removed when their parent link is removed.
<br>
The Redis key is explicitly invalidated because otherwise the deleted URL could continue resolving from cache.

The final verification demonstrated the complete chain:

```text
DELETE /links/k6lOLa6 → 204
Redis EXISTS          → 0
click_events count    → 0
GET /k6lOLa6          → 404

```

This proved the HTTP layer, PostgreSQL state, click-event cascade, and Redis invalidation were all consistent.

----------

## 13. Important architectural limitations

This is intentionally a Phase 1 system.

### Database migrations

Tables are created using:

```python
Base.metadata.create_all(bind=engine)

```

This is convenient for the learning project but is not a migration system.

A production system should use something such as Alembic so schema changes can be versioned and applied incrementally.

### Redis does not eliminate all database work

The redirect still writes click information to PostgreSQL.

Therefore the cache currently improves URL lookup semantics but does not make the entire redirect path database-free.

### No authentication

Anyone who can reach the API can create and delete links.

This is acceptable for the learning project but would obviously need to change for a public service.

These limitations are deliberate Phase 1 boundaries rather than accidental missing features.

----------

## 14. The mental model

The easiest way to remember the entire system is:

```text
                 ┌──────────────┐
                 │    Client    │
                 └──────┬───────┘
                        │
                     HTTP
                        │
                        ▼
                 ┌──────────────┐
                 │   FastAPI    │
                 │     API      │
                 └──────┬───────┘
                        │
              ┌─────────┴─────────┐
              │                   │
              ▼                   ▼
       ┌──────────────┐    ┌──────────────┐
       │  PostgreSQL  │    │    Redis     │
       │              │    │              │
       │ SOURCE       │    │ CACHE        │
       │ OF TRUTH     │    │              │
       └──────┬───────┘    └──────────────┘
              │
              ▼
         Docker volume

```

Remember the responsibilities:

```text
FastAPI     → application logic + HTTP
Uvicorn     → HTTP server
PostgreSQL  → permanent application state
Redis       → temporary cached state
Compose     → service orchestration
Docker      → application packaging/runtime
pgdata      → database persistence

```

And remember the network:

```text
Host
 │
 └── localhost:8000
          │
          ▼
        api
       /   \
      /     \
 db:5432   cache:6379

```

That is the architecture of Snip.
