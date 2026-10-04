# Snip — Phase 2: Container Hardening

## 1. Objective

Phase 2 focused exclusively on hardening the Docker environment for the Snip application.

The existing Dockerized stack was brought closer to a production-style environment by introducing security controls, resource constraints, controlled filesystem access, predictable process management, and operational safeguards.

The primary goal was to move the containers from simply being **functional** to being **appropriately constrained and hardened for production use**.

----------

## 2. Scope

Phase 2 was intentionally limited to the container environment.
<br>

### Included

-   Docker Compose security configuration
    
-   Container privilege reduction
    
-   Linux capability restrictions
    
-   Filesystem restrictions
    
-   Temporary filesystem configuration
    
-   CPU and memory limits
    
-   Container log rotation
    
-   Container process management
    
-   Graceful shutdown behavior
    
-   Verification of the resulting configuration

<br>

### Not included

-   New application features
    
-   API redesign
    
-   Database schema changes
    
-   Redis application-level changes
    
-   Application architecture changes
    
-   New external services
    
<br>

The application behavior from Phase 1 was retained while the container environment around it was hardened.

----------

## 3. Final Container Architecture

The Snip stack consists of three containers:

| Container | Image | Role |
|-----------|-------|------|
| `api` | Snip API image | FastAPI application |
| `db` | `postgres:16-alpine` | PostgreSQL database |
| `cache` | `redis:7-alpine` | Redis cache |


Phase 2 did not change the purpose of these services. The changes were focused on how each service is allowed to operate inside its container.

The final hardening approach is intentionally **service-specific** rather than applying identical security settings to every container.
<br>

The API is the application's own image, so it can be hardened aggressively.

PostgreSQL and Redis use official images whose entrypoints perform privileged initialization before switching to their service users. 

Applying the same restrictions blindly to those containers would prevent them from starting correctly.

----------

## 4. Security Hardening

### 4.1 Non-root Execution

The API container runs using a dedicated non-root user:

```text
appuser
```

The API therefore does not require root privileges during normal application execution.

PostgreSQL and Redis also ultimately run their services as their respective service users.
<br>

This reduces the impact of a potential application or process compromise because an attacker does not automatically receive root privileges inside the container.

**The important distinction is that PostgreSQL and Redis may temporarily start with elevated privileges during their official entrypoint initialization before dropping to their service accounts.**

----------

### 4.2 `no-new-privileges`

All three containers use:

```yaml
security_opt:
  - no-new-privileges:true
```

**This prevents processes inside the container from gaining additional privileges through mechanisms such as setuid/setgid binaries.**

The control is applied consistently across:

-   API
    
-   PostgreSQL
    
-   Redis
    

It therefore establishes a privilege-escalation boundary even where the individual container images have different startup requirements.

----------

### 4.3 Linux Capabilities

Linux capabilities allow processes to perform specific privileged operations without giving them unrestricted root privileges.
<br>

**The API does not require Linux capabilities during normal execution, so all capabilities are removed:**

```yaml
cap_drop:
  - ALL
```

This results in the API having:

```text
CapBnd: 0x0
CapEff: 0x0
```
<br>

The API was also tested to ensure that even UID 0 could not simply bypass normal filesystem permissions after capabilities were removed.


<br>

#### Why PostgreSQL and Redis do not use `cap_drop: ALL`

The database and cache containers use official images whose entrypoints perform initialization tasks such as:

-   changing ownership of data directories
    
-   changing user/group identity
    
-   preparing the service environment
    

These operations require capabilities such as:

```text
CAP_CHOWN
CAP_FOWNER
CAP_SETUID
CAP_SETGID
```

Removing every capability from these containers caused startup failure.
<br>

Therefore, the final configuration deliberately does **not** use `cap_drop: ALL` for PostgreSQL and Redis.

This is not an omission. It is a deliberate compatibility and security decision based on how the official images initialize themselves.
<br>

After initialization, both services run as their respective non-root service users with:

```text
CapEff: 0x0
```

The API can therefore be hardened more aggressively because it controls its own image and startup process, while the third-party service images receive the restrictions that are compatible with their initialization model.

----------

### 4.4 Read-only API Filesystem

The API container uses:

```yaml
read_only: true
```

The API does not need to write persistent data to its container filesystem during normal operation.
<br>

Making the root filesystem read-only prevents an attacker or compromised application process from freely modifying files inside the container.

Verification confirmed:

```text
touch /home/appuser/canary
→ Read-only file system
```

The restriction remained effective even though the application continued to operate normally.

----------

### 4.5 `tmpfs` for `/tmp`

Although the API filesystem is read-only, applications may still require temporary filesystem access.
<br>

A temporary filesystem is therefore mounted at:

```text
/tmp
```

This allows temporary runtime data without making the entire container filesystem writable.
<br>

Verification confirmed:

```text
touch /tmp/canary
→ tmpfs wrote OK
```
<br>

The resulting model is:

```text
Container root filesystem
        │
        └── READ ONLY

/tmp
  │
  └── Writable temporary filesystem
```

**This provides the application with the temporary write capability it needs without restoring general write access to the container filesystem.**

----------

## 5. Resource Controls

### 5.1 CPU Limits

Each service is restricted to:

```text
1 CPU
```

This prevents a single container from consuming unlimited CPU resources on the host.
<br>

The limit applies to:

-   API
    
-   PostgreSQL
    
-   Redis
    

----------

### 5.2 Memory Limits

Each container has a memory limit of:

```text
256 MB
```

**This creates an explicit resource boundary between the containers and the host.**

**Without a memory limit, a container experiencing abnormal memory consumption could consume a disproportionate amount of host memory.**

The Phase 2 configuration therefore establishes:

```text
API      → 256 MB
Postgres → 256 MB
Redis    → 256 MB
```

**Please note that these values are deployment constraints for the current environment rather than performance benchmarks or capacity recommendations.**

----------

### 5.3 Log Rotation

Container logging is configured with:

```text
10 MB maximum per log file
3 rotated files
```

This prevents Docker's JSON logs from growing indefinitely.

<br>


The effective configured allowance is approximately:

```text
10 MB × 3 = 30 MB
```

per container before older log data is rotated out.


<br>

The same logging policy is applied to the API, PostgreSQL, and Redis containers.

----------

## 6. Process & Shutdown Management

### 6.1 Why Graceful Shutdown Matters

A container is not always stopped because the application has finished its work.
<br>
In a production environment, containers may be stopped because of:

-   a new deployment
    
-   a container restart
    
-   a configuration change
    
-   a host shutdown
    
-   a health or orchestration decision
    
-   scaling operations
    
<br>

When Docker stops a container normally, it does not immediately kill the application.

The basic sequence is:

```text
docker stop
    │
    ▼
SIGTERM
    │
    │  application gets time to shut down
    │
    ▼
grace period expires
    │
    ▼
SIGKILL
```

`SIGTERM` is effectively a request to the application:

> "You are being stopped. Shut down cleanly."

<br>

The application can use this time to:

-   stop accepting new work
    
-   finish or cancel active requests
    
-   close database connections
    
-   close Redis connections
    
-   flush pending data
    
-   release resources
    
-   execute application shutdown handlers
    

If the application has not exited when the grace period expires, Docker sends `SIGKILL`.

<br>


`SIGKILL` cannot be handled by the application. The process is terminated immediately.

Therefore, the difference is important:

```text
Graceful shutdown:

SIGTERM
   │
   ▼
Application cleanup
   │
   ▼
Process exits normally
```

```
Forced shutdown:

SIGTERM
   │
   ▼
Grace period expires
   │
   ▼
SIGKILL
   │
   ▼
Process is immediately terminated
```
<br>

**For a service such as Snip, this matters because a future version of the application may have work that must be completed before the process exits.**

**For example, if Phase 3 introduces buffered Redis writes, the application may have data waiting in memory when a container receives `SIGTERM`.**

**A graceful shutdown gives the application an opportunity to flush that data before the container disappears.**

----------

### 6.2 Why PID 1 Matters

Every container has a process with PID 1.

Without an init process, the API's Uvicorn process is PID 1:

```text
PID 1
└── uvicorn
```
<br>

PID 1 has special responsibilities inside a Linux container.

- It is responsible for handling signals sent to the container and for reaping orphaned child processes.

Although Uvicorn correctly handles termination signals, relying directly on the application server to perform all PID 1 responsibilities couples container lifecycle management to the application process.
<br>

Phase 2 therefore adds a small init process using:

```yaml
init: true
```
<br>

Docker injects `tini` as PID 1:

```text
PID 1
└── tini
    └── uvicorn
```

Now the responsibilities are separated:

```text
Docker
  │
  │ SIGTERM
  ▼
tini
  │
  │ forwards signal
  ▼
uvicorn
  │
  │ graceful shutdown
  ▼
application exits
```
<br>

`tini` provides the container-level PID 1 behavior:

-   receives signals sent to the container
    
-   forwards them to the application
    
-   reaps orphaned child processes
    
<br>

Uvicorn remains responsible for shutting down the FastAPI application itself.

This separation is useful because **container lifecycle management and application lifecycle management are different responsibilities**.

----------

### 6.3 Explicit Shutdown Grace Period

The API is configured with:

```yaml
stop_grace_period: 10s
```

This makes the shutdown contract explicit:

```text
SIGTERM
   │
   ├── up to 10 seconds
   │
   ▼
Application exits
```
<br>

If the application has not exited after that period:

```text
SIGKILL
```

The 10-second value is not intended to mean that every shutdown should take 10 seconds.

It establishes the maximum amount of time Docker will allow the API to perform its graceful shutdown before forcing termination.

----------

### 6.4 Shutdown Verification

The configuration was tested rather than simply assumed to work.

A normal container stop produced:

```text
INFO:     Shutting down
INFO:     Waiting for application shutdown.
INFO:     Application shutdown complete.
INFO:     Finished server process
```

This demonstrates the expected sequence:

```text
SIGTERM
   │
   ▼
Uvicorn receives termination
   │
   ▼
FastAPI shutdown lifecycle executes
   │
   ▼
Application completes shutdown
   │
   ▼
Process exits
```

The opposite behavior was also tested using:

```bash
docker compose stop -t 0 api
```

With a zero-second grace period, the application does not receive the same opportunity to complete its normal shutdown sequence before Docker forcefully terminates it.

**The comparison demonstrated why the graceful shutdown configuration exists rather than merely proving that the YAML option was accepted.**

----------

### 6.5 Why This Matters for Snip

The current Snip application has relatively little shutdown work to perform.
<br>

That does **not** make graceful shutdown unnecessary.
<br>

**The purpose of Phase 2 is to establish the correct container behavior before the application becomes more complex.**

A simple application can currently shut down almost immediately:

```text
SIGTERM
  ↓
No significant cleanup
  ↓
Exit
```
<br>

A future application may instead have:

```text
SIGTERM
  ↓
Stop accepting new requests
  ↓
Finish active work
  ↓
Flush buffered data
  ↓
Close Redis connections
  ↓
Close database connections
  ↓
Exit
```

Phase 2 therefore establishes the infrastructure necessary for that behavior without requiring the application to implement additional functionality yet.

This is the distinction between **making the application do more** and **making the container capable of shutting the application down correctly**.



----------

## 7. Why API / DB / Cache Are Hardened Differently

The final configuration intentionally does not treat all containers identically.

| Control | API | PostgreSQL | Redis |
|---------|-----|------------|-------|
| Non-root runtime | Yes | Yes* | Yes* |
| `no-new-privileges` | Yes | Yes | Yes |
| `cap_drop: ALL` | Yes | No | No |
| Read-only root filesystem | Yes | No | No |
| `/tmp` tmpfs | Yes | No | No |
| CPU limit | 1 CPU | 1 CPU | 1 CPU |
| Memory limit | 256 MB | 256 MB | 256 MB |
| Log rotation | Yes | Yes | Yes |
| Init process | Yes | — | — |

`*` PostgreSQL and Redis perform privileged initialization through their official entrypoints before dropping to their service accounts.

<br>


The important principle is:

> **Hardening should be applied according to the actual requirements of each container, not by blindly applying the same security settings everywhere.**

<br>

**The API can use a read-only filesystem because it does not require persistent writes.**

**The database cannot use a read-only filesystem because PostgreSQL must write its database state.**

Likewise, completely dropping capabilities from PostgreSQL and Redis prevents their official startup procedures from completing.

----------

## 8. Verification & Testing

Phase 2 hardening was verified through both Docker-level inspection and behavior-based tests.

### 8.1 Compose Configuration

The final Compose configuration was validated with:

```bash
docker compose config > /dev/null && echo "YAML OK"
```

Result:

```text
YAML OK

```

----------

### 8.2 Container Security Configuration

Container configuration was inspected using:

```bash
docker inspect snip-api-1
```

Relevant properties included:

```text
ReadonlyRootfs
Init
CapDrop
SecurityOpt
Memory
NanoCpus
LogConfig
```

