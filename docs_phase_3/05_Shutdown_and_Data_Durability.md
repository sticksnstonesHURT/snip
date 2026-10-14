# Graceful Shutdown and Data Durability

## 1. Purpose

Phase 3 introduces asynchronous click persistence. 

Redirect requests enqueue click events in Redis, and a background flusher writes them to PostgreSQL.

This improves the redirect path, but it introduces an important operational question:
<br>

#### **What happens to queued click events when the API container shuts down?**

If the application stops before pending events are persisted, recent analytics data may remain unflushed. 

Snip therefore implements a graceful shutdown procedure that attempts to flush pending clicks before the application exits.

This document covers:

-   Graceful shutdown and final flushing.
    
-   Redis persistence and the role of the `redisdata` volume.
    
-   The distinction between queued and persisted click events.
    
-   Shutdown verification.
    
-   Failure scenarios and remaining limitations.
    
----------

## 2. Why Graceful Shutdown Matters

A normal application shutdown should allow the application to finish important work before its process exits.

In Snip, that work includes persisting click events waiting in Redis.
<br>

Consider this sequence:

1.  A user visits a short URL.
    
2.  The API queues a click event in Redis.
    
3.  The redirect succeeds.
    
4.  The background flusher has not yet persisted the event.
    
5.  The API container receives a stop signal.
    

If the application exits immediately, the queued event may not reach PostgreSQL during that shutdown.

A graceful shutdown gives the application an opportunity to flush pending events before exiting.

This is particularly important when the configured flush interval is long. 

A click may be queued shortly after a scheduled flush, leaving it pending for most of the interval.

---------

## 3. Shutdown Lifecycle

The intended shutdown flow is:

```
          API receives shutdown signal
                       |
                       v
             Begin application shutdown
                       |
                       v
             Stop normal background work
                       |
                       v
             Flush remaining click events
                       |
                       v
             Persist events in PostgreSQL
                       |
                       v
             Complete shutdown and exit
```

The application logs indicate that Snip's shutdown handling includes:

-   A shutdown message indicating that clicks are being flushed.
    
-   A final-flush message reporting the number of clicks handled.
    

The exact order in which background tasks are stopped, awaited, or cancelled must follow the implementation. The diagram above describes the intended lifecycle rather than every internal scheduling detail.


-------------------------

## 4. Final Flush During Shutdown

Under normal operation, the background flusher processes queued events periodically according to `FLUSH_INTERVAL_SECONDS`, which defaults to five seconds.
<br>

During graceful shutdown, Snip also attempts a final flush.

This means the application does not rely exclusively on the next scheduled flush to persist pending events.

The final flush follows the same broad persistence process:

1.  Inspect pending click events in Redis.
    
2.  Insert eligible events into PostgreSQL using the idempotent insertion strategy.
    
3.  Update persisted counters for newly inserted events.
    
4.  Remove processed events from the Redis queue.
    
5.  Decrement the corresponding pending-click counters.
    
<br>

The goal is to persist the remaining work before the API process exits.

A successful final flush is not a universal guarantee that all events survive every possible failure. It depends on the application receiving and handling the shutdown signal, PostgreSQL remaining available, the flush completing, and Redis and database operations succeeding.

-------------------------


## 5. Shutdown Verification

The Phase 3 regression test deliberately increased the flush interval so that queued clicks would remain pending long enough to test shutdown behaviour.
<br>

The test used:

```
FLUSH_INTERVAL_SECONDS=600
```
<br>

This long interval reduced the chance that the periodic flusher would persist the test clicks before shutdown began.

The test sequence was:

1.  Start the application with the long flush interval.
    
2.  Generate three clicks.
    
3.  Confirm that the clicks remain pending rather than being persisted by the scheduled flusher.
    
4.  Stop the API service using `docker compose stop api`.
    
5.  Inspect the application logs.
    
6.  Verify the persisted click count in PostgreSQL.
<br>
    

The recorded logs included:

```
shutting down: flushing clicks
final flush: 3 clicks
```

The PostgreSQL click count then increased by three.
<br>

This demonstrated that, in the tested scenario, the application performed a final flush during graceful shutdown and persisted the three pending clicks.

The result is evidence for the tested shutdown path. It does not prove that every crash, timeout, database outage, or forced termination will preserve all queued events.


---------------------

## 6. Redis Persistence

Graceful shutdown protects against some orderly stop scenarios. It cannot protect against every unexpected process or host failure.

Phase 3 also enables Redis AOF persistence and uses the `redisdata` volume.
<br>

### Append-only file (AOF)

Redis AOF persistence records write operations so Redis can reconstruct its data after a restart.

Snip enables AOF with the Redis startup option:

```
redis-server --appendonly yes
```

AOF improves recovery prospects for click events that have been accepted into Redis but not yet persisted to PostgreSQL.

