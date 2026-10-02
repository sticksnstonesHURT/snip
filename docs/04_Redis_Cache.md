# Snip — Redis Cache & Caching Internals

## 1. Why Redis exists in Snip

Redis is used as a **cache** for the most frequently accessed piece of information in the application:

```text
short code → target URL
```

For example:

```text
k6lOLa6
   ↓
https://www.youtube.com/playlist?list=...
```

PostgreSQL remains the source of truth.

<br>

The intended architecture is:

```text
                    ┌───────────────┐
                    │  PostgreSQL   │
                    │ Source of Truth│
                    └───────┬───────┘
                            │
                            │ populate
                            ▼
                    ┌───────────────┐
                    │     Redis     │
                    │     Cache     │
                    └───────────────┘
```


If Redis disappears, the application should still be able to reconstruct the cache from PostgreSQL.

That is why Redis is intentionally **not persisted with a Docker volume** in Phase 1. The README explicitly records this distinction.

----------

# 2. What exactly is being cached?

The application does not cache the entire `links` database row.
<br>

It caches only:

```text
code → target_url
```
<br>

For example:

```text
PostgreSQL

id:          1
code:        k6lOLa6
target_url:  https://example.com/something
clicks:      4
created_at:  ...
```
<br>

Redis contains approximately:

```text
key:
link:k6lOLa6

value:
https://example.com/something
```

This is an important design choice.
<br>

Redis does **not** need to know about:

-   the internal database ID
    
-   click events
    
-   creation time
    
-   the authoritative click counter
    

because those belong to PostgreSQL.
<br>

The cache only needs to answer the hot question:

> Given this short code, where should I redirect the client?

----------

# 3. Why cache this lookup?

The redirect endpoint is the most frequently accessed operation.

A normal redirect without caching would conceptually require:

```text
GET /k6lOLa6
      │
      ▼
PostgreSQL
      │
      ▼
find code
      │
      ▼
target URL
      │
      ▼
307 redirect

```

With a cache:

```text
GET /k6lOLa6
      │
      ▼
Redis
      │
      ├── HIT ──► target URL
      │
      └── MISS
             │
             ▼
         PostgreSQL
             │
             ▼
         target URL
             │
             ▼
        populate Redis

```

This is called the **cache-aside pattern**.

The project explicitly chose cache-aside for Phase 1.

----------

# 4. Where Redis is implemented

The Redis-specific application code lives in:

```text
app/cache.py

```

The file deliberately contains only small helper functions:

```text
cache_get()
cache_set()
cache_delete()
```
<br>

The route handlers in:

```text
app/main.py
```

decide **when** those functions should be called.
<br>

This separation is useful:

```text
cache.py
    ↓
How do we talk to Redis?

main.py
    ↓
When should we use Redis?
```

The original implementation follows exactly this structure.

----------

# 5. Creating the Redis client

The application creates the Redis client with:

```python
redis_client = redis.from_url(
    settings.redis_url,
    decode_responses=True,
)
```

The URL comes from configuration.
<br>

Inside Compose, the Redis service is named:

```text
cache
```

so the application uses a Redis URL equivalent to:

```text
redis://cache:6379/0
```
<br>

The important Docker networking concept is that:

```text
cache
```

is a **Compose service name**, not a special Redis keyword.
<br>

Docker's internal DNS lets the API container resolve:

```text
cache
```

to the Redis container.
<br>

So:

```text
API container
     │
     │ redis://cache:6379/0
     ▼
Redis container
```

The original build deliberately used the Compose service name instead of `localhost`.

----------

# 6. Why `localhost` would be wrong

This is an important Docker concept.
<br>

Inside the API container:

```text
localhost
```

means:

```text
the API container itself
```
<br>

It does **not** mean:

```text
the Redis container
```

Therefore this would be wrong inside the API container:

```text
redis://localhost:6379
```
<br>

The correct address is:

```text
redis://cache:6379/0
```

