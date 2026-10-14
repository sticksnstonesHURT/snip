# Design Decisions, Trade-offs and Lessons

## 1. Purpose

Phase 3 extends Snip beyond its initial URL-shortening functionality by introducing database migration management, asynchronous click persistence, API-key authentication, and Redis-based rate limiting.

These changes address practical application and infrastructure concerns: schema evolution, database write overhead, access control, request abuse, and data durability.

Every architectural decision introduces trade-offs. This document records the reasoning behind the Phase 3 design, its limitations, and the lessons that can guide future development.

-------------------------------------


## 2. Decision: Use Alembic for Database Migrations

### Problem

Application models evolve over time, but an existing database does not automatically acquire every structural change made to those models.

Using `Base.metadata.create_all()` as the primary schema-management mechanism does not provide a complete migration history or automatically alter existing tables to match every model change.
<br>

### Decision

Snip uses Alembic to manage versioned database schema changes. The normal API startup path no longer relies on `Base.metadata.create_all()`.

A dedicated migration service runs:

```
alembic upgrade head
```
<br>

### Benefits

-   Schema changes are represented by versioned migration scripts.
    
-   Database changes can be reviewed alongside application code.
    
-   Environments can apply pending migrations in a consistent order.
    
-   Migration history provides a record of applied revisions.
    
-   Schema management is separated from normal request handling.
    
<br>

### Trade-offs

-   Migration scripts must be maintained and reviewed.
    
-   Failed migrations can prevent application startup.
    
-   Destructive schema changes require careful planning.
    
-   Migration history does not replace database backups.
    
<br>

### Lesson

Database schema changes should be treated as versioned application changes rather than incidental side effects of application startup.


------------------

## 3. Decision: Run Migrations Before Starting the API

### Problem

The API may depend on tables, columns, or constraints that do not yet exist in the database.

A healthy PostgreSQL server can still have an outdated schema.
<br>

### Decision

Compose coordinates three services:

1.  PostgreSQL becomes healthy.
    
2.  The one-shot migration service completes successfully.
    
3.  The API starts after migration completion.
    

The API and migration service share the same application image.
<br>

### Benefits

-   Database readiness and schema readiness are handled separately.
    
-   The API is not intentionally started before the required migration step succeeds.
    
-   The migration environment uses the same image and dependencies as the API.
    
-   Startup ordering is explicit in the Compose configuration.
    
<br>

### Trade-offs

-   A migration failure blocks API startup through the configured dependency.
    
-   Startup now depends on an additional service completing successfully.
    
-   Migration execution must be coordinated carefully as deployment complexity grows.
    
<br>

### Lesson

A running database container does not imply that the application is ready to use it. Readiness checks and schema migration orchestration solve different problems.


----------------

## 4. Decision: Batch Click Writes Through Redis

### Problem

Writing each click event directly to PostgreSQL during a redirect adds synchronous database work to a latency-sensitive request.

As redirect traffic increases, this can increase database write pressure.
<br>

### Decision

The redirect path records click events in Redis. A background flusher periodically inserts queued events into PostgreSQL in batches.

The configured `FLUSH_INTERVAL_SECONDS` defaults to five seconds.
<br>

### Benefits

-   Redirects avoid a synchronous PostgreSQL insert for every click.
    
-   Redis absorbs bursts of incoming click events.
    
-   Database writes can be grouped into batches.
    
-   Click ingestion and persistence are separated.
    
<br>

### Trade-offs

-   Persisted statistics may lag behind actual traffic.
    
-   Redis now holds data that has not yet reached PostgreSQL.
    
-   Queue-processing failures require careful recovery.
    
-   More background processing logic must be maintained.
    
-   The current flusher assumes a single API instance.
    
<br>

### Lesson

Performance improvements often move complexity rather than eliminate it. Asynchronous persistence reduces synchronous work, but it introduces queue management, retry, recovery, and observability requirements.

-----------------------------


## 5. Decision: Use Idempotent Click Inserts

### Problem

