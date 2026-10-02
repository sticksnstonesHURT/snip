# Request Lifecycle

This section describes how a request moves through Snip from the HTTP request to the final response.

## 1. Create a short link

Request:

```text
POST /links

```

Flow:

```text
Client
  ↓
FastAPI route
  ↓
Validate request body
  ↓
Generate short code
  ↓
Create Link record
  ↓
Save to PostgreSQL
  ↓
Return created link

```

The URL is validated through the request schema, then a short code is generated.

The new link is stored in PostgreSQL, which is the source of truth.

Redis is not required for creation because the cache is populated when the link is first requested.

----------

## 2. Redirect to a URL

Request:

```text
GET /{code}

```

This is the most important request path because it uses Redis caching.

```text
Client
  ↓
FastAPI
  ↓
Check Redis
  │
  ├── Cache HIT
  │      ↓
  │    Get target URL
  │
  └── Cache MISS
         ↓
      Query PostgreSQL
         ↓
      Store URL in Redis
  ↓
Record click
  ↓
Increment click counter
  ↓
HTTP redirect

```

### Cache hit

If Redis contains the short code:

```text
code → target URL

```

the target URL can be retrieved without querying PostgreSQL for the link itself.

### Cache miss

If Redis does not contain the code:

```text
Redis
  ↓
miss
  ↓
PostgreSQL
  ↓
find link
  ↓
store result in Redis

```

The next request for the same code can then use the cached value.

----------

## 3. Click tracking

A successful redirect also records the click.

Two things happen:

```text
click_events
    ↓
individual click record

links.clicks
    ↓
total click counter

```

The `click_events` table keeps individual click timestamps for statistics.

The counter on the link provides a cheap way to retrieve the total number of clicks without counting all event rows every time.

The redirect therefore isn't simply:

```text
lookup → redirect

```

It is:

```text
lookup
  ↓
record click
  ↓
update counter
  ↓
redirect

```

----------

## 4. Get link statistics

Request:

```text
GET /links/{code}/stats

```

Flow:

```text
Client
  ↓
FastAPI
  ↓
Find link
  ↓
Read click counter
  ↓
Read recent click events
  ↓
Return statistics

```

PostgreSQL is used because click statistics are persistent application data, not cache data.

----------

## 5. Delete a link

Request:

```text
DELETE /links/{code}

```

Flow:

```text
Client
  ↓
FastAPI
  ↓
Delete link + associated clicks
  ↓
Commit PostgreSQL transaction
  ↓
Invalidate Redis key
  ↓
Return response

```

Redis must be invalidated when a link is deleted.

Otherwise Redis could still contain:

```text
abc123 → https://example.com

```

even though `abc123` no longer exists in PostgreSQL.

----------

## 6. Health check

Request:

```text
GET /health

```

This is intentionally simple.

```text
Client
  ↓
FastAPI
  ↓
/health
  ↓
HTTP 200 response

```

It is primarily used to determine whether the API process is alive.

It is separate from the PostgreSQL and Redis Docker healthchecks, which check the readiness of those individual services.

----------

## Overall request model

The important distinction throughout the application is:

```text
                    ┌──────────────┐
                    │    Client    │
                    └──────┬───────┘
                           │
                           ▼
                    ┌──────────────┐
                    │   FastAPI    │
                    └──────┬───────┘
                           │
              ┌────────────┼────────────┐
              ▼            ▼            ▼
          PostgreSQL      Redis       Response
          source of      cache
           truth
```

PostgreSQL holds the durable application data.

Redis accelerates URL lookups.

FastAPI coordinates the request and response flow.