**This verified that the intended Docker configuration was actually applied to the running container rather than simply existing in the Compose file.**

----------

### 8.3 Kernel-level Capability Verification

Capabilities were checked from inside the containers using:

```bash
docker compose exec api cat /proc/1/status | grep -E '^(Uid|CapEff|CapBnd)'
```

This provided the kernel's view of the running process rather than relying solely on Docker's configuration output.
<br>

The API reached:

```text
CapBnd: 0x0
CapEff: 0x0
```
<br>

PostgreSQL retained its capability bounding set for startup compatibility but had:

```text
CapEff: 0x0
```

during normal execution.

----------

### 8.4 Filesystem Write Tests

The API filesystem was deliberately tested rather than assuming `read_only` was functioning.
<br>

Attempting to write to the application filesystem:

```bash
docker compose exec api sh -c \
'touch /home/appuser/canary && echo "WROTE (bad)" || echo "BLOCKED"'
```

resulted in:

```text
Read-only file system
BLOCKED
```
<br>

Writing to `/tmp` succeeded:

```text
tmpfs wrote OK
```
<br>

This verified both sides of the filesystem policy:

```text
Application filesystem → blocked
/tmp                   → writable
```

----------

### 8.5 Graceful Shutdown Test

The API was stopped normally and its logs inspected.
<br>

The expected shutdown sequence was observed:

```text
Shutting down
Waiting for application shutdown
Application shutdown complete
Finished server process
```

The container was then stopped with zero grace time to demonstrate the difference between graceful and forced termination.

----------

### 8.6 Application Integrity

The hardening changes did not replace or modify the application's core behavior.

The existing stack remained functional after the container-level restrictions were introduced.

**This was important because Phase 2 was specifically intended to improve the container environment without introducing application-level changes.**

----------

## 9. Final `docker-compose` Configuration

The final Phase 2 configuration applies the hardening controls at the Compose layer.
<br>

The resulting design can be summarized as:

```yaml
api:
  user: appuser
  security_opt:
    - no-new-privileges:true
  cap_drop:
    - ALL
  read_only: true
  tmpfs:
    - /tmp
  init: true
  stop_grace_period: 10s
  deploy:
    resources:
      limits:
        cpus: "1.0"
        memory: 256M
  logging:
    driver: json-file
    options:
      max-size: "10m"
      max-file: "3"
```

Equivalent resource and logging controls are applied to PostgreSQL and Redis.

PostgreSQL and Redis intentionally do not receive the API's `cap_drop: ALL` or `read_only` configuration because their startup and runtime requirements differ.

The exact production configuration remains defined by the project's `docker-compose.yml`; the block above represents the Phase 2 hardening model rather than a replacement for the complete Compose file.

----------

## 10. Phase 2 Outcome

**Phase 2 moved the Snip Docker environment from a functional containerized deployment toward a more production-oriented container configuration.**

The major improvements were:

-   Reduced container privileges
    
-   Non-root API execution
    
-   Disabled privilege escalation
    
-   Removed unnecessary API capabilities
    
-   Read-only API filesystem
    
-   Controlled temporary writes through `tmpfs`
    
-   Explicit CPU limits
    
-   Explicit memory limits
    
-   Container log rotation
    
-   Dedicated init process
    
-   Explicit graceful shutdown behavior
    
-   Verification through Docker configuration, kernel state, filesystem tests, and shutdown testing
    
<br>

The key result is not simply that additional Docker options were added.
<br>

The Phase 2 work established **explicit boundaries around what each container is allowed to do**.
<br>

**The API cannot freely write to its filesystem, cannot acquire additional privileges, has no Linux capabilities, and cannot consume unlimited host resources.**

**At the same time, PostgreSQL and Redis retain only the permissions necessary for their actual image initialization and service operation.**

Phase 2 therefore focused on making the existing application **safer and more predictable as a containerized workload**, without changing what the application itself does.

----------

## 11. Phase 3 Boundary

Phase 3 is intentionally outside the scope of this hardening work.

Future application-level work can now build on top of that foundation without mixing application changes with the container security work documented here.

Potential Phase 3 work belongs to the application and service architecture rather than the Phase 2 container-hardening layer.

The boundary is therefore:

```text
PHASE 1
Application + initial Dockerization
                │
                ▼
PHASE 2
Container hardening
                │
                ▼
PHASE 3
Application / architecture changes

```


