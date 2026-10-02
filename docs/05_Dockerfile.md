# Snip — Dockerfile & Image Build Internals

## 1. What the Dockerfile is responsible for

The Dockerfile defines how the **FastAPI application image** is built.

It answers:

> "Starting from a base image, what files, dependencies, environment, user, and startup command should exist inside the API container?"
<br>

It does **not** define:

-   PostgreSQL
    
-   Redis
    
-   the Docker network
    
-   the PostgreSQL volume
    
-   environment values for the whole stack
    
<br>

Those belong to `docker-compose.yml`.
<br>

The Dockerfile is specifically responsible for creating the image that runs:

```text
FastAPI application
        +
Python dependencies
        +
Uvicorn
```
<br>

The final container runs:

```text
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

The project uses a **multi-stage Docker build**.

----------

# 2. The Dockerfile

The final Dockerfile is:

```dockerfile
# ---------- Stage 1: builder ----------
FROM python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt


# ---------- Stage 2: runtime ----------
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /app

COPY --from=builder /opt/venv /opt/venv
COPY app ./app

RUN useradd --create-home --uid 1000 appuser
USER appuser

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

This was the exact structure used in the project.

----------

# 3. The most important concept: two stages

The Dockerfile contains:

```dockerfile
FROM python:3.12-slim AS builder
```

and later:

```dockerfile
FROM python:3.12-slim AS runtime
```

These are **two separate build stages**.
<br>

Think of them as two environments:

```text
┌──────────────────────────────┐
│          BUILDER             │
│                              │
│ Python                       │
│ pip                          │
│ virtualenv                   │
│ dependencies                 │
│ build-time junk              │
└──────────────┬───────────────┘
               │
               │ copy only what
               │ is required
               ▼
┌──────────────────────────────┐
│          RUNTIME             │
│                              │
│ Python                       │
│ virtualenv                   │
│ application code             │
│                              │
│ no builder environment       │
└──────────────────────────────┘
```

The builder is disposable.

The runtime stage is the image you actually run.

That is the central idea behind this Dockerfile.

----------

# 4. Why use multi-stage builds?

For this particular Python application, multi-stage builds are **less dramatic** than they would be for a compiled language such as Go.
<br>

The project's dependencies mostly have prebuilt wheels.

Therefore, we don't need a giant compiler toolchain just to build the application.
<br>

Two main reasons were identified.

### Reason 1 — keep build artifacts out of the runtime image

The builder handles dependency installation.
<br>

Only the finished virtual environment is copied into the runtime image.

```text
builder
   │
   ├── pip
   ├── pip cache
   ├── installation process
   └── /opt/venv
             │
             ▼
        runtime image
```

The runtime doesn't need the builder's environment.

### Reason 2 — future-proofing

Today, the dependencies may install from wheels.
<br>

Tomorrow, you might add something requiring compilation.

Then the builder can contain:

```text
gcc
make
headers
development libraries
```

while the runtime remains clean.
<br>

The project intentionally used the multi-stage pattern even though the immediate size savings aren't enormous.

----------

# 5. Stage 1 — builder

The first line is:

```dockerfile
FROM python:3.12-slim AS builder
```

This does three things.
<br>

### `FROM`

Defines the base image.

Here:

```text
python:3.12-slim
```

provides:

```text
Linux
+
Python 3.12
+
Python's standard runtime/tooling
```
<br>

### `3.12`

Pins the Python **major/minor version**.

The application was developed against Python 3.12.
<br>

### `slim`

Uses a smaller Debian-based Python image instead of the larger full Python image.
<br>

### `AS builder`

Gives this stage a name:

```text
builder
```

That name becomes important later:

```dockerfile
COPY --from=builder ...
```

It means:

> Copy something from the filesystem produced by the builder stage.

----------

# 6. Why not use the full Python image?

The project chose:

```text
python:3.12-slim
```

instead of:

```text
python:3.12
```

because the application doesn't need the larger collection of packages included in the full image.
<br>

The general principle is:

```text
start with the smallest sensible base
```

rather than:

```text
start with everything
```

This reduces unnecessary image contents.

It also keeps the runtime environment easier to reason about.

----------

# 7. `PIP_NO_CACHE_DIR`

The builder defines:

```dockerfile
ENV PIP_NO_CACHE_DIR=1
```

This tells pip not to retain downloaded package caches.
<br>

