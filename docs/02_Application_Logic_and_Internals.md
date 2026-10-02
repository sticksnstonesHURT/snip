# Snip — Application Internals

## 1. Overview

The `app/` directory contains the Python application itself.

```text
app/
├── __init__.py
├── config.py
├── db.py
├── models.py
├── schemas.py
├── cache.py
└── main.py

```

Each file has a deliberately narrow responsibility:

| File | Responsibility |
|------|----------------|
| `__init__.py` | Makes `app/` a Python package |
| `config.py` | Loads configuration from environment variables |
| `db.py` | Creates the SQLAlchemy engine and database sessions |
| `models.py` | Defines the PostgreSQL database models |
| `schemas.py` | Defines API request/response shapes |
| `cache.py` | Provides Redis cache helpers |
| `main.py` | Creates the FastAPI application and defines the routes |


<br>

The application can be understood as several layers:

```text
                    HTTP request
                         │
                         ▼
                    ┌─────────┐
                    │ main.py │
                    │ Routes  │
                    └────┬────┘
                         │
              ┌──────────┼──────────┐
              │          │          │
              ▼          ▼          ▼
           schemas     models      cache
              │          │          │
              │          ▼          ▼
              │        db.py      Redis
              │          │
              │          ▼
              │      PostgreSQL
              │
              ▼
          HTTP response

```

The important idea is that `main.py` coordinates the application, while the other modules provide the pieces it needs.

----------

# 2. `app/__init__.py`

This file is intentionally empty.

```text
app/
└── __init__.py
```

Its purpose is to make `app/` a Python package.
<br>
That allows imports such as:

```python
from app.models import Link

```

and allows Uvicorn to locate the application using:

```text
app.main:app
```
<br>
The notation means:

```text
app.main
   │
   └── Python module: app/main.py

app
   │
   └── FastAPI object inside that module

```

**FastAPI's documentation uses the same package/module structure for applications split across multiple files.**

There is no application logic in `__init__.py`.

----------

# 3. `app/config.py`

`config.py` is responsible for configuration.

The application needs values such as:

```text
DATABASE_URL
REDIS_URL
CODE_LENGTH
```
<br>
Instead of hardcoding those values into the Python source code, they are loaded from environment variables.

The conceptual flow is:

```text
.env / container environment
          │
          ▼
     Pydantic Settings
          │
          ▼
       Settings
          │
     ┌────┴─────┐
     ▼          ▼
 database_url  redis_url

```

The application then imports the shared settings object:

```python
from app.config import settings
```

This gives the rest of the application one consistent configuration source.
<br>
### Why use environment variables?

Because configuration and application code are different things.
<br>
For example, the same application image could theoretically run against:

```text
development database
```

or:

```text
production database
```

without changing the Python code.
<br>
The configuration changes; the application image does not.

This becomes particularly important once the application is containerized.

----------

# 4. `app/db.py`

`db.py` is the database infrastructure layer.
<br>
Its responsibilities are:

1.  create the SQLAlchemy engine
    
2.  create the session factory
    
3.  define the declarative base
    
4.  provide database sessions to FastAPI routes
    
<br>
The important objects are:

```text
engine
SessionLocal
Base
get_db()
```

----------

## 4.1 SQLAlchemy Engine

The application creates:

```python
engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
)
```

The engine is the long-lived SQLAlchemy object responsible for communicating with PostgreSQL.
<br>
Conceptually:

```text
Application
     │
     ▼
 SQLAlchemy Engine
     │
     ▼
connection pool
     │
     ▼
 PostgreSQL
```

The engine is not the same thing as a database session.

It represents the application's database connectivity infrastructure.

<br>

### `pool_pre_ping=True`

This tells SQLAlchemy to check pooled connections before using them.

This is useful in a containerized environment because PostgreSQL can restart independently of the API container.

Without connection validation, the application could potentially receive a stale connection from the pool.

----------

# 5. Database sessions


`SessionLocal` is the session factory:

```python
SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
)

```

A session represents a unit of work with the database.

The distinction is:

```text
Engine
  │
  └── manages database connectivity / pooling

Session
  │
  └── performs a unit of database work

```

<br>

**A request does not create a brand-new engine.
Instead, the route obtains a session from the existing session factory.**

----------

# 6. `get_db()`

The application uses a generator dependency:

```python
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

```
<br>


This is connected to FastAPI through:

```python
db: Session = Depends(get_db)
```


**FastAPI's dependency injection system calls the dependency, obtains its result, and supplies it to the route. Dependencies are specifically designed for shared resources such as database connections.**