because:

```text
cache
```

resolves to the Redis service through the Compose network.
<br>

The same principle applies to PostgreSQL:

```text
db
```

rather than:

```text
localhost
```

----------

# 7. Redis client creation is lazy

One subtle detail:

```python
redis.from_url(...)
```

creates the client object, but it does not necessarily establish a network connection immediately.
<br>

The first real Redis command is what actually communicates with Redis.

So importing:

```text
app.cache
```

doesn't immediately require Redis to be reachable.
<br>

The connection becomes relevant when the application performs something like:

```python
redis_client.get(...)
```

The original build explicitly called this out because it explains why importing the module doesn't immediately explode if Redis isn't available.

----------

# 8. `decode_responses=True`

The client uses:

```python
decode_responses=True
```

Redis fundamentally deals with byte-oriented data.
<br>

Without decoding, a value retrieved by Python could look conceptually like:

```python
b"https://example.com"
```
<br>

With:

```python
decode_responses=True
```

the application receives:

```python
"https://example.com"

```

as a normal Python string.
<br>

That makes the cache helper interface much cleaner.

The project intentionally enabled this rather than forcing the rest of the application to deal with raw bytes.

----------

# 9. Redis database number

The configured URL ends in:

```text
/0
```

So the application uses Redis logical database:

```text
0
```

This is not a separate Docker database or separate Redis server.

It is Redis's logical database selection.

For this project, there is no need for multiple logical Redis databases.

----------

# 10. Key naming

The application defines:

```python
CACHE_PREFIX = "link:"
```

The actual key is constructed with:

```python
f"{CACHE_PREFIX}{code}"
```
<br>

So:

```text
code = k6lOLa6
```

becomes:

```text
link:k6lOLa6
```
<br>

This is called **key namespacing**.

Instead of having:

```text
k6lOLa6
```

the application has:

```text
link:k6lOLa6
```

----------

# 11. Why use a prefix?

Imagine Phase 2 adds other cached objects:

```text
user:123
stats:k6lOLa6
config:homepage
```

Without a naming convention, keys can become difficult to reason about.

The prefix tells you immediately what the key represents:

```text
link:...
```

means:

```text
cached short-link lookup
```

The build specifically chose the `link:` prefix so other Redis keys could coexist without collisions.

----------

# 12. `cache_get()`

The implementation is:

```python
def cache_get(code: str) -> str | None:
    return redis_client.get(
        f"{CACHE_PREFIX}{code}"
    )
```
<br>

Conceptually:

```text
cache_get("k6lOLa6")
       │
       ▼
GET link:k6lOLa6
       │
       ├── exists ──► URL
       │
       └── missing ─► None
```

The function deliberately returns:

```text
str | None
```

because a cache lookup can legitimately fail.
<br>

A cache miss is not an application error.

It simply means:

> Redis doesn't currently have this value.

The application then has another source of truth: PostgreSQL.

----------

# 13. `cache_set()`

The implementation is:

```python
def cache_set(
    code: str,
    url: str,
    ttl: int = 3600,
) -> None:
    redis_client.set(
        f"{CACHE_PREFIX}{code}",
        url,
        ex=ttl,
    )
```
<br>

This stores:

```text
key
    link:k6lOLa6

value
    https://example.com/...

TTL
    3600 seconds
```
<br>

So the conceptual Redis command is:

```text
SET link:k6lOLa6 "https://example.com/..." EX 3600
```

----------

# 14. Why there is a TTL

The cache should not live forever.
<br>

The project chose:

```text
3600 seconds
=
1 hour
```

for the cache entry.
<br>

The reasoning is important:

```text
Redis is temporary
PostgreSQL is authoritative
```

If something somehow becomes stale in Redis, the TTL eventually removes it.

After expiration:

```text
Redis
   ↓
MISS
   ↓
PostgreSQL
   ↓
fresh value
   ↓
Redis
```

So TTL provides a safety mechanism against stale cache entries.

