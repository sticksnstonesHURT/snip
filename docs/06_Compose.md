# Snip — Docker Compose Architecture & Runtime Orchestration

## 1. What Docker Compose is doing in this project

The Dockerfile answers:

> "How do I build the API container image?"

Docker Compose answers:

> "How do I run the entire Snip application as a system?"

Snip consists of three services:

```text
┌─────────────────────┐
│        API          │
│      FastAPI        │
│                     │
│  Uvicorn :8000      │
└──────────┬──────────┘
           │
     ┌─────┴─────┐
     │           │
     ▼           ▼
┌──────────┐ ┌──────────┐
│ Postgres │ │  Redis   │
│  :5432   │ │  :6379   │
└──────────┘ └──────────┘
```

Compose is responsible for creating and connecting these pieces.

Instead of manually doing something like:

```text
docker run ...
docker network create ...
docker run ...
docker run ...
docker volume create ...
```

the desired system is declared in one YAML file.

Compose then creates the required resources and starts the services accordingly. The original project described this as the core idea of Compose: **declare the desired state instead of manually constructing the stack.**

----------

# 2. The actual Compose file

The final `docker-compose.yml` is:

```yaml
services:

  api:
    build: .
    ports:
      - "8000:8000"

    environment:
      DATABASE_URL: postgresql+psycopg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@db:5432/${POSTGRES_DB}
      REDIS_URL: redis://cache:6379/0
      CODE_LENGTH: ${CODE_LENGTH:-7}

    depends_on:
      db:
        condition: service_healthy
      cache:
        condition: service_healthy


  db:
    image: postgres:16-alpine

    environment:
      POSTGRES_USER: ${POSTGRES_USER}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
      POSTGRES_DB: ${POSTGRES_DB}

    volumes:
      - pgdata:/var/lib/postgresql/data

    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER} -d ${POSTGRES_DB}"]
      interval: 5s
      timeout: 3s
      retries: 5


  cache:
    image: redis:7-alpine

    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 3s
      retries: 5


volumes:
  pgdata:

```

This file defines **three services and one named volume**.

----------

# 3. Why there is no `version:` key

Older Compose files often started with:

```yaml
version: "3.8"
```

This project deliberately doesn't have one.

<br>

With modern Docker Compose, the old `version:` field is obsolete. Your Compose version is modern, so the file simply starts with:

```yaml
services:
```

This was a deliberate choice in the project rather than something accidentally omitted.

----------

# 4. `services:` — the core of Compose

Everything under:

```yaml
services:
```

defines a containerized service.

Here:

```text
services
├── api
├── db
└── cache
```

These are three separate containers.

<br>


Importantly, a **service** is the Compose-level definition.

When Compose starts the project, it creates containers from those service definitions.

So:

```yaml
api:

```

doesn't mean "run FastAPI directly."
<br>

It means:

> Define the container/service that will run the FastAPI application.


<br>

Likewise:

```yaml
db:

```

means:

> Define the PostgreSQL service.


<br>

And:

```yaml
cache:

```

means:

> Define the Redis service.

----------

# 5. The `api` service

The first service is:

```yaml
api:
  build: .
```

This is where Compose connects to the Dockerfile you documented previously.

----------

# 6. `build: .`

This means:

> Build the image for the `api` service using the current directory as the build context.

So if you're in:

```text
~/docker-lab/snip
```

then:

```text
build: .
```

means approximately:

```text
~/docker-lab/snip
       │
       ▼
Docker build context
       │
       ▼
Dockerfile
       │
       ▼
API image
```

The API image is therefore **built locally**.

This differs from the database and Redis services, which use prebuilt images.
<br>

The project deliberately uses:

```yaml
api:
  build: .
```

but:

```yaml
db:
  image: postgres:16-alpine

cache:
  image: redis:7-alpine
```

That distinction is important.

----------

# 7. `build:` vs `image:`

These are two different ways of defining where a service's container image comes from.

### API

```yaml
build: .
```

means:

```text
our source code
      ↓
our Dockerfile
      ↓
our image
      ↓
container
```
<br>

### PostgreSQL

