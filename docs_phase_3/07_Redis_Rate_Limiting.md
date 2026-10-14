# Redis Rate Limiting

## 1. Purpose

Phase 3 introduces Redis-based rate limiting for authenticated API requests.

API-key authentication determines whether a client is authorized to perform protected operations. Rate limiting addresses a different concern: how frequently a client can make requests.

Without rate limiting, a client with a valid API key could send a large volume of requests in a short period, potentially increasing application load or consuming database resources.
<br>

Snip uses Redis to track request counts and reject requests that exceed the configured limit.

The objectives are to:

-   Limit request frequency per API key.
    
-   Use Redis for shared request-counter storage.
    
-   Return an appropriate HTTP response when the limit is exceeded.
    
-   Keep rate-limit identity separate from the raw API key.
    
-   Reduce excessive request traffic to protected endpoints.
    

-----------------------------------

## 2. Configuration

The recorded Phase 3 implementation uses a fixed-window limit of **60 requests per minute per API key**.


| Setting | Behaviour |
|---------|-----------|
| Rate limit | 60 requests |
| Window | 60 seconds |
| Identity | API-key hash |
| Storage | Redis |
| Exceeded limit | HTTP `429 Too Many Requests` |

<br>

The limiter uses a Redis key following this pattern:

```
ratelimit:<key-hash>:<window>
```

Here, `<key-hash>` identifies the API key, and `<window>` identifies the current time window.
<br>

The actual Redis key includes the corresponding values rather than these literal placeholders.


-------------------

## 3. Why Rate Limiting Is Per API Key

A rate limit needs an identity to determine which requests belong to the same client or credential.
<br>

Snip uses the API-key hash as that identity.

Conceptually:

```
Incoming request
      |
      v
Extract X-API-Key
      |
      v
Validate API key
      |
      v
Calculate key hash
      |
      v
Determine current time window
      |
      v
Increment Redis request counter
      |
      v
Check request limit
      |
      +---- Within limit ----> Continue request
      |
      +---- Over limit ------> Return HTTP 429
```

This allows different API keys to have separate counters.
<br>

For example, if two clients use two different valid API keys, each key has its own rate-limit bucket. One client's requests do not automatically consume the other client's quota.

The limit is per credential, not necessarily per physical person, device, or IP address. A client with multiple valid keys may have multiple independent buckets.


-------------------

## 4. How the Fixed-Window Algorithm Works

A fixed-window limiter divides time into discrete intervals.
<br>

For Snip, each interval is one minute long.

The application identifies the current window and increments the corresponding Redis counter for the API key. Requests are permitted while the counter remains within the configured limit.
<br>

Once the counter exceeds 60, the application rejects additional requests for that key during the current window.

The general algorithm is:

1.  Identify the authenticated API key.
    
2.  Determine the current one-minute window.
    
3.  Construct the Redis counter key.
    
4.  Increment the counter.
    
5.  Set an expiry for the counter when required.
    
6.  Compare the count with the configured limit.
    
7.  Allow the request or return `429 Too Many Requests`.
    
<br>

A conceptual example:

```
# Conceptual pseudocode, not the exact Snip implementation.
count = increment_redis_counter(key_hash, current_window)

if count > 60:
    return HTTP_429

continue_request()
```

The first 60 counted requests fall within the limit. Requests beyond that limit are rejected for that window.

---------------------------------------

## 5. Why Redis Is Used

Redis provides fast in-memory counters that can be incremented without storing every request in PostgreSQL.

Using Redis for rate-limit state avoids making the primary database responsible for every rate-limit counter update.

It also keeps the rate-limit mechanism separate from the application's persistent link and click records.

The Redis key identifies a particular API key and time window. When that window expires, the corresponding counter no longer needs to be retained.

However, Redis availability becomes important to request handling. The application must have a defined policy for what happens if it cannot access Redis to evaluate the limit. That policy should be confirmed in the implementation rather than assumed.


---------------

## 6. HTTP 429: Too Many Requests

When a client exceeds its limit, Snip returns:

```
HTTP/1.1 429 Too Many Requests
```
<br>

This status code communicates that the client has sent too many requests within the applicable period.

A client receiving this response should avoid immediately repeating the same request at high frequency. It should wait before retrying.
<br>

A more developed rate-limiting implementation could also return a `Retry-After` header or rate-limit metadata.

------------------------------

## 7. Fixed-Window Boundary Bursts

The principal limitation of a fixed-window algorithm is that it can allow a burst across a window boundary.

Consider two adjacent one-minute windows:

| Time | Requests |
|------|----------|
| 12:00:00–12:00:59 | 60 |
| 12:01:00–12:01:59 | 60 |

<br>


A client could send 60 requests near the end of the first window and another 60 immediately after the second window begins.

The client could therefore make approximately 120 requests within a short period spanning the boundary, even though it never exceeds 60 requests in either individual window.

This is expected behaviour for a fixed-window limiter, not necessarily an implementation defect.
<br>

### Why it matters

The fixed-window algorithm is simple and efficient, but it does not guarantee a smooth request rate.

If Snip needs stricter burst control, alternative algorithms could be considered:

-   **Sliding window:** Tracks requests over a rolling time interval.
    
-   **Sliding-window counter:** Estimates rolling-window usage using adjacent fixed-window counters.
    
-   **Token bucket:** Allows controlled bursts while enforcing a longer-term rate.
    
-   **Leaky bucket:** Smooths request processing or admission according to a defined rate.
    

These are possible future improvements, not features established by the current Phase 3 implementation.

---------------------------

## 8. Authentication and Rate-Limit Ordering

Authentication and rate limiting serve different purposes.

Authentication rejects missing or invalid credentials. Rate limiting controls request frequency for an identified credential.
<br>

The intended relationship is:

1.  Authenticate the API key.
    
2.  Identify the key's rate-limit bucket.
    
3.  Evaluate the request counter.
    
4.  Continue processing if the request is permitted.
    
<br>

Invalid credentials can still impose some processing cost, such as a database lookup. The recorded implementation does not establish a separate IP-based limit for unauthenticated requests.

This means API-key rate limiting alone is not a complete defence against all forms of abuse. Additional controls may be appropriate for a publicly exposed service.


-------------


## 9. Verification Performed

The recorded Phase 3 regression test exercised the configured rate limit.

The test established that:

-   Requests within the permitted limit were accepted.
    
-   After the counter exceeded the limit, the API returned `429 Too Many Requests`.
    
<br>

This verifies the tested over-limit behaviour.

It does not independently prove performance under heavy concurrency, strict rolling-window enforcement, rate limiting across multiple application instances in every failure scenario, or protection against all unauthenticated traffic.


-------


## 10. Troubleshooting

### A valid API key receives `429`

Check the number of requests made by that key during the current minute.

Because the limiter uses fixed windows, the counter resets according to the window boundary rather than exactly 60 seconds after each individual request.
<br>

### A new key has a separate limit

This is expected when the key has a different hash and therefore a different rate-limit bucket.
<br>

### Rate limiting stops working when Redis is unavailable

Inspect the Redis connection and application logs. Verify the application's failure behaviour when Redis cannot evaluate the counter.

Do not assume that the application fails open or fails closed without checking its implementation.
<br>

### Requests appear to exceed 60 in a short period

Check whether they straddle two fixed-window boundaries. A fixed-window limiter can permit a burst across adjacent windows.


--------------

## 11. Key Takeaways

-   Snip uses Redis-based fixed-window rate limiting.
    
-   The configured limit is 60 requests per minute per API key.
    
-   Redis counter keys include the API-key hash and current window.
    
-   Requests exceeding the limit receive HTTP `429 Too Many Requests`.
    
-   Fixed-window limits can permit bursts across window boundaries.
    
-   API-key rate limiting does not replace IP-based controls or other abuse-prevention measures.
    
-   Sliding-window or token-bucket algorithms are possible future improvements, not current Phase 3 features.