The actual testing confirmed the TTL was around 3600 seconds immediately after population.

----------

# 15. TTL is not a sliding expiration

This is one of the more subtle things future-you should remember.
<br>

The project uses:

```python
cache_set(...)
```

when the cache is populated.
<br>

A normal cache hit performs:

```python
cache_get(...)
```

It does **not** call:

```python
cache_set(...)
```

again.
<br>

Therefore the TTL does not reset every time somebody clicks the short URL.

Suppose:

```text
12:00
    cache created
    TTL = 3600

12:30
    cache hit
    TTL ≈ 1800

12:59
    cache hit
    TTL ≈ 60

13:00
    cache expires
```

Even if the link received hundreds of requests during that hour, the expiration remains based on the original cache population.

This is called a **fixed TTL window**.

It is not a sliding TTL.

The build actually observed a TTL of `3281` seconds after several minutes, confirming this behavior.

----------

# 16. Why fixed TTL was acceptable

A sliding TTL would mean:

```text
every cache hit
      ↓
refresh expiration
```

A very popular link could therefore remain cached indefinitely.
<br>

The current fixed TTL gives the application a predictable upper bound on how long an untouched cache entry survives.

For this small project:

```text
1 hour fixed TTL
```

is a simple and defensible choice.
<br>

It is not necessarily the optimal TTL for every production system.

The important thing is that the behavior was intentional and understood.

----------

# 17. `cache_delete()`

The third helper is:

```python
def cache_delete(code: str) -> None:
    redis_client.delete(
        f"{CACHE_PREFIX}{code}"
    )
```

It removes:

```text
link:k6lOLa6
```

from Redis.

This becomes extremely important when deleting links.

----------

# 18. Why invalidation is necessary

Consider:

```text
PostgreSQL
    links
    └── k6lOLa6 → https://example.com
```

and:

```text
Redis
    link:k6lOLa6 → https://example.com
```

Now delete the PostgreSQL row.

If Redis isn't changed:

```text
PostgreSQL
    k6lOLa6
       ✗ deleted

Redis
    k6lOLa6
       ✓ still exists
```

The redirect code could still obtain the URL from Redis.

So the user could potentially access a link that the database says no longer exists.

This is the classic **cache invalidation problem**.

----------

# 19. The delete sequence

Snip handles deletion by:

```text
DELETE /links/{code}
        │
        ├──────────────┐
        │              │
        ▼              ▼
   PostgreSQL       Redis
   delete row       delete key
        │              │
        └──────┬───────┘
               ▼
          link is gone
```

The relevant route behavior is explicitly documented in the build:

> delete the row, events cascade, and invalidate the cache.

----------

# 20. What would happen without invalidation?

Suppose the application did:

```text
DELETE PostgreSQL row
```

but forgot:

```text
cache_delete(code)
```
<br>

Then Redis would continue returning:

```text
link:k6lOLa6
```

until its TTL expired.
<br>

Because the TTL is one hour, the deleted link could potentially continue resolving for up to roughly an hour.

That is why `cache_delete()` isn't just a performance detail.

It is part of the application's **correctness**.

The build specifically demonstrated this as the cache-invalidation failure mode.

----------

# 21. Cache-aside pattern

The overall design is called **cache-aside**.
<br>

The application controls the cache explicitly.

The pattern is:

```text
READ

    application
         │
         ▼
      Redis?
       /   \
     HIT   MISS
      │      │
      │      ▼
      │   PostgreSQL
      │      │
      │      ▼
      │   cache_set()
      │      │
      └──┬───┘
         ▼
       return
```
<br>

And for writes/deletes:

```text
WRITE / DELETE

    application
         │
         ▼
    PostgreSQL
         │
         ▼
 invalidate/update Redis
```

This is different from having Redis automatically synchronize itself with PostgreSQL.

The application is responsible for keeping the cache useful.

----------

# 22. Creating a link