```yaml
image: postgres:16-alpine

```

means:

```text
existing image
      ↓
container
```

<br>


### Redis

```yaml
image: redis:7-alpine
```

means:

```text
existing image
      ↓
container
```

So the architecture is:

```text
              Compose
                 │
       ┌─────────┼─────────┐
       ▼         ▼         ▼
     build     image     image
       │         │         │
       ▼         ▼         ▼
      API      Postgres   Redis
```

The project deliberately doesn't build PostgreSQL or Redis itself because there is no reason to. Their official images already provide what is needed.

----------

# 8. Publishing the API port

The API contains:

```yaml
ports:
  - "8000:8000"
```
<br>

This is:

```text
HOST PORT : CONTAINER PORT
```

Therefore:

```text
8000:8000
```

means:

```text
localhost:8000
       │
       ▼
API container:8000
```

<br>


This is why you can run:

```bash
curl localhost:8000/health
```

from the host.

----------

# 9. Why PostgreSQL and Redis don't have `ports:`


The API does not need PostgreSQL or Redis to be reachable through the host.

It only needs to reach them through the internal Docker network.

The database and cache are internal services.

----------

# 10. The API environment

The API receives:

```yaml
environment:
  DATABASE_URL: postgresql+psycopg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@db:5432/${POSTGRES_DB}
  REDIS_URL: redis://cache:6379/0
  CODE_LENGTH: ${CODE_LENGTH:-7}
```

These are **environment variables inside the API container**.
<br>

They tell the application where its dependencies are and how it should behave.

Conceptually:

```text
API container
│
├── DATABASE_URL
├── REDIS_URL
└── CODE_LENGTH
```

Your `config.py` then reads these values through Pydantic Settings.

----------

# 11. The two meanings of `${VARIABLE}`

This is one of the most important Compose concepts in this project.

For example:

```yaml
POSTGRES_USER: ${POSTGRES_USER}
```

looks like a normal environment variable, but there are actually **two stages involved**.
<br>

### Stage 1 — Compose interpolation

Compose reads `.env` in the project directory.

For example:

```text
POSTGRES_USER=snip
POSTGRES_PASSWORD=snip
POSTGRES_DB=snip
CODE_LENGTH=7
```

Compose substitutes:

```text
${POSTGRES_USER}
```

with:

```text
snip
```

while parsing the YAML.<br>
<br>


### Stage 2 — container environment

The resulting values are then injected into the container.

So:

```yaml
POSTGRES_USER: ${POSTGRES_USER}
```

eventually becomes conceptually:

```yaml
POSTGRES_USER: snip
```

inside the PostgreSQL container.

The original project explicitly distinguished these two mechanisms because confusing them is a common source of mistakes.

----------

# 12. Why use `.env` instead of hardcoding values?

Without `.env`, you could theoretically write:

```yaml
POSTGRES_PASSWORD: snip
```

directly into Compose.
<br>

But then the credential becomes part of the Compose file.

Instead:

```text
.env
   │
   ├── POSTGRES_USER
   ├── POSTGRES_PASSWORD
   └── POSTGRES_DB
```

is kept outside version control.
<br>

And:

```text
.env.example
```

contains only the expected configuration structure.
<br>

The project explicitly established:

```text
.env.example
    ↓
committed template

.env
    ↓
real local values
    ↓
never committed
```

----------

# 13. `DATABASE_URL`

The API gets:

```yaml
DATABASE_URL: postgresql+psycopg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@db:5432/${POSTGRES_DB}
```
<br>

After Compose interpolation, yours becomes conceptually:

```text
postgresql+psycopg://snip:snip@db:5432/snip
```

This contains:

```text
postgresql+psycopg://
        │
        └── SQLAlchemy PostgreSQL dialect + driver

snip
        │
        └── username

:
snip
        │
        └── password

@
db
        │
        └── PostgreSQL hostname

:
5432
        │
        └── PostgreSQL port

/
snip
        │
        └── database name
```

----------

# 14. Why `db` instead of `localhost`?

This is probably the single most important networking detail in your Compose file.

The API is inside its own container.

Inside that container:

```text
localhost
```

means:

```text
the API container itself
```

It does **not** mean the PostgreSQL container.
<br>

PostgreSQL is a different container.

Its Compose service name is:

```text
db
```

Compose provides internal DNS, so:

```text
db
```

resolves to the PostgreSQL container.

<br>

The correct connection is:

```text
@db:5432

```

not:

```text
@localhost:5432

```

The project explicitly identified this as the canonical container-networking mistake to avoid.

----------

# 15. `REDIS_URL`

The API receives:

```yaml
REDIS_URL: redis://cache:6379/0
```

Again:

```text
cache
```

is not an IP address.
<br>

It is the **Compose service name**.

Docker's internal DNS resolves:

```text
cache
```

to the Redis container.

So:

```text
API
 │
 │ redis://cache:6379
 ▼
Redis container
```

The `/0` refers to Redis database 0.

----------

# 16. Service names become hostnames

This is the networking model you should remember from this project:

```text
services:
  api:
  db:
  cache:

```

automatically gives you names:

```text
api
db
cache

```

inside the Compose network.

----------

# 17. `CODE_LENGTH`

The final API environment variable is:

```yaml
CODE_LENGTH: ${CODE_LENGTH:-7}
```

The syntax:

```text
${VARIABLE:-default}
```

means:

> Use `VARIABLE` if it exists; otherwise use the default value.

So:

```text
CODE_LENGTH=7
```

results in:

```text
CODE_LENGTH=7
```

If it wasn't defined, Compose would use:

```text
7
```

This gives the application a sensible fallback.

The source project explicitly noted that Compose supports this shell-style default syntax.

----------

# 18. `depends_on`

The API then declares:

```yaml
depends_on:
  db:
    condition: service_healthy
  cache:
    condition: service_healthy

```

This describes startup dependencies.
<br>

The API depends on:

```text
Postgres
Redis

```

being ready first.

But there's an important subtlety.

----------

# 19. Container started ≠ service ready

Suppose Compose starts PostgreSQL:

```text
Postgres container
       │
       ▼
container process starts

```

That does **not** necessarily mean PostgreSQL is immediately accepting connections.
<br>

PostgreSQL may still be:

```text
initializing
creating database
running initdb
starting

```

So this sequence would be unsafe:

```text
start Postgres
      ↓
immediately start API
      ↓
API connects
      ↓
Postgres isn't ready
      ↓
connection refused
      ↓
API crashes

```

The project actually encountered this conceptual issue while designing the startup sequence.

The solution is the healthcheck.

----------

# 20. `service_healthy`

The API doesn't simply say:

```yaml
depends_on:
  - db

```

Instead it says:

```yaml
depends_on:
  db:
    condition: service_healthy

```

This means:

> Don't start the API until the `db` service has reported itself healthy.

Same for Redis.

So the startup becomes:

```text
                 Compose starts
                       │
              ┌────────┴────────┐
              ▼                 ▼
          PostgreSQL           Redis
              │                 │
         healthcheck        healthcheck
              │                 │
          HEALTHY             HEALTHY
              │                 │
              └────────┬────────┘
                       ▼
                      API

```

This eliminates the startup race.

----------

# 21. PostgreSQL service

Now:

```yaml
db:
  image: postgres:16-alpine

```

This uses the official PostgreSQL image.

The project selected:

```text
Postgres 16
Alpine

```

rather than building PostgreSQL itself.

The API talks to it over the internal Compose network.

----------

# 22. PostgreSQL environment variables

The database receives:

```yaml
environment:
  POSTGRES_USER: ${POSTGRES_USER}
  POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
  POSTGRES_DB: ${POSTGRES_DB}

```

These are consumed by the official PostgreSQL image during initialization.

They determine:

```text
database user
database password
initial database name

```

And importantly, the API uses the same values to construct its `DATABASE_URL`.

That gives you one source of truth.

----------

# 23. Why one source of truth matters

Imagine you had:

```text
Postgres password:
abc123

```

but:

```text
DATABASE_URL:
postgresql://snip:xyz789@db:5432/snip

```

The database would be configured with one password while the API tried another.

The application would fail to connect.