Normally pip can retain downloaded wheels/packages in its cache.
<br>

Those files aren't required to **run** the application after installation.

So:

```text
pip downloads package
       │
       ▼
install package
       │
       X
don't keep unnecessary download cache
```

This keeps the build environment cleaner.

The project specifically called this a "belt-and-braces" optimization because the builder itself doesn't survive into the final image anyway.

----------

# 8. `PIP_DISABLE_PIP_VERSION_CHECK`

The second environment variable is:

```dockerfile
PIP_DISABLE_PIP_VERSION_CHECK=1
```

This prevents pip from performing its version-check behavior during installation.
<br>

It isn't essential to application functionality.

It's simply a build hygiene choice:

```text
less unnecessary build-time work
```

The two variables are therefore build-stage configuration rather than application configuration.

----------

# 9. Creating the virtual environment

The builder executes:

```dockerfile
RUN python -m venv /opt/venv
```
<br>

This creates:

```text
/opt/venv
```

inside the image.
<br>

Conceptually:

```text
/opt/venv/
├── bin/
├── lib/
└── ...
```

The Python dependencies will be installed there.

----------

# 10. Why use a virtual environment inside Docker?

This is an interesting question because you might think:

> "Containers already isolate the application. Why do I need a Python venv?"

You technically **could** install the packages directly into the system Python environment inside the container.

But the project intentionally uses a venv because it creates a clean, self-contained directory:

```text
/opt/venv
```
<br>

That gives us a very convenient multi-stage boundary:

```text
builder:

/opt/venv
      │
      │ COPY --from=builder
      ▼
runtime:

/opt/venv
```

Instead of trying to figure out which files pip installed across multiple system directories, we can copy one directory.


----------

# 11. Changing `PATH`

Immediately afterward:

```dockerfile
ENV PATH="/opt/venv/bin:$PATH"
```

This tells the shell:

> Prefer executables from the virtual environment.
<br>

So instead of needing:

```text
/opt/venv/bin/pip
/opt/venv/bin/python
/opt/venv/bin/uvicorn
```

we can simply run:

```text
pip
python
uvicorn
```

and the system finds the versions inside `/opt/venv`.
<br>

This environment variable is also repeated in the runtime stage because the runtime needs to find:

```text
uvicorn
```

from the copied virtual environment.

----------

# 12. `WORKDIR /app`

Next:

```dockerfile
WORKDIR /app
```

This sets the working directory inside the image.
<br>

After this instruction, commands such as:

```dockerfile
COPY requirements.txt .
```

operate relative to:

```text
/app
```
<br>

So:

```text
COPY requirements.txt .
```

means:

```text
host build context/requirements.txt
                ↓
container:
/app/requirements.txt
```
<br>

Later:

```dockerfile
COPY app ./app
```

creates:

```text
/app/app/
```

inside the image.

----------

# 13. Why `requirements.txt` is copied separately

This is one of the most important lines in the Dockerfile:

```dockerfile
COPY requirements.txt .

```

followed by:

```dockerfile
RUN pip install -r requirements.txt

```
<br>

The order is intentional.

It is designed around **Docker's layer cache**.
<br>

Consider two files:

```text
requirements.txt
app/main.py

```
<br>

Which one changes more frequently?

Usually:

```text
requirements.txt
    ↓
changes occasionally

app/main.py
    ↓
changes frequently

```

So we install dependencies before copying application code.

----------

# 14. Docker layer caching

Docker builds an image in layers.

Conceptually:

```text
Layer 1
FROM python:3.12-slim

Layer 2
COPY requirements.txt

Layer 3
RUN pip install ...

Layer 4
COPY app

Layer 5
...

```

If `requirements.txt` hasn't changed, Docker can reuse the dependency-installation layer.

So if you modify:

```text
app/main.py

```

Docker doesn't need to reinstall:

```text
FastAPI
SQLAlchemy
psycopg
Redis
Pydantic Settings
...

```

It can reuse the existing layer.

This is why the order matters.

The project explicitly emphasized this as the layer-cache strategy.

----------

# 15. What would be the bad version?

Imagine the Dockerfile did:

```dockerfile
COPY . .
RUN pip install -r requirements.txt

```

Now every change to application code potentially invalidates the layer containing the dependency installation.

For example:

```text
change main.py
     ↓
COPY . . changes
     ↓
pip install layer invalidated
     ↓
dependencies installed again

```

