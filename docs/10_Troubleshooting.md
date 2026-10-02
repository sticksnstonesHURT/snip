# Troubleshooting

This section records problems encountered while building and running Snip, along with what each problem taught.

**The purpose is not to document every possible Docker error, but to preserve the mistakes that were relevant to this project.**

----------

## 1. `localhost` inside a container

### Problem

The API initially needed to connect to PostgreSQL and Redis from inside its own container.

A common mistake is to use:

```text
localhost:5432
localhost:6379
```
<br>

### Why it doesn't work

Inside the API container, `localhost` refers to the API container itself.

PostgreSQL and Redis are separate containers.
<br>

### Correct approach

Use the Compose service names:

```text
db:5432
cache:6379
```

### Lesson

> Container-to-container communication uses the service name, not `localhost`.

----------

## 2. API starting before its dependencies were ready

### Problem

Starting containers does not necessarily mean the services inside them are ready to accept connections.

PostgreSQL may still be initializing when the API starts.
<br>

### Solution

PostgreSQL and Redis were given healthchecks, and the API uses:

```yaml
depends_on:
  db:
    condition: service_healthy
  cache:
    condition: service_healthy
```
<br>

### Lesson

> Container startup and application readiness are different things.

----------

## 3. Database data disappearing

### Problem

Containers are disposable. Without persistent storage, removing the PostgreSQL container would also remove its database files.
<br>

### Solution

PostgreSQL uses the named volume:

```text
pgdata:/var/lib/postgresql/data
```
<br>

### Lesson

> Persistent application data belongs outside the container's writable layer.

----------

## 4. Redis data disappearing

### Problem

Redis loses its data when its container is recreated.
<br>

### Why this is acceptable

Redis is only being used as a cache.

PostgreSQL remains the source of truth.

After Redis is recreated:

```text
Redis empty
    ↓
Postgres lookup
    ↓
Redis populated again
```
<br>

### Lesson

> Not every service needs persistent storage. Persistence should follow the role of the data.

----------

## 5. Stale Redis data after deletion

### Problem

Deleting a link from PostgreSQL while leaving its Redis entry would allow the deleted short code to continue resolving.
<br>

### Solution

Deleting a link also invalidates its Redis key.

```text
Delete from PostgreSQL
        ↓
Invalidate Redis key

```
<br>

### Lesson

> Whenever cached data is changed or deleted, the corresponding cache entry must be considered.

----------

## 6. Configuration mismatch

### Problem

The API and PostgreSQL must agree on credentials and database information.

If the API uses different values from the database container, the connection fails.
<br>

### Solution

Both sides use the same `.env` variables through Compose.

```text
.env
 │
 ├──→ PostgreSQL
 │
 └──→ API DATABASE_URL
```
<br>

### Lesson

> Keep shared configuration in one place instead of duplicating values.

----------

## 7. Rebuilding after application changes

When application code is copied into the Docker image during the build, changing the source file on the host does not automatically change the existing image.

The image needs to be rebuilt:

```bash
docker compose build
```

or:

```bash
docker compose up --build
```
<br>

### Lesson

> A Docker image is a built snapshot of the application at build time.

For development workflows, bind mounts or other development-specific setups can avoid rebuilding for every change. Snip deliberately keeps the final setup simple rather than optimizing it as a development environment.

----------

## 8. How to troubleshoot the stack

The first checks should be:

```bash
docker compose ps
docker compose logs
docker compose config
```
<br>

Then narrow the problem down to the relevant layer:

```text
Application problem
        ↓
API logs / endpoint behavior

Database problem
        ↓
Postgres logs / connection settings

Redis problem
        ↓
Redis logs / cache behavior

Compose problem
        ↓
Compose config / service status

Persistence problem
        ↓
Docker volumes
```

The main lesson from the project is to **identify which layer is failing before changing configuration blindly**.