Instead, both are generated from:

```text
POSTGRES_PASSWORD

```

So:

```text
.env
 │
 └── POSTGRES_PASSWORD
       │
       ├──────────────► PostgreSQL
       │
       └──────────────► API DATABASE_URL

```

Change it once, and both sides change.

This prevents configuration drift.

----------

# 24. PostgreSQL volume

This is:

```yaml
volumes:
  - pgdata:/var/lib/postgresql/data

```

The syntax is:

```text
<volume-name>:<container-path>

```

So:

```text
pgdata

```

is the Docker-managed named volume.

And:

```text
/var/lib/postgresql/data

```

is where PostgreSQL stores its data inside the container.

Therefore:

```text
Docker volume: pgdata
        │
        ▼
Postgres container
/var/lib/postgresql/data

```

----------

# 25. Why PostgreSQL needs a volume

Containers are disposable.

If the PostgreSQL container itself is deleted, its writable container filesystem disappears.

Without a volume:

```text
Postgres container
      │
      └── database files

```

would mean:

```text
delete container
      ↓
delete database files

```

The volume changes that:

```text
Postgres container
      │
      ▼
/var/lib/postgresql/data
      │
      ▼
Docker named volume
pgdata

```

Now:

```text
delete container
      ↓
volume remains
      ↓
new Postgres container
      ↓
same database

```

This is why the project's links survive container recreation.

----------

# 26. Named volume vs bind mount

The project deliberately uses:

```yaml
pgdata:/var/lib/postgresql/data

```

rather than:

```yaml
./some-folder:/var/lib/postgresql/data

```

The first is a **named volume**.

The second would be a **bind mount**.

### Named volume

```text
Docker-managed storage

```

Good for:

```text
database data

```

### Bind mount

```text
host directory
       ↕
container directory

```

Useful when you want to directly edit/inspect host files.

For PostgreSQL's internal database files, a named volume is the cleaner choice here.

----------

# 27. `docker compose down` vs `down -v`

This distinction is extremely important.

### Normal:

```bash
docker compose down

```

removes the containers/network but keeps:

```text
pgdata

```

So your database survives.

### With `-v`:

```bash
docker compose down -v

```

the named volumes are deleted too.

Therefore:

```text
docker compose down
    ↓
containers gone
volume remains
database remains

docker compose down -v
    ↓
containers gone
volume gone
database gone

```

In your Makefile, this is why:

```text
down

```

and:

```text
nuke

```

are deliberately separate operations.

----------

# 28. PostgreSQL healthcheck

The database has:

```yaml
healthcheck:
  test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER} -d ${POSTGRES_DB}"]
  interval: 5s
  timeout: 3s
  retries: 5

```

This answers:

> "Is PostgreSQL actually ready to accept connections?"

----------

# 29. `pg_isready`

The actual check is:

```bash
pg_isready

```

with:

```text
-U username
-d database

```

So conceptually:

```text
Compose
   │
   ▼
run pg_isready
   │
   ▼
Postgres
   │
   ├── ready → success
   │
   └── not ready → failure

```

This is much better than merely checking:

```text
"Does the container exist?"

```

because what actually matters is whether PostgreSQL is usable.

----------

# 30. Healthcheck timing

The configuration is:

```yaml
interval: 5s
timeout: 3s
retries: 5

```

Meaning:

### `interval: 5s`

Check approximately every 5 seconds.

### `timeout: 3s`

A check that doesn't finish within 3 seconds is considered failed.

### `retries: 5`

Repeated failures eventually cause the service to be marked unhealthy.

The source notes specifically call out the 5-second interval and five retries.

----------

# 31. Redis service

Redis is:

```yaml
cache:
  image: redis:7-alpine

```

Again, this is an official prebuilt image.

The important architectural distinction is:

```text
Postgres = persistent source of truth

Redis = disposable cache

```

That difference determines how you treat their storage.

----------

# 32. Why Redis has no volume

Notice:

```yaml
cache:
  image: redis:7-alpine

```

but there is no:

```yaml
volumes:

```

That is deliberate.

Redis stores cached copies of data that ultimately exists in PostgreSQL.