An event may be retried when a worker fails or cannot determine whether a previous insert succeeded.

Without duplicate protection, retries can create duplicate click records.
<br>

### Decision

Each click event has a unique `event_id`. PostgreSQL insertion uses conflict handling equivalent to:

```
ON CONFLICT (event_id) DO NOTHING
```

The flusher uses the rows actually inserted to determine which persisted counters should be updated.
<br>

### Benefits

-   Reprocessing an event does not create another row for the same event identifier.
    
-   Database retries are safer.
    
-   Persisted counters are not incremented merely because an already-existing event was encountered again.
    
<br>

### Trade-offs

-   Unique identifiers and conflict-handling logic add implementation complexity.
    
-   Idempotency does not guarantee that every event is eventually persisted.
    
-   Redis queue state and PostgreSQL transactions are not one atomic transaction.
    
-   Pending counters can still become inconsistent if failures occur between operations.
    
<br>

### Lesson

Idempotency is an essential building block for reliable retry behaviour, but it is not equivalent to exactly-once delivery.

The current design provides at-least-once processing with idempotent database insertion. Its guarantees must be described in those terms.


----------------------

## 6. Decision: Persist Redis Data

### Problem

Redis initially served primarily as a cache. Losing cached entries could generally be addressed by retrieving the original records from PostgreSQL.

Phase 3 also uses Redis to hold unflushed click events.
<br>

### Decision

Redis AOF persistence is enabled, and the `redisdata` volume is used to retain Redis data across container lifecycles.
<br>

### Benefits

-   Redis can reconstruct persisted state from its append-only file.
    
-   A persistent volume avoids relying solely on a container's writable filesystem.
    
-   Recovery prospects improve for queued events that have not yet reached PostgreSQL.
<br>
    

### Trade-offs

-   Redis storage now requires greater operational care.
    
-   Persistence configuration affects recovery guarantees.
    
-   Redis and PostgreSQL can temporarily contain different representations of click state.
    
-   AOF and persistent volumes do not replace PostgreSQL backups.
    
<br>

### Lesson

The durability requirements of a service change when it starts storing data that cannot simply be reconstructed from the primary database.

A cache and a queue may use the same technology, but they do not have the same operational requirements.

-------------------------------------

## 7. Decision: Flush Clicks During Graceful Shutdown

### Problem

Pending click events may remain in Redis when the API container is stopped.

Without a final flush, those events may not be persisted before the process exits.
<br>

### Decision

Snip attempts to flush pending clicks during graceful shutdown.

The regression test used `FLUSH_INTERVAL_SECONDS=600` to keep three clicks pending until the API stopped.

The recorded logs included:

```
shutting down: flushing clicks
final flush: 3 clicks
```

The PostgreSQL click count then increased by three.
<br>

### Benefits

-   Pending events do not have to wait for the next scheduled flush during an orderly shutdown.
    
-   The application has an opportunity to persist its remaining work.
    
-   The behaviour can be verified through logs and database state.
    
<br>

### Trade-offs

-   The final flush can fail if PostgreSQL is unavailable.
    
-   A forced termination may prevent shutdown handling from completing.
    
-   The container stop timeout must allow sufficient time for shutdown work.
    
-   A successful test does not guarantee survival of every crash scenario.
    
<br>

### Lesson

Graceful shutdown is part of data handling, not merely process cleanup. Background work should have an explicit shutdown strategy and a test that verifies its observable result.


------------------------

## 8. Decision: Protect Write Operations with API Keys

### Problem

Unauthenticated clients could otherwise attempt to create or delete links.
<br>

### Decision

Snip requires the `X-API-Key` header for:

-   `POST /links`
    
-   `DELETE /links/{code}`
    

Redirects, statistics, and the health endpoint remain unauthenticated.

Keys are generated using Python's `secrets` module, and only a SHA-256 hash is stored in the database.

Key generation uses the documented workflow:

```
make key n="laptop"
```
There is no public HTTP endpoint for creating new keys.
<br>

### Benefits

-   Write operations require a valid credential.
    
