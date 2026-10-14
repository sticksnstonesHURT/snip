# Troubleshooting and Failure Scenarios

## 1. Purpose

Phase 3 introduces several interacting components: Alembic migrations, Docker Compose startup dependencies, Redis click batching, asynchronous PostgreSQL writes, graceful shutdown handling, API-key authentication, and rate limiting.

A failure in one component can affect another. For example, a migration failure can prevent API startup, while a Redis issue can affect both click recording and rate-limit enforcement.

This document provides a structured approach to diagnosing these failures.

The objectives are to:

-   Identify the component associated with a failure.
    
-   Inspect service status and logs before making changes.
    
-   Distinguish expected asynchronous behaviour from actual data loss.
    
-   Avoid unsafe manual modifications to Redis queues or database records.
    
-   Verify recovery instead of assuming that a successful restart fixed the problem.
    

------------------------

## 2. General Diagnostic Workflow

Start by inspecting the Compose services:

```bash
docker compose ps -a
```

<br>


Inspect the relevant service logs:

```bash
docker compose logs --tail=100 api
docker compose logs --tail=100 db
docker compose logs --tail=100 redis
docker compose logs --tail=100 migrate
```


<br>


For a service that is repeatedly failing, follow its logs:

```bash
docker compose logs -f api
```

<br>

Use the following sequence:

1.  Identify the failing endpoint or service.
    
2.  Inspect the service status and recent logs.
    
3.  Determine whether the failure concerns startup, authentication, Redis, PostgreSQL, or application logic.
    
4.  Check the relevant configuration and dependencies.
    
5.  Apply the smallest appropriate corrective action.
    
6.  Repeat the original failing operation.
    
7.  Verify the resulting database state when persistence is involved.
    

Avoid immediately deleting containers, volumes, or Redis data. Such actions can destroy evidence or remove pending click events.


--------------------------

## 3. PostgreSQL or Database Startup Problems

### Symptom: The migration service cannot connect to PostgreSQL

**Possible causes**

-   PostgreSQL is not ready.
    
-   Database connection settings are incorrect.
    
-   The database credentials do not match.
    
-   The configured database name is incorrect.
    
-   PostgreSQL is unhealthy or repeatedly restarting.
    
<br>

**Diagnosis**

```bash
docker compose ps -a
docker compose logs --tail=100 db
docker compose logs --tail=100 migrate
```

Check the database connection settings used by the application and migration service.

If PostgreSQL provides `pg_isready` in the container, it can help confirm whether the server is accepting connections.
<br>

**Recovery**

Correct the underlying database configuration or health issue, then retry the migration workflow. Do not assume that a running database container is necessarily ready for connections.
<br>

### Symptom: The API starts but database queries fail

**Possible causes**

-   A required migration has not been applied.
    
-   The application expects a different schema version.
    
-   Database connection settings point to the wrong database.
    
-   A query or model is incompatible with the actual schema.
    
<br>

**Diagnosis**

```bash
docker compose logs --tail=100 api
docker compose logs --tail=100 migrate
```

Where the application environment permits it, inspect the current Alembic revision:

```
alembic current
alembic history
```

Run Alembic commands against the intended database environment.
<br>

**Recovery**

Identify the missing or incorrect schema change. Apply the appropriate reviewed migration rather than manually altering tables without recording the change in migration history.


----------------------

## 4. Migration Service Failure

### Symptom: The `migrate` service exits with a non-zero status

**Possible causes**

-   Database connection failure.
    
-   Invalid migration configuration.
    
-   An error in a migration script.
    
-   A missing revision or inconsistent migration history.
    
-   Insufficient database permissions.
    
<br>

**Diagnosis**

```
docker compose logs migrate
docker compose ps -a
```

Read the first meaningful exception in the migration logs and trace it to the failing operation.
<br>

### Why the API may not start

Snip's Compose configuration requires successful completion of the migration service before the API starts.

If the migration fails, the dependency condition is not satisfied. This is intentional: it prevents the API from starting against a schema that may be outdated.
<br>

### Recovery

1.  Preserve the migration error and relevant logs.
    
2.  Identify the failing migration or connection setting.
    
3.  Check the current database state before retrying.
    
4.  Correct the cause.
    
5.  Rerun the migration workflow.
    
6.  Confirm a successful migration exit and subsequent API startup.
    

Do not delete the `alembic_version` table or edit revision records as a first-line fix. Such changes can make the migration history inconsistent with the actual schema.


-----------------------

## 5. API-Key Authentication Failures

### Symptom: Link creation returns `401 Unauthorized`

**Possible causes**

-   The `X-API-Key` header is missing.
    
-   The key was copied incorrectly.
    
-   The stored key record does not exist in the target database.
    
-   The key was revoked.
    
-   The API cannot query the key record correctly.
    
<br>

**Diagnosis**

Check that the request includes the required header:

```
X-API-Key: snip_your_api_key_here
```

The value shown is a placeholder, not a real credential.

Inspect the API logs for authentication-related errors. Do not print the raw key into shared logs or troubleshooting documents.
<br>

### Recovery

-   Confirm that the correct header is being sent.
    
-   Confirm that the key belongs to the database used by the running API.
    
-   Generate a replacement key if the original is lost or compromised.
    
-   If the key was revoked, use a newly issued key.
    

A key generated in one independent database environment is not automatically registered in another.
<br>

### Symptom: Link deletion returns an authentication error

The delete endpoint also requires an API key.

Verify that the request sends the header and that the key is valid. If authentication succeeds but deletion still fails, inspect the response and logs for resource or application-level errors rather than treating every failure as an authentication problem.


------------------------

## 6. Redis Connectivity Problems

### Symptom: Redirects or API requests fail when Redis is unavailable

Redis serves both caching and click-processing functions. It also stores rate-limit counters.

A Redis failure can therefore affect more than cache performance.

**Diagnosis**

```
docker compose ps -a
docker compose logs --tail=100 redis
docker compose logs --tail=100 api
```

Check:

-   Whether the Redis container is running.
    
-   Whether Redis is accepting connections.
    
-   Whether the API uses the correct Redis hostname and port.
    
-   Whether the relevant Redis volume is mounted.
    
-   Whether the application logs show connection errors.
    
<br>

### Recovery

Resolve the underlying Redis or network issue and then retest the affected operation.

Do not assume that every endpoint will continue working without Redis. Whether a failed Redis request causes an error, a fallback, or a rejected operation depends on the implementation of that specific path.

-------------------------

## 7. Clicks Are Not Appearing in Statistics

### Symptom: A redirect succeeds, but the persisted click count does not immediately increase

**Possible explanation**

Snip persists click events asynchronously. A successful redirect can queue an event in Redis before the background flusher inserts it into PostgreSQL.

A short delay between the redirect and the persisted count is therefore expected.

The statistics endpoint also exposes `pending_clicks`, which helps distinguish queued events from persisted records.
<br>

### Diagnosis

Check the API logs:

```
docker compose logs --tail=100 api
```

Inspect the statistics endpoint and compare the persisted count with the pending count.

Check whether the configured `FLUSH_INTERVAL_SECONDS` explains the observed delay.
<br>

### Recovery

If the pending count decreases and the persisted count increases, the flusher is making progress.

If pending clicks remain elevated, inspect the flusher logs, Redis connectivity, PostgreSQL availability, and the queue-processing path.

Do not manually remove queue entries just because statistics appear delayed.


---------------------


## 8. The Click Queue Is Growing

### Symptom: Pending clicks increase but do not drain

**Possible causes**

-   PostgreSQL is unavailable.
    
-   Click inserts are failing.
    
-   The flusher is not running or is repeatedly failing.
    
-   Incoming clicks exceed the rate at which the flusher can persist them.
    
-   Redis operations are failing.
    
-   Queue-processing logic is encountering an error.
    
<br>

**Diagnosis**

```bash
docker compose logs --tail=200 api
docker compose logs --tail=100 db
docker compose logs --tail=100 redis
```

Check the queue-processing logs and the database error messages.

Compare the rate at which pending clicks accumulate with the rate at which they are persisted.
<br>

### Recovery

Restore the failed dependency or correct the application error, then verify that the queue begins draining.

If the backlog is large, allow enough time for processing and monitor database load. Do not flush or delete queue entries manually without understanding their relationship to persisted records.


--------------------------------------------------------------


## 9. Duplicate Clicks or Inconsistent Pending Counters

### Symptom: The same event appears to be processed more than once

Snip uses a unique `event_id` and PostgreSQL conflict handling to prevent duplicate event rows when an event is retried.

The relevant insert strategy includes:

```
ON CONFLICT (event_id) DO NOTHING
```

If an insert conflicts with an existing event, it should not create another row for the same event identifier.
<br>

### Symptom: Pending counters do not match queue contents

The Redis queue and pending counters are separate structures. Failures between queue operations, counter updates, and PostgreSQL transactions can leave them temporarily inconsistent.

**Diagnosis**

Inspect the application logs and relevant database records. Establish whether the issue concerns duplicate database records, queue removal, or pending-counter updates before modifying any state.
<br>

### Important limitation

The current flusher uses `LRANGE` to inspect entries and `LTRIM` to remove processed entries. It assumes a single API instance running the flusher.

Multiple concurrent flushers can inspect overlapping queue entries. Database idempotency protects against duplicate event rows, but does not automatically protect pending counters or all queue operations.

Do not horizontally scale the API without addressing this queue-consumer limitation.


-----------------------

## 10. Graceful Shutdown Does Not Flush All Clicks

### Symptom: The API stops without a successful final flush

**Possible causes**