<br>

The lifecycle is:

```text
HTTP request
     │
     ▼
FastAPI calls get_db()
     │
     ▼
SessionLocal()
     │
     ▼
database session
     │
     ▼
route executes
     │
     ▼
finally:
    db.close()
```

The important part is the `finally`.

Even if the route raises an exception, the session is closed.

This prevents database sessions from being left open.

----------

# 7. `Base`

`db.py` also defines:

```python
class Base(DeclarativeBase):
    pass
```

This is the base class from which the SQLAlchemy models inherit.

<br>

For example:

```python
class Link(Base):
    ...

```

and:

```python
class ClickEvent(Base):
    ...

```
<br>
SQLAlchemy uses these model definitions to understand the application's database schema.

The relationship is:

```text
Base
 │
 ├── Link
 │
 └── ClickEvent

```

Later, the application uses:

```python
Base.metadata.create_all(bind=engine)

```

to create the tables.
<br>
**That approach is intentionally a Phase 1 development convenience. It is not being treated as the production migration strategy.**

----------

# 8. `app/models.py`

`models.py` represents the **database layer**.
<br>
It defines two SQLAlchemy models:

```text
Link
ClickEvent

```
<br>
These correspond to:

```text
links
click_events

```
<br>

The distinction between models and schemas is important:


| | Models | Schemas |
|---|---|---|
| **File** | `models.py` | `schemas.py` |
| **Represents** | Database | API |
| **Technology** | SQLAlchemy | Pydantic |
| **Purpose** | Store data | Validate/shape data |
| **Example** | `Link` | `LinkOut` |

They may contain similar fields, but they serve different layers.

----------

# 9. `Link` model

The `Link` model represents one shortened URL.

Conceptually:

```text
links
├── id
├── code
├── target_url
├── clicks
└── created_at
```
<br>
The important fields are:

### `id`

Internal primary key.

```python
id: Mapped[int] = mapped_column(
    Integer,
    primary_key=True,
)

```

This is used internally by the database.

It is deliberately not exposed through the public API response.

----------

### `code`

```python
code: Mapped[str] = mapped_column(
    String(16),
    unique=True,
    index=True,
)

```

This is the actual short code.
<br>
Example:

```text
aB3xK9z
```

`unique=True` means two links cannot have the same code.

`index=True` is important because one of the hottest queries in the application is:

```sql
WHERE code = ?
```

The redirect endpoint needs to find a link from its short code.

Therefore the lookup is indexed.
<br>
**The model was intentionally designed around the application's actual access pattern rather than just storing data.**

----------

### `target_url`

```python
target_url: Mapped[str] = mapped_column(
    String(2048)
)

```

This is the original destination URL.
<br>
Example:

```text
https://example.com/some/long/path
```

----------

### `clicks`

```python
clicks: Mapped[int] = mapped_column(
    Integer,
    default=0,
    server_default="0",
)

```

This is a denormalized counter.
<br>
Every redirect increments it.


<br>

**Why maintain a counter if individual click events already exist?**

Because:

```text
SELECT clicks FROM links

```

is much cheaper than repeatedly counting every click event when the application only needs the total.
<br>
So the application keeps:

```text
Link.clicks

```

for the cheap total and:

```text
ClickEvent

```

for historical information.

----------

# 10. `default` vs `server_default`

This is an important SQLAlchemy detail from the project.
<br>
The model uses both:

```python
default=0
server_default="0"

```

They are not the same thing.

### `default=0`

The default is applied by SQLAlchemy/Python when the row is inserted through the ORM.

### `server_default="0"`

The default is defined at the PostgreSQL level.

That means PostgreSQL can still provide the default if something inserts a row without going through the SQLAlchemy ORM.


<br>


Conceptually:

```text
SQLAlchemy insert
      │
      ▼
Python-side default

Raw SQL / other DB client
      │
      ▼
PostgreSQL server default
```

Both were used deliberately so the database itself still has a sensible default.

----------

# 11. `created_at`

The model uses:

```python
DateTime(timezone=True)

```

with:

```python
server_default=func.now()

```

This means PostgreSQL supplies the timestamp.

The reasoning is useful in a containerized application.


<br>


There are multiple processes/containers:

```text
API container
DB container
```

Rather than having application code decide the timestamp, the database is treated as the authoritative source of database timestamps.

----------

# 12. `ClickEvent`

`ClickEvent` represents one individual redirect.

```text
click_events
├── id
├── link_id
└── clicked_at

```