That's wasteful.

The current design instead does:

```text
requirements.txt
       ↓
install dependencies
       ↓
app code

```

So:

```text
change main.py
       ↓
dependency layer stays cached
       ↓
only later layers rebuild

```

This is one of the most practical Docker optimization concepts in the project.

----------

# 16. Installing dependencies

The builder executes:

```dockerfile
RUN pip install -r requirements.txt

```

This reads:

```text
requirements.txt

```

which contains the project's Python dependencies:

```text
fastapi
uvicorn
sqlalchemy
psycopg
redis
pydantic-settings

```
<br>

They are installed into:

```text
/opt/venv

```

because the PATH has been changed to the virtual environment.
<br>

So after this step:

```text
builder
└── /opt/venv
    ├── FastAPI
    ├── Uvicorn
    ├── SQLAlchemy
    ├── psycopg
    ├── Redis client
    └── Pydantic Settings

```

----------

# 17. Stage 2 — runtime

Now the Dockerfile starts another image:

```dockerfile
FROM python:3.12-slim AS runtime

```

This is a **new stage**.
<br>

It does not inherit the filesystem from `builder`.

This distinction is crucial.

The runtime starts again from:

```text
python:3.12-slim

```

and then selectively receives what we want.

That is the whole point of multi-stage builds.

----------

# 18. Runtime environment variables

The runtime sets:

```dockerfile
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH"
```

These have different purposes.

----------

# 19. `PYTHONDONTWRITEBYTECODE=1`

Python normally creates compiled bytecode files:

```text
__pycache__/
*.pyc
```

Inside a container image, these aren't particularly useful to keep around.

The project therefore sets:

```text
PYTHONDONTWRITEBYTECODE=1

```

which tells Python not to create `.pyc` files.

The goal is simply to keep the container filesystem cleaner.

----------

# 20. `PYTHONUNBUFFERED=1`

This one is particularly important for containers:

```dockerfile
PYTHONUNBUFFERED=1
```

Python can buffer stdout.

Inside a container, that can create a confusing situation:

```text
application is running
       ↓
logs are buffered
       ↓
docker logs appears quiet
       ↓
you think something is wrong
```
<br>

With:

```text
PYTHONUNBUFFERED=1
```

output is emitted immediately.

So:

```text
application
    ↓
stdout
    ↓
Docker logging
    ↓
docker logs
```

behaves much more predictably.
<br>

The original project explicitly included this because container logging is something you want to work naturally.

----------

# 21. Runtime `PATH`

The runtime again contains:

```dockerfile
PATH="/opt/venv/bin:$PATH"
```

Why?

Because the virtual environment is about to be copied from the builder:

```dockerfile
COPY --from=builder /opt/venv /opt/venv
```
<br>

The runtime needs to know that:

```text
/opt/venv/bin
```

contains the executables.

Most importantly:

```text
/opt/venv/bin/uvicorn
```

----------

# 22. Runtime working directory

Again:

```dockerfile
WORKDIR /app
```
<br>

So the application will live under:

```text
/app/app/
```

and Uvicorn can import:

```text
app.main
```

from the working directory.

----------

# 23. The most important multi-stage instruction

This is the line that actually connects the two stages:

```dockerfile
COPY --from=builder /opt/venv /opt/venv
```

Read it literally:

```text
COPY
    from builder stage
        /opt/venv
    into runtime stage
        /opt/venv
```

So:

```text
BUILDER

/opt/venv
   │
   │
   │ COPY --from=builder
   ▼
RUNTIME

/opt/venv
```

Nothing else from the builder needs to come across.

----------

# 24. What does NOT get copied?

The runtime does not inherit:

```text
pip cache
builder filesystem
temporary build files
builder-specific layers
```

Only:

```text
/opt/venv
```

is explicitly transferred.
<br>

This is the actual payoff of multi-stage builds.

The builder can be messy because the runtime only receives what you deliberately copy from it.

----------

# 25. Copying the application

Next:

```dockerfile
COPY app ./app
```

This copies only the application directory.
<br>

The result is:

```text
/app
└── app
    ├── __init__.py
    ├── config.py
    ├── db.py
    ├── models.py
    ├── schemas.py
    ├── cache.py
    └── main.py
```

This was deliberately **not** written as:

```dockerfile
COPY . .
```

----------

# 26. Why not `COPY . .`?

This is both a security and cleanliness decision.