Therefore:

```text
Redis disappears
      ↓
cache is empty
      ↓
application queries Postgres
      ↓
cache gets rebuilt

```

Nothing authoritative has been lost.

The project's design explicitly treats Redis as intentionally non-persistent because it is a cache.

----------

# 33. Redis healthcheck

Redis has:

```yaml
healthcheck:
  test: ["CMD", "redis-cli", "ping"]
  interval: 5s
  timeout: 3s
  retries: 5

```

The actual test is:

```bash
redis-cli ping

```

A healthy Redis server responds:

```text
PONG

```

So the logic is:

```text
redis-cli ping
      │
      ▼
    Redis
      │
      ▼
    PONG
      │
      ▼
  HEALTHY

```

Then Compose is allowed to start the API.

----------

# 34. Why Redis also needs a healthcheck

You could technically just start Redis before the API.

But the project deliberately makes the API wait for both dependencies.

That means the application has a consistent startup contract:

```text
API starts only when:

Postgres = healthy
Redis    = healthy

```

This makes the behavior deterministic instead of relying on timing luck.

----------

# 35. The `volumes:` section at the bottom

Finally:

```yaml
volumes:
  pgdata:

```

This declares the named volume.

Notice that we didn't specify a host directory.

Docker manages it.

Compose will create the actual project-scoped volume.

For example, your project resulted in a volume named conceptually:

```text
snip_pgdata

```

rather than simply:

```text
pgdata

```

because Compose scopes resources to the project name.

----------

# 36. Why the volume gets the `snip_` prefix

Compose derives a project name from the project directory unless you explicitly override it.

For this project:

```text
project name = snip

```

So Compose creates resources such as:

```text
snip_default
snip_pgdata

```

This prevents two Compose projects from accidentally using the same resource names.

For example:

```text
project A → snip_pgdata
project B → another_pgdata

```

The resources are scoped to their respective Compose projects.

----------

# 37. The default network

There is something interesting missing from the Compose file:

```yaml
networks:

```

There isn't one.

That's intentional.

Compose automatically creates a default network for the project and attaches the services to it.

Conceptually:

```text
snip_default
│
├── api
├── db
└── cache

```

This is why:

```text
db

```

and:

```text
cache

```

are reachable by hostname.

The project deliberately didn't create a custom network because there was no need for network segmentation at this stage.

----------

# 38. Why not create a custom network?

You could have written:

```yaml
networks:
  backend:

```

and explicitly attached all three services.

But for this project, that would add configuration without providing a meaningful benefit.

The application is only:

```text
API
DB
Redis

```

There is no frontend that needs to be isolated from the database, for example.

Therefore the default network is sufficient.

A custom network becomes more useful when you have requirements such as:

```text
Internet-facing frontend
        │
        ▼
    API network
        │
        ▼
   private DB network

```

That isn't necessary for Phase 1.

----------

# 39. The complete runtime topology

At runtime, the entire Compose system looks like:

```text
                         HOST
                          │
                          │ localhost:8000
                          ▼
                 ┌──────────────────┐
                 │       API        │
                 │                  │
                 │    FastAPI       │
                 │    Uvicorn       │
                 │    :8000         │
                 └───────┬──────────┘
                         │
                  snip_default network
                         │
              ┌──────────┴──────────┐
              │                     │
              ▼                     ▼
      ┌──────────────┐      ┌──────────────┐
      │   Postgres   │      │    Redis     │
      │              │      │              │
      │    :5432     │      │    :6379     │
      │              │      │              │
      │ source truth │      │    cache     │
      └───────┬──────┘      └──────────────┘
              │
              ▼
        ┌───────────┐
        │  pgdata   │
        │   volume  │
        └───────────┘

```

Only the API is exposed to the host.

Postgres and Redis remain internal.

----------

# 40. The startup sequence

When you execute:

```bash
docker compose up -d

```

Compose approximately does:

```text
1. Read docker-compose.yml
        │
2. Read .env
        │
3. Interpolate ${VARIABLES}
        │
4. Create project network
        │
5. Create pgdata volume
        │
6. Build/pull required images
        │
7. Start PostgreSQL
        │
8. Start Redis
        │
9. Run PostgreSQL healthcheck
        │
10. Run Redis healthcheck
        │
11. Wait until both are healthy
        │
12. Start API
        │
13. Publish host port 8000

```

This ordering is one of the main reasons the Compose file exists.

----------

# 41. What `docker compose config` taught you

Before actually starting the stack, the project used:

```bash
docker compose config

```

This is an important debugging/validation command.

It parses the Compose file and resolves the `.env` interpolation.

For example, it lets you verify that:

```text
DATABASE_URL

```

actually becomes:

```text
postgresql+psycopg://snip:snip@db:5432/snip

```

rather than something broken such as:

```text
postgresql+psycopg://:@localhost:5432/

```

The source chat specifically used this as a validation step before building/running.

----------

# 42. What the resolved Compose configuration revealed

The resolved configuration also showed:

```text
name: snip

```

which explains the resource names.

It showed the services attached to:

```text
default network

```

and showed:

```text
DATABASE_URL

```

using:

```text
@db:5432

```

It also showed:

```text
CODE_LENGTH: "7"

```

This is worth understanding:

Environment variables are strings.

So Compose gives the container:

```text
"7"

```

while Pydantic Settings converts it to the Python type expected by the application:

```python
code_length: int = 7

```

Therefore:

```text
Compose
  "7"
    ↓
environment variable
    ↓
Pydantic Settings
    ↓
Python int
  7

```

The project explicitly verified this behavior during configuration validation.

----------

# 43. Why the API doesn't get a `.env` file mounted

Your application configuration uses:

```text
env_file=".env"

```

for local development.

But the container does not actually need the `.env` file itself.

Compose reads `.env` on the host and injects the resulting values into the container using:

```yaml
environment:

```

So the flow is:

```text
host .env
    │
    ▼
Docker Compose interpolation
    │
    ▼
environment:
    │
    ▼
API container

```

The `.env` file itself does not need to exist inside `/app`.

The source notes explicitly explain this behavior.

----------

# 44. Why this matters for secrets

This is another security benefit.

The Docker image doesn't contain:

```text
.env

```

and the Dockerfile doesn't hardcode the password.

Instead, configuration is injected when the container starts.

So conceptually:

```text
IMAGE
│
├── application code
├── Python
└── dependencies

NO DATABASE PASSWORD

```

Then at runtime:

```text
Compose
    │
    └── injects configuration
            │
            ▼
       running container

```

This separates the image from environment-specific configuration.

----------

# 45. What happens if the API container is recreated?

Suppose:

```bash
docker compose down

```

then:

```bash
docker compose up -d

```

The API container is recreated.

Postgres may also be recreated.

But:

```text
pgdata

```

still exists.

Therefore:

```text
new Postgres container
        │
        ▼
existing pgdata volume
        │
        ▼
existing database

```

Redis starts empty.

That's okay because Redis is only the cache.

The application can repopulate it from PostgreSQL.

----------

# 46. What happens if Redis is deleted?

Suppose Redis disappears completely.

The result is:

```text
Redis cache
    ↓
empty

```

But:

```text
Postgres
    ↓
still contains links

```

The redirect path can therefore fall back to PostgreSQL and warm Redis again.

This is directly related to the cache-aside design documented in the README:

```text
Redis hit
    ↓
return URL

Redis miss
    ↓
Postgres
    ↓
populate Redis

```

The Compose storage decision supports that application architecture.

----------

# 47. Why the stack uses three containers

You could theoretically put everything into one container.

For example:

```text
one container
├── FastAPI
├── PostgreSQL
└── Redis

```

But that defeats one of the major benefits of containerization.

Instead:

```text
API
DB
Redis

```

are independently managed processes.

Each has:

```text
its own image
its own process
its own lifecycle
its own configuration
its own responsibility

```

That is a much cleaner architecture.

----------

# 48. The responsibility boundary

The three services have very clear responsibilities:

### API

```text
HTTP
routing
business logic
cache interaction
database interaction

```

### PostgreSQL

```text
persistent application data
links
click events

```

### Redis