Every time someone visits:

```text
/{code}

```

the application creates a `ClickEvent`.

Therefore:

```text
one Link
   │
   ├── ClickEvent
   ├── ClickEvent
   ├── ClickEvent
   └── ...

```

This is what makes the `/stats` endpoint capable of returning recent click timestamps rather than only the total count.

----------

# 13. Relationship between `Link` and `ClickEvent`

The relationship is one-to-many:

```text
Link
 │
 ├── Event 1
 ├── Event 2
 ├── Event 3
 └── Event 4

```



<br>

A click event belongs to exactly one link.

The foreign key is:

```python
ForeignKey(
    "links.id",
    ondelete="CASCADE",
)

```

The relationship also specifies:

```python
cascade="all, delete-orphan"

```

This means deleting a link also removes its associated click events.


<br>

Conceptually:

```text
DELETE Link
     │
     ▼
ClickEvents belonging to Link
     │
     ▼
deleted

```

This prevents orphaned click records.

----------

# 14. `app/schemas.py`

`schemas.py` defines the application's API contract.

It uses Pydantic.


<br>

There are three schemas:

```text
LinkCreate
LinkOut
LinkStats

```

These are not database models.

They describe what enters and leaves the API.

----------

# 15. `LinkCreate`

```python
class LinkCreate(BaseModel):
    url: HttpUrl

```

<br>

This represents the request body for:

```text
POST /links

```

Expected request:

```json
{
  "url": "https://example.com/some/path"
}

```


<br>

The important part is:

```python
HttpUrl
```

Pydantic validates the value before the route logic executes.

For example, an invalid URL does not reach the link-generation logic.

FastAPI then returns a validation error, normally HTTP `422`.

**This avoids manually writing URL validation code inside the route.**

----------

# 16. `LinkOut`

```python
class LinkOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    code: str
    target_url: str
    clicks: int
    created_at: datetime

```

This is the public representation of a link.


<br>

Notice what is missing:

```text
id
```

The database has an internal ID, but clients do not need to know about it.

<br>

The API exposes:

```text
code
target_url
clicks
created_at

```

but hides:

```text
database primary key
```

This is one of the main reasons the project keeps models and schemas separate.

----------

# 17. `from_attributes=True`

The route returns a SQLAlchemy object:

```python
return link
```

But `LinkOut` is a Pydantic model.

These are different object types.

```text
SQLAlchemy Link object
        │
        ▼
Pydantic LinkOut
        │
        ▼
JSON response
```

This configuration:

```python
ConfigDict(from_attributes=True)
```

allows Pydantic to construct the response model from object attributes rather than requiring a normal dictionary.

<br>

It is the bridge between:

```text
database object
```

and:

```text
API response
```

----------

# 18. `LinkStats`

```python
class LinkStats(BaseModel):
    code: str
    clicks: int
    recent_clicks: list[datetime]
```

This represents:

```text
GET /links/{code}/stats
```

<br>

The response contains:

-   short code
    
-   total clicks
    
-   recent click timestamps
    

<br>

**The project limits the returned click history to the 20 most recent events.**

The total comes from:

```text
Link.clicks
```

while the timestamps come from:

```text
ClickEvent.clicked_at
```

**This demonstrates why the application stores both a counter and individual events.**

----------

# 19. `app/cache.py`

`cache.py` isolates Redis-specific logic.

It creates one Redis client:

```python
redis_client = redis.from_url(
    settings.redis_url,
    decode_responses=True,
)
```

<br>

The module provides three helpers:

```text
cache_get()
cache_set()
cache_delete()
```

This keeps Redis implementation details out of the route logic.

<br>

Instead of doing this throughout `main.py`:

```python
redis_client.get(...)
redis_client.set(...)
redis_client.delete(...)
```

the routes use:

```python
cache_get(...)
cache_set(...)
cache_delete(...)
```

This makes the application code easier to read and gives Redis one clear integration point.

----------

# 20. Redis client creation

An important subtlety:

```python
redis.from_url(...)
```

creates/configures the Redis client.


<br>

It does not mean the application has already successfully communicated with Redis.

The first actual Redis operation is what needs the server to be reachable.

Therefore importing `cache.py` does not immediately require Redis to be available.

This is useful during application startup and local development.

----------

# 21. `decode_responses=True`

Redis normally returns byte strings.

<br>

Without:

```python
decode_responses=True

```

a value such as:

```text
https://example.com
```

could arrive in Python as bytes rather than a normal string.

<br>

With it enabled:

```text
Redis
  ↓
Python str
```

which is what the rest of the application expects.

----------

# 22. Redis key namespace

The application defines:

```python
CACHE_PREFIX = "link:"
```

<br>

A link with code:

```text
aB3xK9z
```

therefore becomes:

```text
link:aB3xK9z
```

rather than simply:

```text
aB3xK9z

```

<br>


This creates a namespace.

If the application later caches other types of objects, their keys can use different prefixes:

```text
link:aB3xK9z
user:123
stats:aB3xK9z
```

This reduces the chance of key collisions.

----------

# 23. Cache TTL

`cache_set()` uses:

```python
ex=3600
```

so cache entries expire after:

```text
3600 seconds
=
1 hour
```

<br>

**The reasoning is that Redis is a cache, not the authoritative data store.**


<br>

If a cache entry disappears:

```text
Redis key expires
       │
       ▼
next request misses cache
       │
       ▼
PostgreSQL provides the value
       │
       ▼
cache is warmed again

```

This is the cache-aside pattern.

----------

# 24. Cache-aside pattern

The application's cache behavior is:

```text
READ

Redis
 │
 ├── HIT ───────────► return cached value
 │
 └── MISS
       │
       ▼
   PostgreSQL
       │
       ▼
   Redis SET
       │
       ▼
   return value
```


<br>

For writes/deletes:

```text
WRITE

PostgreSQL
    │
    ▼
change authoritative state
    │
    ▼
update/invalidate Redis
```


<br>

The routes are responsible for orchestrating this pattern.

`cache.py` only provides the Redis operations.

----------

# 25. `app/main.py`

`main.py` is the central application module.

It brings the other modules together:

```python
from app.cache import ...
from app.config import settings
from app.db import ...
from app.models import ...
from app.schemas import ...
```


<br>

The dependency structure is roughly:

```text
main.py
 │
 ├── config.py
 │
 ├── db.py
 │    └── PostgreSQL
 │
 ├── models.py
 │
 ├── schemas.py
 │
 └── cache.py
      └── Redis
```

**This is why `main.py` can remain focused on application behavior instead of containing database setup, Redis implementation, and validation logic all in one file.**

This is standard application build philosophy.


----------

# 26. FastAPI application object

The application is created with:

```python
app = FastAPI(
    title="Snip",
    version="0.1.0",
    lifespan=lifespan,
)
```

This `app` object is what Uvicorn loads:

```text
uvicorn app.main:app
```

**FastAPI uses the application object to register routes and generate the OpenAPI schema that powers the interactive documentation at `/docs`.**

----------

# 27. Application lifespan

The application defines:

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    yield
```

This is startup/shutdown lifecycle handling.


<br>

For this project, the startup action is:

```python
Base.metadata.create_all(bind=engine)

```

which creates the database tables if they don't already exist.


<br>

The sequence is:

```text
API starts
   │
   ▼
lifespan begins
   │
   ▼
create_all()
   │
   ▼
tables exist
   │
   ▼
yield
   │
   ▼
API serves requests
```

<br>

This is deliberately a development/learning shortcut.

It is not a replacement for database migrations.

If the schema later needs to be changed, `create_all()` does not provide the versioned migration workflow that a tool such as Alembic provides.

----------

# 28. Generating short codes

The application defines:

```python
ALPHABET = string.ascii_letters + string.digits

```

and:

```python
def generate_code(length: int) -> str:
    return "".join(
        secrets.choice(ALPHABET)
        for _ in range(length)
    )
```

<br>

The available characters are:

```text
a-z
A-Z
0-9
```

<br>

The important choice here is:

```python
secrets.choice()
```

rather than:

```python
random.choice()
```

<br>

**The project uses `secrets` because short codes should not be unnecessarily predictable.**

The generated code is also checked against PostgreSQL before being accepted.

----------

# 29. Collision handling

Creating a short code involves a theoretical collision:

```text
generated code
      │
      ▼
Does it already exist?
      │
   ┌──┴──┐
   │     │
  YES    NO
   │     │
retry    use it

```

<br>

The application retries up to five times.

Conceptually:

```python
for _ in range(5):
    code = generate_code(...)
    if code_is_available:
        break
else:
    raise HTTPException(...)
```

If all five attempts somehow collide, the API returns HTTP `500`.

**The important point is that the application does not simply assume random generation guarantees uniqueness.**

The database remains authoritative for uniqueness.

----------

# 30. `GET /health` of the API

```python
@app.get("/health")
def health():
    return {"status": "ok"}