The project directory contains things such as:

```text
.env
.git
docker-compose.yml
Dockerfile
README.md
Makefile
```

You don't want all of that inside the application runtime image.

Especially:

```text
.env
```

because it can contain secrets.

Instead:

```dockerfile
COPY app ./app

```

means:

```text
only application code

```

enters the runtime image.

The original project explicitly called out this distinction.

----------

# 27. `.dockerignore` still matters

Even though the Dockerfile only copies:

```text
app/
requirements.txt

```

the Docker build still starts with a **build context**.
<br>

When you run:

```bash
docker build .

```

the `.` means:

> Use the current directory as the build context.

Docker processes that context before executing the Dockerfile.
<br>

The project therefore created:

```text
.dockerignore

```

to prevent unnecessary files from entering the build context.
<br>

The project's `.dockerignore` excludes things such as:

```text
.git
.env
__pycache__
*.pyc
.venv
pytest cache
logs

```

and similar development artifacts.

----------

# 28. Build context vs `COPY`

This distinction is worth remembering.
<br>

There are two separate concepts:

```text
BUILD CONTEXT
    ↓
what Docker is allowed to receive from the host

COPY
    ↓
what the Dockerfile actually places into an image layer

```
<br>

For example:

```text
.dockerignore
    ↓
prevents .git from entering the context

COPY app ./app
    ↓
only app/ gets copied into the image

```

So you want both:

```text
.dockerignore
+
deliberate COPY instructions

```

They solve related but different problems.

----------

# 29. Creating a non-root user

The next instruction is:

```dockerfile
RUN useradd --create-home --uid 1000 appuser

```
<br>

By default, many containers start processes as:

```text
root

```
<br>

The project deliberately avoids that.

It creates:

```text
appuser

```

with:

```text
UID 1000

```

and gives the user a home directory.

----------

# 30. Why not run the application as root?

Running an application as root inside a container gives the application more privileges than it actually needs.
<br>

The better principle is:

```text
application
    ↓
minimum privileges necessary

```

rather than:

```text
application
    ↓
root

```
<br>

If the application process is compromised, reducing its privileges reduces the potential impact.

The project included this specifically as an image-hardening practice.

----------

# 31. `USER appuser`

Creating the user isn't enough.
<br>

This instruction actually switches the process:

```dockerfile
USER appuser

```

Everything after this point runs as:

```text
appuser

```

including the final Uvicorn process.
<br>

So the runtime becomes:

```text
container
└── appuser
    └── uvicorn
        └── FastAPI

```

rather than:

```text
container
└── root
    └── uvicorn

```

----------

# 32. Why UID 1000?

The Dockerfile uses:

```text
--uid 1000

```

UID 1000 is commonly used for the first non-root user on Linux systems.
<br>

The project's original reasoning was to use a normal non-root identity rather than relying on root.
<br>

The exact numeric UID isn't what makes the security principle work.

The important part is:

```text
not root
```

----------

# 33. `EXPOSE 8000`

The Dockerfile then says:

```dockerfile
EXPOSE 8000

```

This does **not** publish the port.
<br>

This is one of the easiest Docker concepts to misunderstand.

It means roughly:

> The application inside this image expects to listen on port 8000.

It is documentation/metadata.
<br>

It does not mean:

```text
host:8000 → container:8000

```

by itself.

That mapping is configured by Compose.

The original project explicitly called out this distinction.

----------

# 34. Container port vs host port

The application listens on:

```text
container:
8000

```

Compose can then publish:

```text
host:
8000
        ↓
container:
8000

```

Conceptually:

```text
Browser/curl
     │
     ▼
localhost:8000
     │
     ▼
Docker port mapping
     │
     ▼
API container :8000
     │
     ▼
Uvicorn

```

`EXPOSE 8000` simply documents the last part.

----------

# 35. The final `CMD`

The final line is:

```dockerfile
CMD [
    "uvicorn",
    "app.main:app",
    "--host",
    "0.0.0.0",
    "--port",
    "8000"
]

```

This is the default command executed when the container starts.

It launches Uvicorn.

----------

# 36. Why Uvicorn instead of `python main.py`?

FastAPI is the **web framework**.
<br>

Uvicorn is the **ASGI server**.

The relationship is:

```text
HTTP request
     │
     ▼
Uvicorn
     │
     ▼
FastAPI application
     │
     ▼
route handler

```