```text
temporary cached URL lookups

```

Compose doesn't implement any of those application responsibilities.

It **connects and manages the pieces**.

----------

# 49. Why Compose is valuable even though Docker can do this manually

Everything Compose does could theoretically be recreated with individual Docker commands.

You could manually:

```text
create network
create volume
run Postgres
run Redis
build API
run API
configure environment
connect everything

```

But that would mean remembering a huge number of commands and flags.

Compose turns that into:

```bash
docker compose up -d

```

because the desired state is already described in YAML.

That is the real value.

----------

# 50. The entire Compose design in one picture

```text
                    docker-compose.yml
                           │
             ┌─────────────┼─────────────┐
             │             │             │
             ▼             ▼             ▼
            api            db           cache
             │             │             │
         build: .      postgres:16     redis:7
             │          -alpine         -alpine
             │             │             │
             │             │             │
             └─────────────┼─────────────┘
                           │
                    snip_default
                     Docker network
                           │
             ┌─────────────┴─────────────┐
             │                           │
          db:5432                     cache:6379
             │
             ▼
          pgdata
       named volume

Host
 │
 └── localhost:8000
          │
          ▼
         api

```

----------

# 51. The reasoning behind every major choice

This is the part you should eventually remember when looking at the repository months later.

Choice

Why

`api: build: .`

Build your own application image

`db: image: postgres:16-alpine`

Use official PostgreSQL image

`cache: image: redis:7-alpine`

Use official Redis image

`8000:8000`

Expose API to host

No DB port

DB only needs internal access

No Redis port

Redis only needs internal access

`db` hostname

Compose service discovery

`cache` hostname

Compose service discovery

`.env` interpolation

Separate configuration from YAML

Shared Postgres variables

One source of truth

`service_healthy`

Prevent startup race

`pg_isready`

Verify PostgreSQL is actually ready

`redis-cli ping`

Verify Redis is actually ready

`pgdata` volume

Persist database

No Redis volume

Cache is disposable

No custom network

Default Compose network is sufficient

No `version:`

Modern Compose doesn't need it

----------

# 52. The most important mental model

Don't think of Compose as:

> "A file containing Docker commands."

Think of it as:

> **A declaration of the runtime architecture.**

You are telling Compose:

```text
I want:

1 API service
1 PostgreSQL service
1 Redis service

The API needs:
    Postgres
    Redis

Postgres needs:
    persistent storage

The services need:
    private network communication

The API needs:
    host port 8000

The API must not start until:
    Postgres is healthy
    Redis is healthy

```

Compose figures out the mechanics.

----------

# 53. The three layers you've now built

At this point, your Docker architecture can be understood as three separate layers:

```text
                 APPLICATION
                     │
              FastAPI + Python
                     │
                     ▼
                  Dockerfile
                     │
              builds API image
                     │
                     ▼
              docker-compose.yml
                     │
        ┌────────────┼────────────┐
        ▼            ▼            ▼
       API         Postgres      Redis
        │            │            │
        └────────────┼────────────┘
                     │
               Docker network
                     │
                  pgdata
                   volume

```

### Dockerfile

Defines:

> **What is inside the API image?**

### Compose

Defines:

> **How do the containers run together?**

### Application code

Defines:

> **What does the system actually do?**

That separation is the architecture you were trying to learn through this project.

----------

# 54. What to remember when you reopen this repository

If six months from now you open:

```text
docker-compose.yml

```

you should be able to read it as:

```text
services:
    ├── api
    │   ├── build my application
    │   ├── expose port 8000
    │   ├── give it DB + Redis configuration
    │   └── wait for both dependencies to become healthy
    │
    ├── db
    │   ├── PostgreSQL 16
    │   ├── initialize using shared credentials
    │   ├── persist /var/lib/postgresql/data
    │   └── report health using pg_isready
    │
    └── cache
        ├── Redis 7
        └── report health using redis-cli ping

volume:
    pgdata
        ↓
    persistent Postgres storage

```

And the networking should immediately make sense:

```text
api → db:5432
api → cache:6379
host → localhost:8000

```

That is the Compose mental model for Snip.