```

This is intentionally simple.

<br>

It answers:

```text
Is the API process responding?

```

It is a liveness check.

<br>

It does not attempt to prove that:

```text
PostgreSQL is healthy
Redis is healthy
```

**Those dependencies are handled separately through Docker Compose healthchecks.**

----------

# 31. `POST /links`

This endpoint creates a short link.

```text
POST /links
```

<br>

The request enters through:

```python
payload: LinkCreate
```

so validation happens before the route logic.


<br>

The flow is:

```text
HTTP request
    │
    ▼
LinkCreate validation
    │
    ▼
extract URL
    │
    ▼
generate code
    │
    ▼
check collision
    │
    ▼
create Link
    │
    ▼
PostgreSQL commit
    │
    ▼
Redis cache_set()
    │
    ▼
LinkOut response
```

<br>

The important ordering is:

```text
PostgreSQL first
Redis second
```

because PostgreSQL is the source of truth.

----------

# 32. `GET /links`

This endpoint lists links:

```text
GET /links
```

<br>

The query orders by:

```python
Link.created_at.desc()
```

so newest links appear first.

<br>

The response model is:

```python
list[LinkOut]
```

Again, the database model is not exposed directly as the API contract.

----------

# 33. `GET /links/{code}/stats`

This endpoint retrieves statistics for a link.

<br>

The first query finds the link:

```text
code → Link
```

If it doesn't exist:

```text
404
```

<br>

Then the application retrieves up to 20 recent click timestamps:

```text
ClickEvent
   │
   ├── filter by link_id
   ├── newest first
   └── limit 20
```

<br>

The final response combines:

```text
Link.clicks
+
recent ClickEvent timestamps
```

into:

```text
LinkStats
```

----------

# 34. `DELETE /links/{code}`

Deletion follows:

```text
find Link
   │
   ├── missing → 404
   │
   ▼
delete Link
   │
   ▼
PostgreSQL commit
   │
   ▼
delete Redis key
   │
   ▼
204 No Content
```

<br>

**Deleting the SQLAlchemy `Link` causes its associated click events to be deleted through the configured cascade behavior.**

Then:

```python
cache_delete(code)
```

removes the corresponding Redis entry.

<br>

This is critical.

If the database row were deleted but the Redis key remained:

```text
PostgreSQL → link doesn't exist
Redis       → link still exists

```

and the redirect could continue resolving the deleted code until the cache expired.

That is a classic cache invalidation problem.

----------

# 35. `GET /{code}` — redirect

This is the most interesting route.

```text
GET /{code}
```

<br>

The complete flow is:

```text
Request
   │
   ▼
Find Link in PostgreSQL
   │
   ▼
Redis lookup
   │
   ├── HIT
   │    │
   │    └── use cached URL
   │
   └── MISS
        │
        ▼
     use DB URL
        │
        ▼
     populate Redis
        │
        ▼
increment Link.clicks
        │
        ▼
create ClickEvent
        │
        ▼
commit
        │
        ▼
307 redirect
```

<br>

The response is:

```text
HTTP 307 Temporary Redirect
```

to the target URL.

----------

# 36. Why the redirect currently still touches PostgreSQL??

At first glance, Redis might appear pointless because the route already retrieves the `Link` from PostgreSQL.

That is a real limitation of Phase 1.


<br>


The current sequence is effectively:

```text
PostgreSQL → find Link
       +
Redis → get cached URL
       +
PostgreSQL → record click

```

Therefore the cache does not currently eliminate the database interaction.

<br>

#### Why keep it?

- Because this project was designed to exercise caching and cache invalidation without adding unnecessary complexity.

- A natural Phase 2 design would move click aggregation into Redis and periodically flush those counters/events into PostgreSQL.

- Then a cache hit could potentially avoid PostgreSQL entirely during the redirect path.

That optimization was deliberately left out of Phase 1.

----------

# 37. Why `GET /{code}` is declared last?

This is an important FastAPI routing detail.

<br>

The route:

```text
/{code}
```

matches any single path segment.

<br>

Therefore it could potentially match:

```text
/health
```

or:

```text
/links
```

if those routes were not matched first.

<br>

The application therefore declares the specific routes first:

```text
/health
/links
/links/{code}/stats
/links/{code}
```

and the generic redirect route last:

```text
/{code}
```

This prevents the generic route from swallowing more specific paths.

<br>

Route ordering matters because FastAPI matches registered routes in order. 

FastAPI's routing model is based on path operations registered against the application/router.

----------

# 38. Complete application dependency map

The easiest way to understand the Python code is:

```text
                         main.py
                            │
        ┌───────────────────┼────────────────────┐
        │                   │                    │
        ▼                   ▼                    ▼
    schemas.py          models.py            cache.py
        │                   │                    │
        │                   ▼                    ▼
        │                 db.py                Redis
        │                   │
        │                   ▼
        │              PostgreSQL
        │
        ▼
   HTTP contract