So:

```text
FastAPI ≠ web server

```

FastAPI defines the application.

Uvicorn runs it.

This distinction was explicitly established when the project chose the dependencies.

----------

# 37. Understanding `app.main:app`

This part:

```text
app.main:app

```

looks strange until you split it:

```text
app.main
   │
   └── Python module

:
   │
   └── object separator

app
   │
   └── FastAPI instance

```
<br>

So:

```text
app.main:app

```

means:

> Import the `app` object from the `app.main` Python module.
<br>

The project has:

```text
app/
└── main.py

```

and inside that file:

```python
app = FastAPI(...)

```

Therefore Uvicorn knows what application to serve.

----------

# 38. Why `--host 0.0.0.0`?

This is another extremely important container detail.

The command uses:

```text
--host 0.0.0.0

```

rather than:

```text
--host 127.0.0.1

```
<br>

Inside a container:

```text
127.0.0.1

```

means:

```text
the container's own loopback interface

```
<br>

If Uvicorn only listened there, external Docker traffic wouldn't be able to reach the application correctly.

Using:

```text
0.0.0.0

```

means:

> Listen on all interfaces inside the container.
<br>

So the flow becomes:

```text
host
  │
  ▼
Docker networking
  │
  ▼
container network interface
  │
  ▼
0.0.0.0:8000
  │
  ▼
Uvicorn

```

----------

# 39. Why `--port 8000`?

The application was designed around:

```text
8000

```

so Uvicorn listens on:
<br>

```text
0.0.0.0:8000

```

Compose then publishes that container port to the host.
<br>

The same port was chosen consistently across:

```text
Dockerfile
Compose
FastAPI/Uvicorn
README

```

which avoids unnecessary translation.

----------

# 40. What the final image conceptually contains

After the build, you can think of the runtime image as:

```text
python:3.12-slim
│
├── /opt/venv
│   ├── FastAPI
│   ├── Uvicorn
│   ├── SQLAlchemy
│   ├── psycopg
│   ├── Redis
│   └── Pydantic Settings
│
└── /app
    └── app
        ├── __init__.py
        ├── config.py
        ├── db.py
        ├── models.py
        ├── schemas.py
        ├── cache.py
        └── main.py

```

And the process is:

```text
appuser
   │
   ▼
uvicorn
   │
   ▼
app.main:app

```

----------

# 41. What the final image does NOT contain

It does not need:

```text
PostgreSQL
Redis server
Docker Compose
Git
.env
README
Makefile
host Python
WSL
your source repository

```

Those are external to the API runtime.
<br>

The API container only needs:

```text
Python
+
Python dependencies
+
application code

```

That separation is a major part of containerization.

----------

# 42. The complete build process

When you run:

```bash
docker compose build

```

the conceptual sequence is:

```text
Docker build context
        │
        ▼
.dockerignore filtering
        │
        ▼
Stage 1: builder
        │
        ├── python:3.12-slim
        │
        ├── create /opt/venv
        │
        ├── copy requirements.txt
        │
        └── pip install
        │
        ▼
Stage 2: runtime
        │
        ├── python:3.12-slim
        │
        ├── copy /opt/venv
        │
        ├── copy app/
        │
        ├── create appuser
        │
        └── set CMD
        │
        ▼
Final API image

```

The builder stage exists to **produce artifacts**.

The runtime stage exists to **run the application**.

----------

# 43. What happens when `main.py` changes?

This is where the layer design becomes useful.

Suppose you modify:

```text
app/main.py

```

and rebuild.

<br>


Docker can generally reuse:

```text
FROM python:3.12-slim

```

and:

```text
COPY requirements.txt

```

and:

```text
RUN pip install

```

because the dependency inputs haven't changed.
<br>

Then:

```text
COPY app ./app

```

changes.
<br>

So the later layers rebuild.

Conceptually:

```text
Base image             CACHED
requirements           CACHED
pip install            CACHED
copy app               REBUILD
runtime setup          REBUILD

```

That's exactly why the Dockerfile separates dependencies from source code.

----------

# 44. What happens when `requirements.txt` changes?

Now suppose you add:

```text
httpx

```

to `requirements.txt`.
<br>

The dependency input changes.

Therefore:

```text
COPY requirements.txt
        ↓
changed
        ↓
pip install layer invalidated
        ↓
dependencies installed again

```

This is correct.

