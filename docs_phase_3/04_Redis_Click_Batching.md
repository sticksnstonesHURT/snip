# Redis Click Batching and Data Flow

## 1. Purpose

Phase 3 changes how Snip records click events.

- In the original approach, recording a click involved writing to PostgreSQL during the redirect request. 
- As redirect traffic increases, synchronous database writes can add latency and increase the database's write workload.

**Snip now uses Redis to buffer click events and a background flusher to persist them to PostgreSQL in batches.**

The objectives are to:

-   Keep the redirect hot path lightweight.
    
-   Reduce synchronous PostgreSQL writes during redirects.
    
-   Buffer click events in Redis.
    
-   Persist click events asynchronously in batches.
    
-   Avoid duplicate database records when a queued event is retried.
    
-   Expose both persisted and pending click counts.
    

**This design improves the separation between serving redirects and persisting analytics data.**


---------------------

## 2. Components Involved


| Component          | Responsibility                                                                    |
| ------------------ | --------------------------------------------------------------------------------- |
| FastAPI            | Handles redirects and application requests.                                       |
| Redis              | Caches link lookups and buffers click events awaiting persistence.                |
| PostgreSQL         | Stores link records and persisted click events.                                   |
| Background flusher | Transfers queued click events from Redis to PostgreSQL.                           |
| `click_events` table | Stores individual click events, including their unique event identifiers.       |


**Redis now holds more than disposable cache entries. It also contains click events that may not yet have been persisted to PostgreSQL.**

**For that reason, Phase 3 enables Redis AOF (Append-Only File) persistence and uses a persistent Redis data volume.**


-------------

## 3. High-Level Architecture

```
                Redirect request
                       |
                       v
                 FastAPI API
                       |
                       v
                Redis cache lookup
                   /       \
               Cache hit   Cache miss
                   |           |
                   |           v
                   |       PostgreSQL
                   |           |
                   |       Cache result
                   |           |
                   +-----+-----+
                         |
                         v
                  Resolve destination
                         |
                         v
                  Record click in Redis
                  - Increment pending count
                  - Enqueue click event
                         |
                         v
                  Return redirect
```


<br>

The background persistence path operates separately:

```
                  Redis click queue
                         |
                         v
                  Background flusher
                         |
                         v
                 PostgreSQL batch insert
                         |
                         v
               Update persisted counters
                         |
                         v
                  Trim processed queue
                  and decrement pending
```
<br>

The key distinction is that **the redirect request queues the event; the background flusher persists it**.


-----------------

## 4. Redirect Request Data Flow

### Step 1: Resolve the short code

When a user visits a short URL, the API resolves the corresponding destination.

The lookup follows a cache-aside pattern:

1.  Look for the link in Redis.
    
2.  If the cached entry exists, use it.
    
3.  If the entry is missing, retrieve the link from PostgreSQL.
    
4.  Cache the result in Redis for subsequent requests.
    

This avoids repeatedly querying PostgreSQL for frequently accessed links.

<br>


### Step 2: Record the click in Redis

After resolving the destination, the API records the click using Redis operations grouped in a pipeline.

The relevant operations are conceptually:

-   `INCR clicks:pending:{code}` — increments the pending-click counter for the short code.
    
-   `RPUSH clicks:queue {event_id, code, clicked_at}` — appends the click event to the queue.
    

The event contains an identifier, the short code, and the click timestamp.

**Grouping the operations in a Redis pipeline reduces the overhead of issuing separate Redis commands. A pipeline is not automatically a transaction; atomic execution requires the appropriate Redis transaction or scripting mechanism.**

<br>


### Step 3: Return the redirect

The API can return the redirect without waiting for the click event to be inserted into PostgreSQL.

This reduces the amount of synchronous database work associated with a redirect.

The click is now pending persistence. It must not yet be treated as a confirmed PostgreSQL record.


------------


## 5. Redis Queue and Pending Counters

**Snip uses two related Redis data structures.**
<br>

### Click queue

The `clicks:queue` list stores click events awaiting processing.

New events are appended using `RPUSH`. The flusher examines queued entries and processes them in batches.


<br>

### Pending-click counters

The `clicks:pending:{code}` counters track queued clicks for each short code.

For example, if three clicks for the code `abc123` are queued but not yet persisted, its pending counter can report three.

Once those events are successfully processed, the corresponding pending count is decremented.

The queue and pending counters serve different purposes:

-   The queue contains the event data needed for persistence.
    
-   The counters provide a fast way to report how many clicks are still pending.
    
<br>

Because these structures are updated separately from PostgreSQL, their consistency depends on correct queue-processing and recovery logic.


-------

## 6. Background Flushing

The background flusher periodically processes queued click events.

The configured `FLUSH_INTERVAL_SECONDS` controls the interval between scheduled flushes. The documented default is five seconds.

The process is conceptually:

1.  Inspect the queue using `LRANGE`.
    
2.  Select a batch of events to process.
    
3.  Insert the events into PostgreSQL.
    
4.  Use idempotent insertion to avoid creating duplicate event records.
    
5.  Update persisted counters only for events actually inserted.
    
6.  Remove processed entries using `LTRIM`.
    
7.  Decrement the corresponding pending-click counters.
    

This separates click ingestion from database persistence.
<br>

### Why inspect the queue before removing entries?

The flusher needs the event data to perform the PostgreSQL inserts. Inspecting the queue first allows it to attempt persistence before removing those entries.