-   The process was forcibly terminated.
    
-   The shutdown handler did not complete.
    
-   PostgreSQL was unavailable.
    
-   The queue-processing operation failed.
    
-   The container stop timeout expired.
    

**Diagnosis**

```bash
docker compose logs --tail=200 api
docker compose ps -a
```

Look for the shutdown message and the final-flush result.

The recorded regression test produced logs containing:

```
shutting down: flushing clicks
final flush: 3 clicks
```

The test also verified that the PostgreSQL click count increased by three.

These results establish successful behaviour for that test scenario, not a guarantee for every shutdown condition.
<br>

### Recovery

Restore database connectivity and inspect the queue and persisted records before retrying operations.

If the application is stopped, restart it only after checking the state of its dependencies and pending events. Avoid deleting Redis data to make the service start cleanly.


------------------

## 11. Redis Restart and Missing Pending Events

### Symptom: Queued clicks appear to be missing after Redis restarts

Phase 3 enables Redis AOF persistence and uses the `redisdata` volume because Redis holds unflushed click events.

**Diagnosis**

Check the Redis logs and the Compose volume configuration.

Verify that:

-   AOF is enabled.
    
-   Redis is using the expected persistence directory.
    
-   The intended persistent volume is mounted.
    
-   The volume has not been removed or replaced.
    
-   The Redis persistence policy matches the expected recovery requirements.
    
<br>

### Recovery

Preserve the available Redis and PostgreSQL state while investigating.

Do not run `docker compose down -v` as a routine troubleshooting step. Removing volumes can destroy Redis persistence files and PostgreSQL data.

AOF and a persistent volume improve recovery prospects but do not guarantee survival of every failure. Their effectiveness depends on the persistence policy and the storage failure involved.


----------------------

## 12. Rate Limiting Returns `429 Too Many Requests`

### Symptom: A valid API key receives HTTP `429`

Snip uses a fixed-window limit of 60 requests per minute per API key.

**Possible causes**

-   The key has exceeded its limit in the current minute.
    
-   The requests straddled a window boundary, producing a burst that looks different from a rolling 60-second limit.
    
-   A different request is using the same API key.
    

**Diagnosis**

Review the request volume and the time window associated with the key. Avoid assuming that the limiter measures exactly 60 seconds backward from each individual request.
<br>

### Recovery

Wait until the relevant fixed window resets before retrying.

If the client needs sustained high throughput, review the configured policy rather than repeatedly retrying requests that are already being rejected.

A fixed-window limiter can allow bursts across adjacent minute boundaries. This is a known algorithmic limitation.


-----------------

## 13. Container Hardening Causes Runtime Errors

### Symptom: The API or migration service fails when running with a read-only root filesystem

The API and migration service use hardened runtime settings, including a read-only root filesystem and dropped Linux capabilities.

**Possible causes**

-   The application attempts to write to a path that is not writable.
    
-   A required temporary directory is unavailable.
    
-   A dependency expects to modify files at runtime.
    
-   The container configuration does not provide the required writable mount.
    
<br>

**Diagnosis**

```
docker compose logs --tail=100 api
docker compose logs --tail=100 migrate
```

Look for permission errors and failed file operations.
<br>

### Recovery

Identify the specific path that must be writable and decide whether it should use a temporary filesystem, an explicitly writable volume, or a different application path.

Do not disable the container's hardening settings wholesale to bypass an unexplained error.

----------------------------------------------------

## 14. Safe Recovery Principles

When diagnosing a failure involving queued or persisted click events, follow these principles:

1.  Preserve logs and the current service state before making destructive changes.
    
2.  Determine whether the affected events are pending in Redis or already persisted in PostgreSQL.
    
3.  Restore unavailable dependencies before retrying persistence operations.
    
4.  Use the existing event idempotency mechanism when retrying database inserts.
    
5.  Avoid manually trimming the Redis queue or decrementing counters without a verified recovery procedure.
    
6.  Avoid removing volumes during routine troubleshooting.
    
7.  Verify the resulting database records and pending state after recovery.
    

PostgreSQL backups, Redis persistence, and idempotent inserts serve different purposes. None should be treated as a complete substitute for the others.


-----------

## 15. Key Takeaways

-   Inspect Compose service status and logs before changing application state.
    
-   A failed migration can intentionally prevent API startup.
    
-   Click statistics can lag because persistence is asynchronous.
    
-   A growing queue usually requires investigation of the flusher and its dependencies.
    
-   Idempotent inserts prevent duplicate event rows but do not guarantee complete queue consistency.
    
-   Graceful shutdown helps persist pending clicks but cannot protect against every forced termination.
    
-   Redis AOF and persistent volumes improve recovery prospects but are not substitutes for database backups.
    
-   The current queue-processing implementation assumes a single API instance.
    
-   Avoid destructive cleanup commands until pending and persisted data have been examined.
