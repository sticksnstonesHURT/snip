# Planning & Testing

## 1. Planning Approach

Snip was built as a Docker learning project rather than starting with Docker configuration first.

The application was designed first as a small but realistic system with multiple dependencies:

```text
FastAPI
   │
   ├── PostgreSQL
   │
   └── Redis
```

The goal was to have enough moving parts to practice:

-   Containerizing an application
    
-   Multi-stage Docker builds
    
-   Compose service orchestration
    
-   Container-to-container networking
    
-   Persistent volumes
    
-   Healthchecks
    
-   Environment-based configuration
    
-   Redis caching
    
-   Cache invalidation
    

The application was intentionally kept small enough that the Docker architecture remained understandable.

----------

## 2. Build Order

The project was developed in stages rather than trying to build the entire stack at once.

```text
1. Application
      ↓
2. PostgreSQL integration
      ↓
3. Redis caching
      ↓
4. Dockerfile
      ↓
5. Docker Compose
      ↓
6. Configuration
      ↓
7. Runtime testing
```

This made it easier to determine whether a problem came from the application itself or from the containerization layer.

----------

## 3. Testing Strategy

Testing was mainly performed by running the actual stack and checking each layer independently.

### Application

Verify that the API starts and its endpoints behave as expected.

```bash
curl http://localhost:8000/health
```
<br>

### Database

Verify that the API can connect to PostgreSQL and persist data.

Create a link, then retrieve it again to confirm that the data was stored rather than only existing in memory.
<br>

### Redis

Verify the cache behavior by testing:

```text
First lookup
    ↓
Redis miss
    ↓
Postgres lookup
    ↓
Redis populated

Second lookup
    ↓
Redis hit
```
<br>

### Compose

Verify that all services start correctly:

```bash
docker compose up -d
docker compose ps
```

Check the service logs when diagnosing startup or connection problems:

```bash
docker compose logs

```
<br>

### Healthchecks

Confirm that PostgreSQL and Redis become healthy before the API starts.

```bash
docker compose ps

```

The expected state is that all three services are running and the dependencies report healthy.

----------

## 4. Functional Test Flow

A basic end-to-end test is:

```text
Create link
    ↓
Receive short code
    ↓
Request /{code}
    ↓
Redirect to original URL
    ↓
Click recorded
    ↓
Request stats
    ↓
Click count increased
```

Deletion is then tested separately:

```text
Create link
    ↓
Delete link
    ↓
Request /{code}
    ↓
Link no longer resolves
```

This also verifies that the Redis cache is invalidated correctly.

----------

## 5. Docker Lifecycle Testing

The stack was also tested across container recreation.
<br>

The important question was:

> What survives when containers are removed?

PostgreSQL data should survive:

```text
docker compose down
        ↓
docker compose up -d
        ↓
existing database still available

```

because the database uses the `pgdata` named volume.
<br>

Redis is expected to lose its cached values because it is intentionally not persistent.

The application should recover by reading the source of truth from PostgreSQL and rebuilding the cache.

----------

## 6. What These Tests Validate

The testing is not just checking whether the API returns `200`.

It verifies the architecture itself:

```text
Application
    │
    ├── correctly talks to PostgreSQL
    │
    ├── correctly talks to Redis
    │
    ├── survives container recreation
    │
    ├── persists database data
    │
    ├── rebuilds cache after a miss
    │
    └── invalidates stale cache entries
```

This makes the tests useful for validating the Docker design as well as the application.