-   Raw keys are not stored in the database.
    
-   Key generation is separated from public request handling.
    
-   The stored hash can identify a key for rate limiting.
    
<br>

### Trade-offs

-   Clients must securely manage and supply API keys.
    
-   Compromised keys remain usable until revoked.
    
-   The current implementation lacks a dedicated rotation and revocation workflow.
    
-   A shared API-key requirement does not provide fine-grained permissions.
    
<br>

### Lesson

Authentication should be introduced at the application boundary, with a clear policy for which operations require credentials and how those credentials are stored and managed.


---------------------

## 9. Decision: Use Redis Fixed-Window Rate Limiting

### Problem

Authentication alone does not prevent a valid client from sending excessive requests.
<br>

### Decision

Snip uses Redis to enforce a limit of 60 requests per minute per API key. Requests beyond the limit receive HTTP `429 Too Many Requests`.

The counter key includes the API-key hash and the current time window.
<br>

### Benefits

-   Request counters can be updated without writing every counter to PostgreSQL.
    
-   Different API keys have separate rate-limit buckets.
    
-   Excessive requests can be rejected before further processing.
    
<br>

### Trade-offs

-   Fixed windows permit bursts across adjacent window boundaries.
    
-   A key is a credential identity, not necessarily a unique person or device.
    
-   Multiple keys can provide multiple independent buckets.
    
-   The limiter does not replace controls for unauthenticated traffic.
    
-   Redis availability affects rate-limit evaluation.

<br>    

### Lesson

A rate limit must be understood in terms of its algorithm and identity. A limit of 60 requests per minute is not necessarily equivalent to a rolling maximum of 60 requests in every possible 60-second interval.

-----------------

## 10. Decision: Harden the Containers

### Problem

Application functionality alone does not provide sufficient runtime isolation.

Containers should run with only the permissions and resources they require.
<br>

### Decision

Phase 2 introduced container hardening, which remains relevant to Phase 3. The recorded configuration includes:

-   Non-root execution.
    
-   A read-only root filesystem.
    
-   Dropped Linux capabilities.
    
-   `no-new-privileges`.
    
-   Resource controls.
    
-   Log rotation.
    
-   An init process for process and shutdown management.
    

The recorded Phase 3 verification confirmed the API's read-only root filesystem, init process, dropped capabilities, and 256 MiB memory limit. It also confirmed the migration service's read-only root filesystem and dropped capabilities.
<br>

### Benefits

-   Reduces unnecessary container privileges.
    
-   Restricts writes to the container root filesystem.
    
-   Makes resource usage more predictable.
    
-   Supports cleaner process management and shutdown behaviour.
    
<br>

### Trade-offs

-   Applications may need explicit writable temporary paths or volumes.
    
-   Resource limits must reflect real application requirements.
    
-   Runtime restrictions can expose hidden assumptions in dependencies.
    
-   Security settings need to be tested with the actual workload.
    
<br>

### Lesson

Container hardening is most effective when designed into the application and deployment configuration rather than added as an afterthought.


--------------------------

## 11. Architectural Limitation: Single-Instance Flushing

The current click flusher uses `LRANGE` to inspect queued events and `LTRIM` to remove processed entries.

This approach assumes a single API instance running the flusher.

If multiple instances process the same queue concurrently, they may inspect overlapping batches. Unique event identifiers protect against duplicate PostgreSQL event rows, but do not automatically prevent all pending-counter or queue-consistency problems.
<br>

### Future improvement

Before scaling the API horizontally, replace or strengthen the queue-consumption mechanism.

Possible approaches include:

-   Redis Streams with consumer groups.
    
-   An atomic claim-and-process strategy.
    
-   A dedicated background worker with a coordinated ownership model.
    

Any replacement should define retry behaviour, acknowledgement rules, failure recovery, and the relationship between queue acknowledgements and PostgreSQL commits.

Horizontal scaling should follow a design change and appropriate concurrency testing, not simply an increase in the number of API replicas.


--------------------------