```


<br>

More concretely:

```text
config.py
   │
   ├──────────────► db.py ─────────► PostgreSQL
   │
   └──────────────► cache.py ──────► Redis
                         ▲
                         │
                      main.py
                         ▲
                         │
                     schemas.py
                         │
                      models.py
```

----------

# 39. One complete request: creating a link [Example]


Suppose the client sends:

```http
POST /links
Content-Type: application/json

{
  "url": "https://example.com/some/path"
}
```

<br>

The application does:

```text
1. FastAPI receives request
            │
            ▼
2. Pydantic validates LinkCreate
            │
            ▼
3. URL is converted to string
            │
            ▼
4. secrets generates short code
            │
            ▼
5. PostgreSQL checks for collision
            │
            ▼
6. Link object created
            │
            ▼
7. Session commits
            │
            ▼
8. SQLAlchemy refreshes object
            │
            ▼
9. Redis stores link:<code>
            │
            ▼
10. LinkOut shapes response
            │
            ▼
11. HTTP 201 Created
```

----------

# 40. One complete request: redirect

Suppose the client requests:

```text
GET /aB3xK9z
```

The application does:

```text
1. FastAPI matches /{code}
            │
            ▼
2. Find Link in PostgreSQL
            │
            ▼
3. Look for link:aB3xK9z in Redis
            │
       ┌────┴────┐
       │         │
      HIT       MISS
       │         │
       │         ▼
       │      DB URL
       │         │
       │         ▼
       │      Redis SET
       │
       └────┬────┘
            │
            ▼
4. Increment Link.clicks
            │
            ▼
5. Insert ClickEvent
            │
            ▼
6. Commit transaction
            │
            ▼
7. Return HTTP 307
```

----------

# 41. Why the application is split into these files??

The split is not arbitrary.

A single `main.py` could technically contain everything:

```text
configuration
database
models
schemas
Redis
routes
```
but that would quickly become difficult to understand.

<br>

Instead:

```text
config.py → configuration
db.py     → database infrastructure
models.py → persistence representation
schemas.py → API representation
cache.py  → Redis integration
main.py   → application behavior
```

**This gives each module a clear reason to exist.**

The architecture is still intentionally small. 

There is no unnecessary service layer, repository layer, or large router hierarchy.

That was appropriate for the scope of this Phase 1 project.

----------

# 42. The most important distinctions to remember

If returning to this project months later, these are the concepts worth remembering.


### Model vs Schema

```text
SQLAlchemy Model
      ↓
database

Pydantic Schema
      ↓
     API
```

They are separate because the database representation and public API representation have different responsibilities.

----------

### Engine vs Session

```text
Engine
  ↓
database connectivity / pool

Session
  ↓
individual unit of work
```

----------

### PostgreSQL vs Redis

```text
PostgreSQL
    ↓
authoritative state

Redis
    ↓
cached state
```

----------

### Counter vs ClickEvent

```text
Link.clicks
    ↓
cheap total

ClickEvent
    ↓
individual history
```

----------

### Cache helper vs route

```text
cache.py
    ↓
knows HOW to talk to Redis

main.py
    ↓
knows WHEN Redis should be used
```

----------

### Validation vs business logic

```text
Pydantic
    ↓
"Is this request shaped correctly?"

Route logic
    ↓
"What should the application do with it?"
```

This separation keeps the actual route logic focused on the application's behavior.

----------

# 43. Final mental model

When opening the `app/` directory, think:

```text
                 REQUEST
                    │
                    ▼
                main.py
             "What should happen?"
                    │
          ┌─────────┼─────────┐
          │         │         │
          ▼         ▼         ▼
      schemas     models     cache
      "API"       "DB"      "Redis"
          │         │         │
          │         ▼         ▼
          │       db.py     Redis
          │         │
          │         ▼
          │     PostgreSQL
          │
          ▼
       RESPONSE
```

The key principle is:

> **`main.py` orchestrates. The other modules provide specialized pieces.**

That is the mental model to carry into the Docker documentation later: 

Docker is packaging and running this application structure; it is not changing what these Python layers fundamentally do.