Docker cannot safely reuse the old dependency layer because its input has changed.
<br>

Then the new `/opt/venv` is copied into the runtime stage.

----------

# 45. Why the Dockerfile copies `app/` after dependencies

The ordering is therefore based on **change frequency**:

```text
rarely changing
       ↓
requirements.txt
       ↓
pip install
       ↓
frequently changing
       ↓
app/

```

The general Docker optimization you should remember is:

> Put expensive, rarely-changing operations before frequently-changing files.

This is more important than memorizing this specific Dockerfile.

----------

# 46. Security model of this image

The image has several deliberate security properties:

```text
1. slim base image
2. only required application files copied
3. .env excluded from build context
4. multi-stage build
5. application runs as non-root
6. secrets aren't baked into the Dockerfile

```

This isn't a complete production security model.

But it establishes good fundamentals.

----------

# 47. What was deliberately NOT done

The project did not:

### Copy the entire repository

```dockerfile
COPY . .

```

Instead:

```dockerfile
COPY app ./app

```
<br>

### Run as root

Instead:

```dockerfile
USER appuser

```
<br>

### Install dependencies after copying source

Instead:

```dockerfile
COPY requirements.txt .
RUN pip install ...
COPY app ./app

```
<br>

### Put secrets in the image

The `.env` file is excluded.
<br>

### Use the full Python image

Instead:

```text
python:3.12-slim

```
<br>

### Run FastAPI directly as if it were the server

Instead:

```text
uvicorn

```

Each of these choices exists for a reason.

----------

# 48. Dockerfile vs `.dockerignore`

These two files are related but should not be mentally merged.
<br>

### Dockerfile

Controls:

```text
what becomes part of the image

```
<br>

### `.dockerignore`

Controls:

```text
what gets sent as build context

```

For example:

```text
.git/

```

in `.dockerignore` prevents Git data from being sent to Docker.
<br>

Meanwhile:

```dockerfile
COPY app ./app

```

ensures only application code is copied into the runtime image.

Both are useful.

----------

# 49. Dockerfile vs Compose

Another distinction worth preserving.

### Dockerfile

Answers:

> How do I build the API image?
<br>

### Compose

Answers:

> How do I run the entire Snip system?

Compose handles:

```text
API
PostgreSQL
Redis
network
ports
environment
volume
healthchecks
startup relationships

```
<br>

The Dockerfile handles:

```text
Python runtime
dependencies
application files
user
startup command

```

So:

```text
Dockerfile
    ↓
build one image

Compose
    ↓
run the whole system

```

----------

# 50. The final mental model

If you forget everything else, remember the Dockerfile as a **factory**:

```text
                 Dockerfile
                      │
                      ▼
             ┌─────────────────┐
             │     BUILDER     │
             │                 │
             │ Python          │
             │ pip             │
             │ venv            │
             │ dependencies    │
             └────────┬────────┘
                      │
                 /opt/venv
                      │
                      ▼
             ┌─────────────────┐
             │     RUNTIME     │
             │                 │
             │ Python          │
             │ dependencies    │
             │ app/            │
             │ appuser         │
             │                 │
             │ CMD: uvicorn    │
             └────────┬────────┘
                      │
                      ▼
                API container
                      │
              ┌───────┴───────┐
              ▼               ▼
           Postgres          Redis

```

The key decisions were:

```text
multi-stage
    ↓
separate building from running

python:3.12-slim
    ↓
small sensible base

venv
    ↓
self-contained dependency directory

requirements first
    ↓
better Docker layer caching

COPY app separately
    ↓
avoid rebuilding dependencies on code changes

.dockerignore
    ↓
keep build context clean and secrets out

non-root user
    ↓
reduce runtime privileges

EXPOSE 8000
    ↓
document expected container port

CMD uvicorn
    ↓
start the actual ASGI server

0.0.0.0
    ↓
listen on the container's network interfaces

```
<br>

The most important thing to retain isn't the syntax of the Dockerfile.

It's the **reasoning chain**:

```text
What does the application need to RUN?
                ↓
Python + dependencies + source
                ↓
What does it need only to BUILD?
                ↓
dependency installation environment
                ↓
Separate those two
                ↓
multi-stage build
                ↓
Order instructions by change frequency
                ↓
good layer caching
                ↓
Copy only what runtime actually needs
                ↓
smaller + cleaner + safer image

```

That is why this Dockerfile looks the way it does.