When a link is created, the application doesn't wait for the first redirect to populate Redis.
<br>

After writing the link to PostgreSQL, it seeds the cache immediately:

```text
POST /links
      │
      ▼
generate code
      │
      ▼
INSERT PostgreSQL
      │
      ▼
cache_set(code, target_url)
      │
      ▼
return response
```

The build explicitly describes this as:

> Writes to Postgres, then seeds the cache so the first redirect is already fast.

This avoids an unnecessary initial cache miss.

----------

# 23. Redirect flow

The redirect route is the most interesting Redis interaction.

Conceptually:

```text
GET /k6lOLa6
       │
       ▼
find Link
       │
       ▼
cache_get("k6lOLa6")
       │
       ├─────────────── HIT
       │                  │
       │                  ▼
       │             target URL
       │
       └─────────────── MISS
                          │
                          ▼
                    target_url from DB
                          │
                          ▼
                    cache_set(...)
```

After that:

```text
record click
     │
     ▼
307 redirect
```

The build describes the redirect sequence explicitly.

----------

# 24. The important Phase 1 limitation

There is a subtle architectural problem here.

At first glance, you might think:

```text
Redis hit
    ↓
no PostgreSQL query
```

which would make the redirect much faster.

But the current implementation still needs the database because it records:

```text
links.clicks += 1
```

and:

```text
INSERT ClickEvent
```

for every redirect.
<br>

Therefore the current flow is effectively:

```text
GET /code
     │
     ▼
Redis
     │
     ▼
target URL
     │
     ▼
PostgreSQL
     │
     ├── increment counter
     └── insert event
```

So Redis currently accelerates the **code → URL lookup**, but does not eliminate the database interaction on the complete redirect path.

The README deliberately records this as a Phase 1 limitation.

----------

# 25. Why this is still useful

This does not mean the Redis implementation is pointless.

The project was designed as a learning project.

You successfully exercised:

```text
Redis client
      ↓
Compose DNS
      ↓
Redis key design
      ↓
TTL
      ↓
cache-aside
      ↓
cache population
      ↓
cache invalidation
      ↓
database/cache consistency
```

The limitation simply means the architecture hasn't yet reached the point where Redis can remove **all** database work from a redirect.

That is a natural Phase 2 optimization rather than something hidden or hand-waved.

----------

# 26. Why not immediately batch clicks in Redis?

The next optimization suggested by the project is roughly:

```text
redirect
    │
    ▼
Redis
    │
    ├── resolve URL
    │
    └── record click
          │
          ▼
       periodically
          │
          ▼
      PostgreSQL
```

Instead of doing:

```text
every redirect
    ↓
PostgreSQL UPDATE
    ↓
PostgreSQL INSERT
```

the application could accumulate click information in Redis and periodically flush it to PostgreSQL.
<br>

Then a cache hit could genuinely avoid PostgreSQL during the redirect.
<br>

That would make the cache architecture much more meaningful.
<br>

But it introduces new complexity:

-   durability concerns
    
-   lost clicks if Redis disappears
    
-   flush scheduling
    
-   concurrency
    
-   atomic counters
    
-   reconciliation
    
-   failure recovery
    
The original build therefore deliberately left this for Phase 2 rather than prematurely complicating Phase 1.

----------

# 27. Direct Redis verification

The project did not merely assume Redis worked.
<br>

Redis was inspected directly with:

```bash
docker compose exec cache redis-cli KEYS 'link:*'

docker compose exec cache redis-cli GET link:YOUR_CODE

docker compose exec cache redis-cli TTL link:YOUR_CODE
```

This tests three different things:

```text
KEYS
    ↓
does the cache key exist?

GET
    ↓
does it contain the expected URL?

TTL
    ↓
does expiration exist and have the expected lifetime?
```

The actual verification showed the code as the key, the target URL as its value, and a TTL around 3600 seconds.

This is an important Docker debugging habit:

> Don't only test the application interface. Inspect the dependency directly.

----------

# 28. Example Redis state

After creating:

```text
code = k6lOLa6
```

Redis conceptually contains:

```text
┌─────────────────────────────────────┐
│ Redis DB 0                          │
├─────────────────────────────────────┤
│                                     │
│ key:   link:k6lOLa6                 │
│ value: https://example.com/...      │
│ TTL:   ~3600 seconds                │
│                                     │
└─────────────────────────────────────┘
```
<br>

PostgreSQL simultaneously contains the authoritative record.

So the same URL exists in two places:

```text
PostgreSQL
    ↓
authoritative copy

Redis
    ↓
cached copy
```

This duplication is intentional.

----------

# 29. Cache miss

Suppose:

```text
link:k6lOLa6
```

doesn't exist in Redis.
<br>

The application receives:

```python
None
```

from:

```python
cache_get(code)
```
<br>

It then gets:

```text
target_url
```

from PostgreSQL and calls:

```python
cache_set(code, target_url)
```
<br>

The result is:

```text
Redis MISS
     │
     ▼
PostgreSQL
     │
     ▼
target URL
     │
     ▼
Redis SET
     │
     ▼
future request becomes HIT
```

This process is often described as **cache warming**.

----------

# 30. Cache hit

If the Redis key exists:

```text
link:k6lOLa6
```

then:

```python
cache_get("k6lOLa6")
```

returns the URL.
<br>

The application doesn't need Redis to understand anything about PostgreSQL.

Redis simply answers:

```text
"What URL is associated with this cached code?"
```

This simplicity is intentional.

----------

# 31. Cache expiration

Eventually:

```text
TTL → 0
```

Redis automatically removes the key.

Then:

```text
GET /k6lOLa6
       │
       ▼
Redis MISS
       │
       ▼
PostgreSQL
       │
       ▼
cache_set(...)
```

The application therefore naturally repopulates the cache.

No separate cache rebuild process is required for ordinary expiration.

----------

# 32. Cache invalidation after deletion

The more important case is deliberate invalidation.
<br>

Before deletion:

```text
Redis
link:k6lOLa6 → URL
```

Delete operation:

```text
DELETE /links/k6lOLa6
```
<br>

Then:

```text
PostgreSQL
    ↓
row deleted

Redis
    ↓
link:k6lOLa6 deleted
```

The project verified this directly with:

```bash
docker compose exec cache redis-cli EXISTS link:k6lOLa6
```

The expected result was:

```text
0
```

The subsequent redirect returned:

```text
404
```

This confirmed that the deleted PostgreSQL row could no longer be bypassed through a stale Redis key.

----------

# 33. Why Redis isn't persisted

The Compose design intentionally does not give Redis a persistent volume.

The reasoning is:

```text
Redis data
    ↓
reconstructable
    ↓
not authoritative
```

If the Redis container is destroyed:

```text
cache = empty
```

but:

```text
PostgreSQL = intact
```
<br>

The application can rebuild Redis as requests arrive.

PostgreSQL is different:

```text
PostgreSQL data
    ↓
not reconstructable from Redis
    ↓
must persist
```

Therefore PostgreSQL gets the named Docker volume while Redis does not.

----------

# 34. Redis's role in the Docker architecture

The full container relationship is:

```text
                       Docker Compose Network
                               │
             ┌─────────────────┼─────────────────┐
             │                 │                 │
             ▼                 ▼                 ▼
        ┌─────────┐      ┌───────────┐     ┌─────────┐
        │   API   │─────►│  Redis    │     │Postgres │
        │ FastAPI │      │   cache   │     │   DB    │
        └────┬────┘      └───────────┘     └────┬────┘
             │                                   │
             └───────────────────────────────────┘
```

The API is the only component that orchestrates the data flow.

Redis does not communicate directly with PostgreSQL.

PostgreSQL does not know that Redis exists.

The application sits between them.

----------