However, the durability guarantee depends on the configured AOF synchronization policy, storage behaviour, and the failure involved. Enabling AOF alone does not mean every recent write is guaranteed to survive every failure.
<br>

### Persistent volume

The `redisdata` volume allows Redis data to persist beyond the lifecycle of an individual Redis container.

Without a correctly mounted persistent volume, replacing a container could discard the data stored in its writable container filesystem.

A persistent volume and AOF serve complementary purposes:

-   AOF provides a mechanism for reconstructing Redis data.
    
-   The volume provides persistent storage for the Redis persistence files.
    

Neither removes the need for PostgreSQL backups or appropriate recovery procedures.


------------------------------

## 7. Graceful Shutdown Versus Forced Termination

It is important to distinguish an orderly stop from an abrupt termination.

| Scenario | Expected behaviour |
|----------|-------------------|
| Normal scheduled flush | The background flusher attempts to persist queued events. |
| Graceful API shutdown | The application attempts a final flush before exiting. |
| API process crashes | The final flush may not run to completion. |
| Container is forcibly killed | The application may not get an opportunity to flush. |
| PostgreSQL is unavailable during shutdown | The final flush may fail or remain incomplete. |
| Redis or its storage fails | Queued events may be lost depending on persistence state and failure conditions. |


<br>


A container stop operation normally gives the process a shutdown opportunity before the configured stop timeout expires. If the process does not exit in time, the container runtime can forcibly terminate it.

The stop timeout therefore matters. It must allow sufficient time for the application to finish its shutdown work under expected conditions.

The precise timeout configured for Snip should be verified in the Compose file rather than assumed.


-------------------

## 8. The Database and Redis Have Different Roles

PostgreSQL remains the primary database for persisted link and click records.
<br>

Redis serves two roles in this design:

1.  Caching link lookups.
    
2.  Temporarily holding click events and pending-click counters.
    

This changes the operational importance of Redis. Losing a cached link can generally be handled by retrieving it from PostgreSQL, but losing an unflushed click event can mean losing analytics data that has not yet reached PostgreSQL.
<br>

The distinction is:

-   **Persisted click:** A click event successfully stored in PostgreSQL.
    
-   **Pending click:** A click event queued for persistence but not yet confirmed as a database record.
    
<br>

Pending events should not be treated as equivalent to committed database records.


--------------------

## 9. Durability Limitations

The implementation improves shutdown handling and Redis recovery, but several limitations remain.
<br>

### Redis and PostgreSQL are separate systems

A Redis queue update and a PostgreSQL transaction do not form one shared atomic transaction. Failures between operations can leave the systems temporarily inconsistent.
<br>

### Idempotency does not guarantee delivery

The unique `event_id` and conflict-handling strategy prevent duplicate database rows when an event is retried.

They do not guarantee that every queued event is eventually persisted. Queue removal, counter updates, process failures, and retry behaviour still matter.
<br>

### A final flush is an attempt, not an unconditional guarantee

The final flush must finish successfully before the application exits for it to provide the intended benefit. A forced kill, an exhausted stop timeout, or an unavailable database can prevent completion.
<br>

### AOF is not a database backup

Redis AOF helps reconstruct Redis state. It does not replace PostgreSQL backups, and it does not guarantee recovery from every storage failure or corrupted state.
<br>

### Multiple API instances remain a constraint

The current flusher assumes a single API instance. Running multiple concurrent flushers can create queue-processing and pending-counter consistency problems, even though idempotent database inserts protect against duplicate event rows.


-----------------------------

## 10. Troubleshooting

### The shutdown log does not show a final flush

Inspect the API logs:

```
docker compose logs api
```

Check whether the application received the shutdown signal and whether its shutdown handler was executed.
<br>

### The final flush reports pending clicks but the database count does not increase

Inspect the surrounding logs for database errors or failed inserts. Confirm that the relevant events were newly inserted rather than rejected as duplicates.

Check both the database state and pending queue state before manually removing entries.
<br>

### The API takes too long to stop

Review the queue backlog, PostgreSQL availability, flush duration, and configured container stop timeout.

Do not shorten the timeout without understanding how long the final flush can take.
<br>

### Clicks are missing after a Redis restart

Check that the `redisdata` volume is mounted correctly and that AOF is enabled with the expected persistence settings.

Investigate the failure before assuming that all recent writes were durable.

----

## 11. Key Takeaways

-   Snip attempts a final click flush during graceful API shutdown.
    
-   The shutdown regression test used a 600-second flush interval to keep three clicks pending until the API stopped.
    
-   Logs confirmed the final flush, and the PostgreSQL click count increased by three.
    
-   Redis AOF and the `redisdata` volume improve recovery prospects for queued events.
    
-   Graceful shutdown does not protect against every crash or forced termination.
    
-   Redis persistence, PostgreSQL backups, and idempotent inserts address different failure modes.
    
-   The system's durability depends on successful queue processing, database availability, and appropriate runtime configuration.