## 12. Architectural Limitation: Distributed State

Click events pass through Redis before reaching PostgreSQL. During this process, the two systems can temporarily disagree about which events have been persisted.

For example, a database insert can succeed before the application completes its Redis queue updates.

If the process fails at that boundary, a later retry may encounter an existing event row while the queue still contains the event.

The unique `event_id` protects the database from duplicate rows, but the remaining Redis operations must still handle the retry correctly.
<br>

### Future improvement

A stronger design should define explicit invariants for:

-   When an event is considered accepted.
    
-   When an event is considered persisted.
    
-   When it can be acknowledged or removed from the queue.
    
-   How pending counters are repaired after partial failure.
    
-   How events are recovered after a worker restart.
    

This turns data durability from an informal expectation into a set of behaviours that can be implemented and tested.

------------------

## 13. Testing Lessons

Phase 3 verification demonstrated the value of testing observable behaviour rather than relying solely on configuration or code inspection.

Examples include:

-   A cold-start test verified migration completion before API startup.
    
-   `alembic check` reported no new autogenerated schema operations.
    
-   Authentication tests verified rejection of a missing API key and acceptance of a valid key.
    
-   Container inspection verified selected runtime hardening settings.
    
-   A long flush interval made it possible to test shutdown with three pending clicks.
    
-   Rate-limit testing verified that requests exceeding the limit received HTTP `429`.
    

These tests provided evidence for specific scenarios. They did not establish correctness under every possible failure condition.
<br>

### General lessons

1.  **Test failure paths deliberately.** A successful startup does not prove that startup behaves correctly when a migration fails.
    
2.  **Verify persistence in PostgreSQL.** Logs alone do not establish that click records were committed.
    
3.  **Test boundaries.** Rate limits, timeouts, and queue batch sizes can behave differently at their edges.
    
4.  **Distinguish verified results from assumptions.** Untested behaviour should remain explicitly unverified.
    
5.  **Keep recovery procedures safe.** Destructive cleanup can remove the very state needed to recover pending events.


-------------------    

## 14. Future Improvements

The following are potential future improvements, not features currently established by Phase 3.

| Area | Potential improvement | Reason |
|------|-----------------------|--------|
| Click queue | Redis Streams or another coordinated queue | Safer processing across multiple workers |
| Pending counters | Reconciliation and recovery procedure | Repair inconsistencies after partial failures |
| API keys | Explicit revocation, rotation, and expiry | Better credential lifecycle management |
| Rate limiting | Sliding-window or token-bucket algorithm | Better control over bursts |
| Abuse prevention | IP-based limits for unauthenticated traffic | Reduce exposure to anonymous request floods |
| Observability | Queue-depth metrics and flush-failure alerts | Detect backlogs before they become serious |
| Durability | Documented recovery objectives and restore tests | Establish realistic recovery expectations |
| Testing | Migration-failure, crash-recovery, and concurrency tests | Validate behaviour beyond normal operation |


These improvements should be prioritized according to actual deployment requirements rather than implemented merely for architectural complexity.

-------------------

## 15. Final Summary

Phase 3 addresses four main areas:

1.  **Schema management:** Alembic provides versioned migrations, and Compose runs them before the API starts.
    
2.  **Click persistence:** Redis buffers click events, and a background flusher writes them to PostgreSQL in batches.
    
3.  **Access control:** API keys protect link creation and deletion, with hashes stored instead of raw credentials.
    
4.  **Abuse prevention:** Redis fixed-window rate limiting restricts requests per API key.
    

**The main trade-off is increased architectural complexity in exchange for a lighter redirect path, controlled schema evolution, and better access control.**

The implementation also establishes important limits that must remain explicit: click persistence is asynchronous, idempotent insertion is not exactly-once delivery, graceful shutdown cannot protect against every failure, and the current flusher assumes a single API instance.

The central lesson from Phase 3 is that reliable infrastructure requires more than adding components. 

Each component needs a clearly defined responsibility, explicit failure behaviour, and verification that demonstrates what the system actually guarantees.