# 35. Why the cache abstraction is useful

Suppose `main.py` directly contained:

```python
redis_client.get(...)
redis_client.set(...)
redis_client.delete(...)
```

everywhere.
<br>

Then Redis-specific details would be spread throughout the application.

Instead:

```text
main.py
    │
    ├── cache_get()
    ├── cache_set()
    └── cache_delete()
           │
           ▼
       cache.py
           │
           ▼
         Redis
```

This creates a small abstraction boundary.

If the cache implementation changed later, fewer parts of the application would need to change.

For example, future-you could potentially replace Redis with another cache system without rewriting every route.

----------

# 36. What `cache.py` deliberately does NOT do

`cache.py` is intentionally small.
<br>

It does not decide:

```text
when to cache
when to invalidate
what the HTTP response should be
what a click means
whether a link exists
```
<br>

Those decisions belong to the application layer.

`cache.py` only provides:

```text
GET
SET + TTL
DELETE
```

This keeps the responsibilities clear.

----------

# 37. Redis failure vs PostgreSQL failure

This is one of the most important operational distinctions.

## Redis failure

```text
Redis unavailable
       │
       ▼
cache cannot be used
```

Depending on the application's error handling, requests may fail or fall back differently.
<br>

But conceptually, Redis is replaceable because its contents are derived state.

## PostgreSQL failure

```text
PostgreSQL unavailable
       │
       ▼
authoritative application state unavailable
```

This is fundamentally more serious.

Redis cannot fully replace PostgreSQL because Redis does not contain the complete application state.

----------

# 38. The consistency problem

Whenever the same information exists in two places:

```text
PostgreSQL
    +
Redis
```

there is a consistency problem to manage.
<br>

For Snip:

```text
PostgreSQL says:
k6lOLa6 → URL A

Redis says:
k6lOLa6 → URL A
```

Everything is good.
<br>

But imagine:

```text
PostgreSQL:
k6lOLa6 deleted

Redis:
k6lOLa6 → URL A
```

Now the cache is stale.
<br>

That is exactly why deletion performs:

```text
database deletion
+
cache invalidation
```

TTL provides another safety mechanism, but explicit invalidation is what makes deletion take effect immediately.

----------

# 39. TTL is a safety net, not the primary invalidation strategy

This distinction matters.

You could theoretically say:

> "We don't need `cache_delete()` because the key expires in one hour."

But that would mean accepting stale deleted links for up to an hour.

Instead, Snip does:

```text
normal expiration
    +
explicit invalidation
```
<br>

So:

```text
TTL
    ↓
protects against stale entries surviving forever

cache_delete
    ↓
handles known state changes immediately
```

That is the better mental model.

----------

# 40. Redis failure and stale data

The architecture assumes the database remains authoritative.

Therefore, if a cache entry is wrong or missing, the application should ultimately be able to recover the correct value from PostgreSQL.

This is the central property of a cache:

```text
Cache can be wrong/missing temporarily.
Source of truth cannot be replaced by it.
```

That principle is more important than any individual Redis command.

----------

# 41. Redis commands used in the project

The actual application needs only three core operations:

```text
GET
    cache_get()

SET EX
    cache_set()

DEL
    cache_delete()
```

The debugging process additionally used:

```text
KEYS
GET
TTL
EXISTS
```
<br>

The distinction is useful.

### Application operations

```text
GET
SET
DEL
```

### Verification/debugging operations

```text
KEYS
GET
TTL
EXISTS
```

The latter were used to inspect whether the architecture was actually behaving as intended.

----------

# 42. Why `KEYS` was acceptable for this project

During debugging, the project used:

```bash
redis-cli KEYS 'link:*'
```
<br>

This is useful for a tiny learning environment because it lets you inspect all matching keys immediately.

**It should not automatically be copied into a high-scale production application for frequent runtime use because `KEYS` can scan the keyspace.**
<br>

The important distinction is:

```text
debugging a tiny lab
    ≠
production runtime behavior
```

The command was used as an inspection tool, not as part of the application's request path.

----------

# 43. The complete Redis lifecycle of a link

A shortened link moves through Redis like this:

```text
CREATE
  │
  ▼
PostgreSQL INSERT
  │
  ▼
Redis SET
  │
  ▼
cached
```

Then:

```text
REDIRECT
  │
  ▼
Redis GET
  │
  ├── HIT ────────► URL
  │
  └── MISS
          │
          ▼
      PostgreSQL
          │
          ▼
       Redis SET
```

Eventually:

```text
TTL expires
     │
     ▼
Redis key removed
     │
     ▼
next request → cache miss
     │
     ▼
PostgreSQL
     │
     ▼
Redis SET again
```

Or the link is deleted:

```text
DELETE
  │
  ├──► PostgreSQL DELETE
  │
  └──► Redis DEL
```

That is the complete Phase 1 cache lifecycle.

----------

# 44. The important design decisions

These are the Redis decisions worth remembering.

### 1. Redis is a cache, not the source of truth

PostgreSQL owns the actual application state.

### 2. Only the hot lookup is cached

```text
code → target_url
```

not the entire link model.

### 3. Cache-aside was chosen

The application explicitly performs:

```text
GET → fallback → SET
```

rather than Redis automatically synchronizing with PostgreSQL.

### 4. Keys use a namespace

```text
link:<code>
```

### 5. Entries have a one-hour TTL

```text
3600 seconds
```

### 6. TTL is fixed

Cache hits do not refresh it.

### 7. New links seed the cache immediately

This avoids an unnecessary first-request miss.

### 8. Deleting a link explicitly invalidates Redis

This prevents stale deleted links from surviving until TTL expiration.

### 9. Redis is not persisted

It can be reconstructed from PostgreSQL.

### 10. Phase 1 still writes clicks to PostgreSQL synchronously

Therefore Redis does not yet eliminate all DB work from the redirect path.

----------

# 45. What Phase 2 could change

The current architecture leaves an obvious optimization path:

```text
Phase 1

Redis
  ↓
resolve URL

Postgres
  ↓
record click
```

Potential Phase 2:

```text
Redis
  ↓
resolve URL
  +
record click information
        │
        ▼
periodic batch
        │
        ▼
PostgreSQL
```


That would allow a genuine:

```text
Redis HIT
    ↓
no immediate PostgreSQL round trip
    ↓
redirect
```

But that is a substantially more complicated system.

It introduces questions around:

```text
durability
atomicity
flush intervals
lost events
Redis failure
concurrent workers
reconciliation
```

Therefore it was correctly left outside the Phase 1 implementation.

----------

# 46. Final mental model

If you come back to this project six months later, remember Redis as:

```text
                     SNIP CACHE
                         │
                         ▼
                ┌────────────────┐
                │     Redis      │
                ├────────────────┤
                │                │
                │ link:k6lOLa6    │
                │      ↓         │
                │ target URL      │
                │                │
                │ TTL = 3600s    │
                └───────┬────────┘
                        │
                        │ cache miss
                        ▼
                ┌────────────────┐
                │   PostgreSQL   │
                │ Source of Truth│
                └────────────────┘
```

And the three operations are:

```text
cache_get()
    ↓
"Do I already know this code?"

cache_set()
    ↓
"I got the authoritative value; cache it."

cache_delete()
    ↓
"The authoritative value was deleted; remove the stale copy."
```

The deepest concept in this implementation is not Redis itself.

It is this:

```text
             SOURCE OF TRUTH
                    │
                    ▼
              PostgreSQL
                    │
                    │ derive/cache
                    ▼
                 Redis
                    │
                    ▼
             faster reads
```

Redis is disposable.

PostgreSQL is authoritative.

The application is responsible for keeping the cached copy useful and invalidating it when the underlying state changes.

That is the caching architecture you actually built in Snip.