However, `LRANGE` followed later by `LTRIM` is not a complete atomic delivery mechanism. If multiple flusher instances process the same queue concurrently, they can inspect overlapping batches.

**The current design therefore assumes a single API instance running the flusher. Supporting multiple concurrent API instances safely would require a stronger queue-consumer design.**

------


## 7. Idempotent PostgreSQL Inserts

A background worker may retry an event if processing is interrupted or the result of a previous attempt is uncertain.

Without duplicate protection, retrying could create duplicate click records and inflate analytics.

<br>

Snip addresses this using a unique `event_id` in `click_events` and an insertion strategy based on:

```
ON CONFLICT (event_id) DO NOTHING
```

The flusher also uses `RETURNING link_id` to identify which inserts actually created new records.
<br>

Persisted click counters are updated only for events that were newly inserted.

**This is important because a duplicate event that conflicts with an existing `event_id` must not increment the persisted count a second time.**

<br>

### Delivery semantics

The design provides **at-least-once processing with idempotent database insertion**, not exactly-once delivery.

The distinction matters:

-   An event may be attempted more than once.
    
-   A repeated insert with the same event identifier does not create a second event row.
    
-   Database idempotency protects against duplicate persisted records.
    
-   It does not automatically guarantee that every Redis counter and queue operation remains consistent after every possible crash.
    

**The overall system still needs careful recovery handling around the boundary between database commits and Redis queue updates.**


-------------

## 8. Persisted Clicks Versus Pending Clicks

Because persistence is asynchronous, the click count stored in PostgreSQL can temporarily lag behind actual redirect traffic.

**Snip's statistics endpoint exposes both persisted clicks and `pending_clicks`.**

Conceptually:
```
[  
\text{Observed clicks} \approx  
\text{Persisted clicks} + \text{Pending clicks}  
]
```

This is an operational approximation, not a guarantee of a perfectly consistent total at every instant. 

Concurrent requests, flushes, retries, and failures can temporarily affect the relationship.

For example:

| State                       | Persisted clicks | Pending clicks |
| --------------------------- | ---------------- | -------------- |
| Before a new click          | 100              | 0              |
| After a click is queued     | 100              | 1              |
| After the click is persisted| 101              | 0              |

The persisted count increases only after the database insert succeeds.

**A pending count indicates work awaiting persistence; it is not proof that the event is already present in** PostgreSQL.


-------

## 9. Redis Persistence and Data Durability

Before Phase 3, Redis was primarily used as a cache. Losing cached entries could generally be addressed by querying PostgreSQL again.

Phase 3 changes this assumption because Redis also stores unflushed click events.

The configuration therefore enables Redis AOF persistence and uses a persistent `redisdata` volume.
<br>

These measures improve the chances of recovering queued events after a Redis or container restart, but they do not eliminate every possible data-loss scenario.

Durability still depends on the Redis persistence configuration, when writes reach the persistence layer, storage health, and the failure involved.

**Redis persistence also does not replace PostgreSQL backups. PostgreSQL remains the durable application database for successfully persisted click records.**


------------

## 10. Performance and Trade-offs

### Benefits

-   Redirects avoid a synchronous PostgreSQL write for every click.
    
-   Redis absorbs short bursts of click traffic.
    
-   The background flusher can batch database inserts.
    
-   Idempotent inserts protect against duplicate event rows.
    
-   Pending-click counters expose work that has not yet been persisted.
    
<br>

### Costs and limitations

-   Click statistics may lag behind actual traffic.
    
-   Redis now stores operational data, not just reconstructible cache entries.
    
-   Queue processing and counter updates require careful failure handling.
    
-   The current flusher assumes a single API instance.
    
-   A large backlog can increase the time needed to persist events.
    
-   Redis persistence improves recovery but is not a substitute for backups or a fully transactional queue.
    

The architecture trades immediate database consistency for a lighter redirect path and asynchronous persistence.


------

## 11. Troubleshooting

### Pending clicks do not decrease

Inspect the API logs and Redis queue state. Check whether the flusher is running, whether PostgreSQL inserts are failing, and whether the queue is growing faster than it can be processed.
<br>

### Persisted click counts appear delayed

This can be expected because writes are asynchronous. Check the configured flush interval, pending-click count, and flusher logs before concluding that data is missing.
<br>

### Duplicate click events are suspected

Check the uniqueness constraint on `click_events.event_id` and confirm that the flusher uses conflict handling correctly.
<br>

### Redis restarts and queued clicks are missing

Verify that AOF persistence is enabled, that the `redisdata` volume is mounted correctly, and that the actual Redis persistence settings match the intended configuration.

Do not assume that enabling AOF makes all acknowledged Redis operations immune to every failure.
<br>

### Queue processing becomes unsafe with multiple API replicas

The current `LRANGE` and `LTRIM` processing approach assumes a single flusher. Do not scale the API horizontally without reviewing this assumption.

A future design could use Redis Streams with consumer groups or another queue-consumption strategy that provides safer coordination between workers.

------

## 12. Key Takeaways

-   Redirects queue click events in Redis instead of synchronously inserting each event into PostgreSQL.
    
-   A background flusher persists queued events in batches.
    
-   Unique event identifiers and `ON CONFLICT DO NOTHING` provide idempotent database insertion.
    
-   Persisted click counts can lag behind actual traffic; pending counts help explain that delay.
    
-   Redis AOF and a persistent volume matter because Redis now holds unflushed click events.
    
-   The current flusher assumes a single API instance.
    
-   At-least-once processing and idempotent inserts are not equivalent to exactly-once delivery.
