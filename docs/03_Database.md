# Snip — Database Design & Internals

## 1. Purpose of the database

PostgreSQL is the **source of truth** for Snip.

<br>

Redis is only a cache. PostgreSQL contains the information that must survive:

-   container restarts
    
-   Redis restarts
    
-   cache expiration
    
-   application restarts
    

<br>

The database stores two types of information:

```text
links
    ↓
the actual shortened URLs

click_events
    ↓
individual redirect events
```

<br>

The relationship is:

```text
                 ┌──────────────┐
                 │    links     │
                 ├──────────────┤
                 │ id           │
                 │ code         │
                 │ target_url   │
                 │ clicks       │
                 │ created_at   │
                 └──────┬───────┘
                        │
                     1  │
                        │
                     N  │
                        ▼
              ┌──────────────────┐
              │   click_events   │
              ├──────────────────┤
              │ id               │
              │ link_id          │
              │ clicked_at       │
              └──────────────────┘

```

One link can therefore have many click events.

----------

# 2. Where the database definition actually comes from

There are two layers involved.

## Application layer

The schema is defined in:

```text
app/models.py

```

using SQLAlchemy.

The important models are:

```python
class Link(Base):
    ...

class ClickEvent(Base):
    ...
```

## Database layer

When the application starts, Phase 1 runs:

```python
Base.metadata.create_all(bind=engine)

```

This asks SQLAlchemy to create the tables represented by the registered models if they don't already exist.

<br>

The important limitation is that `create_all()` is **not a migration system**.

It is sufficient for this learning project because the schema is created from scratch.

It does not provide a proper mechanism for versioning future schema changes.
<br>

For example, if the application later changed:

```text
target_url VARCHAR(2048)

```

into:

```text
target_url TEXT

```

`create_all()` is not the tool you would use to safely manage that schema evolution.

<br>

That is why the project identifies Alembic migrations as the production approach.

This is a deliberate Phase 1 simplification, not an accidental omission.

----------

# 3. The actual tables

The running PostgreSQL container was inspected directly with:

```bash
docker compose exec db psql -U snip -d snip -c "\dt"
```

The result showed:

```text
Schema | Name         | Type  | Owner
-------+--------------+-------+------
public | click_events | table | snip
public | links        | table | snip
```

**So the application creates exactly these two application tables in the `public` schema.**

----------

# 4. The `links` table

The SQLAlchemy model is:

```python
class Link(Base):
    __tablename__ = "links"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    code: Mapped[str] = mapped_column(
        String(16),
        unique=True,
        index=True,
    )

    target_url: Mapped[str] = mapped_column(
        String(2048)
    )

    clicks: Mapped[int] = mapped_column(
        Integer,
        default=0,
        server_default="0",
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
```

The model is directly documented in the original build record.

<br>

The resulting conceptual table is:

| Column | Type | Purpose |
|--------|------|---------|
| `id` | Integer | Internal primary key |
| `code` | String(16) | Public short code |
| `target_url` | String(2048) | Original destination |
| `clicks` | Integer | Fast total-click counter |
| `created_at` | Timestamp with timezone | Creation timestamp |

----------

# 5. `id`

```python
id: Mapped[int] = mapped_column(
    Integer,
    primary_key=True,
)

```

This is the database's internal identifier.

For example:

```text
id = 1
```

The application does **not** use this as the public short code.

The user sees:

```text
k6lOLa6
```

rather than:

```text
1
```

<br>

So there are two different concepts:

```text
Database identity
        ↓
id = 1

Application/public identity
        ↓
code = k6lOLa6
```

This separation is intentional.

**The internal database ID can change or remain an implementation detail without changing how users access their shortened URL.**

The API schema also deliberately does not expose `id`.

----------

# 6. `code`

```python
code: Mapped[str] = mapped_column(
    String(16),
    unique=True,
    index=True,
)
```

<br>

This is the short code used in the URL:

```text
http://localhost:8000/k6lOLa6
```

There are two important database constraints here:

```text
unique=True
index=True
```

They solve two different problems.

----------

## 6.1 Why `unique=True`?

Two different links cannot have the same short code.

Without uniqueness:

```text
id | code    | target_url
---+---------+----------------------
1  | abc123  | example.com
2  | abc123  | another.com
```

would create ambiguity.
<br>

The application wouldn't know which URL should redirect to.

The database therefore enforces:

```text
one code → one link
```

----------

# 7. Why `index=True`?

The most important lookup in the application is:

```sql
SELECT ...
FROM links
WHERE code = ?
```

The redirect endpoint needs to turn:

```text
k6lOLa6
```

into:

```text
Link
```

very frequently.

<br>

An index makes this lookup efficient as the table grows.

So the two properties have different jobs:

```text
UNIQUE
    ↓
prevents duplicate codes

INDEX
    ↓
makes code lookups efficient
```

<br>

A useful distinction:

> A unique constraint and an index are related database concepts, but their purposes here are not the same.

The original implementation explicitly chose `unique=True, index=True` because code lookup is the hot lookup path.

----------

# 8. `target_url`

```python
target_url: Mapped[str] = mapped_column(
    String(2048)
)
```

<br>

This stores the actual destination.

Example:

```text
https://www.youtube.com/playlist?list=...
```

The short code is merely the lookup key.

<br>

The database relationship is:

```text
code
  │
  ▼
links row
  │
  ▼
target_url
```
<br>


For example:

```text
k6lOLa6
   │
   ▼
https://example.com/some/long/path
<br>


```

When the redirect route eventually returns:

```text
HTTP 307
Location: https://example.com/some/long/path

```

that destination ultimately comes from this column.

----------

# 9. Why `String(2048)`?

The project chose a bounded string rather than leaving the field unlimited.
<br>


The application expects URLs rather than arbitrary blobs of text.
<br>


The value is therefore represented as:

```text
String(2048)

```

The important point for future-you is not that `2048` is some magical universal URL limit.
<br>


It is simply the application's chosen database constraint for Phase 1.

If this assumption changes later, the schema and validation strategy should be reconsidered together.

----------

# 10. `clicks`

```python
clicks: Mapped[int] = mapped_column(
    Integer,
    default=0,
    server_default="0",
)

```

This is the total click counter.
<br>


Suppose:

```text
Link A
```

has been redirected five times.
<br>


Instead of calculating:

```sql
SELECT COUNT(*)
FROM click_events
WHERE link_id = ...;
```

every time the total is requested, the link stores:

```text
clicks = 5
```

<br>



This is a **denormalized counter**.

The project deliberately stores both:

```text
links.clicks
```

and:

```text
click_events
```

because they serve different purposes.

----------

# 11. Why store both a counter and click rows?

This is one of the most important database design decisions in the project.

## The counter

```text
links.clicks
```

answers:

> How many clicks has this link received?

Very cheaply.

## The event table

```text
click_events
```
answers:

> What individual clicks happened, and when?
<br>


For example:

```text
id | link_id | clicked_at
---+---------+-------------------------
1  | 1       | 2026-09-28 21:09:06+00
2  | 1       | 2026-09-28 21:09:33+00

```

This makes `/stats` more useful than simply returning:

```json
{
  "clicks": 2
}

```

The API can return:

```json
{
  "clicks": 2,
  "recent_clicks": [
    "...21:09:33...",
    "...21:09:06..."
  ]
}

```

The original build explicitly describes `clicks` as the fast denormalized total and `ClickEvent` as the individual event history.

----------

# 12. The consistency problem introduced by the counter

There is an important consequence.

Every redirect performs two logical database changes:

```text
1. increment links.clicks
2. insert click_events row
```

<br>


The implementation does both before:

```python
db.commit()
```
<br>


The relevant code is:

```python
link.clicks += 1
db.add(ClickEvent(link_id=link.id))
db.commit()
```

Therefore both operations are part of the same SQLAlchemy session transaction and are committed together.
<br>


Conceptually:

```text
BEGIN
  │
  ├── UPDATE links
  │
  └── INSERT click_events
  │
COMMIT
```

That matters because the application wants these two representations of the click to stay synchronized.

----------

# 13. `created_at`

```python
created_at: Mapped[datetime] = mapped_column(
    DateTime(timezone=True),
    server_default=func.now(),
)
```

This records when the link was created.
<br>


Two important choices are hidden in this one field:

```text
DateTime(timezone=True)
server_default=func.now()
```

----------

# 14. Timezone-aware timestamps

The application uses:

```text
DateTime(timezone=True)
```

rather than a naive timestamp.
<br>


That means the timestamp includes timezone information.
<br>


The actual observed PostgreSQL output looked like:

```text
2026-09-28 21:06:48.789232+00
```

The `+00` indicates UTC.

**Using timezone-aware timestamps avoids ambiguity when timestamps cross environments or locations.**

----------

# 15. Why PostgreSQL supplies the timestamp

The field uses:

```python
server_default=func.now()
```

rather than having Python explicitly calculate the timestamp.
<br>


That means the database supplies the creation time.
<br>


The reasoning used in the project was:

```text
API container
     │
     │
     ▼
PostgreSQL
     │
     ▼
database decides timestamp
```

The database is therefore the single source of time for database records.
<br>


This avoids making the application container's clock the authority for database timestamps.

**The build notes explicitly call out this distinction: `func.now()` uses the database's clock rather than the container's.**

----------

# 16. `default=0` vs `server_default="0"`

This is an important SQLAlchemy detail.
<br>


The model contains:

```python
default=0,
server_default="0",
```

These are not duplicates.

They operate at different layers.

----------

## Python/SQLAlchemy default

```python
default=0
```

means SQLAlchemy can supply:

```text
0
```

when creating a new ORM object.

----------

## PostgreSQL server default

```python
server_default="0"
```

means PostgreSQL itself defines the column default.
<br>


So if something outside the normal ORM path inserts a row:

```text
raw SQL
psql
migration
another database client
```

PostgreSQL can still provide:

```text
clicks = 0
```

The original implementation intentionally used both.

----------

# 17. The `click_events` table

The second table is:

```python
class ClickEvent(Base):
    __tablename__ = "click_events"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
    )

    link_id: Mapped[int] = mapped_column(
        ForeignKey(
            "links.id",
            ondelete="CASCADE",
        ),
        index=True,
    )

    clicked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
```

This is the event/history table.

----------

# 18. `click_events.id`

Just like `links.id`, this is the internal primary key.
<br>


Each click gets its own unique row.

Example:

```text
id
--
1
2
3
4
```

The ID identifies the event itself.

It does not identify the link.

That is what `link_id` is for.

----------

# 19. `click_events.link_id`

```python
link_id: Mapped[int] = mapped_column(
    ForeignKey("links.id", ondelete="CASCADE"),
    index=True,
)
```
<br>


This is the foreign key.

It connects:

```text
click_events.link_id
```

to:

```text
links.id
```

<br>



For example:

```text
links

id | code
---+-------
1  | k6lOLa6
```

and:

```text
click_events

id | link_id
---+--------
1  | 1
2  | 1
3  | 1
```

means:

```text
link #1
   │
   ├── click #1
   ├── click #2
   └── click #3
```

----------

# 20. Why `link_id` is indexed

The stats query asks for events belonging to a particular link:

```sql
WHERE click_events.link_id = ?
```
<br>


The application then sorts them by:

```text
clicked_at DESC
```

and limits the result to 20.
<br>


The foreign key is therefore indexed:

```python
index=True
```

This matches the application's access pattern.

The important idea is:

> Index the columns you frequently search/filter on, not simply every column because indexes exist.

----------

# 21. `clicked_at`

```python
clicked_at: Mapped[datetime] = mapped_column(
    DateTime(timezone=True),
    server_default=func.now(),
)
```
<br>


Every event gets its own timestamp.

So a single link might have:

```text
Link
  clicks = 4

ClickEvent
  ├── 21:09:06
  ├── 21:09:33
  ├── 21:11:02
  └── 21:14:20
```

The counter gives the total.

The event rows provide the history.

----------

# 22. The relationship in SQLAlchemy

The `Link` model contains:

```python
events: Mapped[list["ClickEvent"]] = relationship(
    back_populates="link",
    cascade="all, delete-orphan",
)
```

The `ClickEvent` model contains:

```python
link: Mapped["Link"] = relationship(
    back_populates="events"
)
```

This gives SQLAlchemy a bidirectional relationship:

```text
Link
  │
  │ events
  ▼
ClickEvent

ClickEvent
  │
  │ link
  ▼
Link
```

The relationship exists at the ORM level.

The foreign key exists at the database level.

Those are related but not identical concepts.

----------

# 23. ORM relationship vs foreign key

This distinction is worth remembering.

### Foreign key

```text
ForeignKey("links.id")
```

is a database constraint.

It says:

> `click_events.link_id` references a real row in `links.id`.

### SQLAlchemy relationship

```text
relationship(...)
```

tells SQLAlchemy how the Python objects relate.
<br>


It allows you to conceptually work with:

```python
link.events
```

and:

```python
event.link
```
<br>


So:

```text
ForeignKey
    ↓
database relationship/integrity

relationship()
    ↓
ORM object relationship
```

The project deliberately uses both.

----------

# 24. Cascade deletion

The implementation uses two forms of cascade behavior.

## ORM level

```python
cascade="all, delete-orphan"
```

## Database level

```python
ondelete="CASCADE"
```

The intention is:

```text
DELETE Link
     │
     ▼
delete associated ClickEvents
```

This prevents orphaned click events.

The build notes describe this as having the rule represented both in SQLAlchemy and at the database level.

----------

# 25. What happens when a link is deleted?

Suppose:

```text
links

id | code
---+--------
1  | k6lOLa6
```

and:

```text
click_events

id | link_id
---+--------
1  | 1
2  | 1
```

Now:

```text
DELETE link #1
```

The intended result is:

```text
links

empty
```

and:

```text
click_events

empty
```

The click events no longer have a meaningful parent, so they are deleted.

----------

# 26. Why not keep click events after deleting a link?

Because Phase 1 treats click events as children of the link.

A click event exists to describe activity on:

```text
that specific short link
```

If the link is intentionally deleted, retaining its events would create orphaned historical data that the current application has no way to associate with a live link.
<br>


The project therefore chose:

```text
Link owns ClickEvents
```

rather than treating click events as independent permanent analytics records.
<br>


That is a design choice, not a universal rule.

**A more advanced analytics system might intentionally preserve historical events even after a link is removed.**

----------

# 27. Creating a link: database lifecycle

When:

```text
POST /links
```

is received, the route:

1.  validates the URL
    
2.  generates a code
    
3.  checks whether the code already exists
    
4.  constructs a `Link`
    
5.  adds it to the SQLAlchemy session
    
6.  commits it
    
7.  refreshes the object
    
<br>


The relevant implementation is:

```python
link = Link(code=code, target_url=target)
db.add(link)
db.commit()
db.refresh(link)
```
<br>


The database lifecycle is therefore:

```text
Python Link object
        │
        ▼
db.add()
        │
        ▼
SQLAlchemy session
        │
        ▼
db.commit()
        │
        ▼
PostgreSQL row
```

----------

# 28. Why `commit()`?

Before `commit()`:

```text
the change is part of the current transaction
```

After:

```text
db.commit()
```

the transaction is committed to PostgreSQL.

This matters because creating a link is a state-changing database operation.

The application wants the row to exist before it proceeds to treat the link as successfully created.

----------

# 29. Why `refresh(link)`?

After committing, the database may have generated values that the Python object did not originally know.
<br>


For example:

```text
id
created_at
```

are database-generated/defaulted values.
<br>


`db.refresh(link)` reloads the row from PostgreSQL so the SQLAlchemy object contains the current database state.
<br>


Conceptually:

```text
Python object
     │
     ▼
INSERT
     │
     ▼
PostgreSQL generates values
     │
     ▼
COMMIT
     │
     ▼
REFRESH
     │
     ▼
Python object now reflects DB row
```

This is why the API can immediately return the populated response.

----------

# 30. Redirect: database lifecycle

When:

```text
GET /{code}
```

is requested, the application first finds the link:

```python
link = db.scalar(
    select(Link).where(Link.code == code)
)
```
<br>


If it doesn't exist:

```text
404
```

Otherwise, after resolving the URL through the cache:

```python
link.clicks += 1
db.add(ClickEvent(link_id=link.id))
db.commit()
```

The database transaction therefore represents one click.

----------

# 31. One click as a database transaction

Conceptually:

```text
BEGIN TRANSACTION
       │
       ├── increment links.clicks
       │
       └── insert click_events row
       │
       ▼
COMMIT
```

After the transaction:

```text
links.clicks
```

and:

```text
COUNT(click_events)
```

should represent the same number of clicks for that link, assuming all click transactions have completed successfully.
<br>


This is one of the reasons the counter and event insertion happen before the same `commit()`.

----------

# 32. Stats query

The stats endpoint first retrieves the link:

```python
link = db.scalar(
    select(Link).where(Link.code == code)
)
```
<br>


Then retrieves recent click timestamps:

```python
recent = db.scalars(
    select(ClickEvent.clicked_at)
    .where(ClickEvent.link_id == link.id)
    .order_by(ClickEvent.clicked_at.desc())
    .limit(20)
).all()
```
<br>


The query means:

```text
Find events
    ↓
belonging to this link
    ↓
newest first
    ↓
only 20
```

This prevents `/stats` from returning an unlimited click history.

----------

# 33. Why the stats endpoint uses two sources

The response combines:

```text
link.clicks
```

with:

```text
recent ClickEvent.clicked_at
```
<br>


So:

```text
                 Link
                  │
          ┌───────┴────────┐
          │                │
       clicks          ClickEvents
          │                │
          ▼                ▼
       total          recent history
```

The endpoint therefore gets:

```text
total = cheap counter
history = event table
```

This is a practical example of denormalization being used intentionally.

----------

# 34. Actual database verification

The database was inspected directly rather than trusting only the API.

The following query was used:

```bash
docker compose exec db psql -U snip -d snip -c \
"SELECT id, code, target_url, clicks, created_at FROM links;"
```

The actual output showed:

```text
id |  code   | target_url | clicks | created_at
---+---------+------------+--------+-------------------------------
1  | k6lOLa6 | ...        | 2      | 2026-09-28 21:06:48.789232+00
```

This confirmed that the link existed in PostgreSQL and that the click counter had been updated.

----------

# 35. Inspecting click events directly

The event table was inspected with:

```bash
docker compose exec db psql -U snip -d snip -c \
"SELECT id, link_id, clicked_at
 FROM click_events
 ORDER BY clicked_at DESC;"
```

The observed data was:

```text
id | link_id | clicked_at
---+---------+-------------------------------
2  | 1       | 2026-09-28 21:09:33.960685+00
1  | 1       | 2026-09-28 21:09:06.546986+00
```

This proved that redirects were creating actual database rows rather than merely incrementing an in-memory counter.

----------

# 36. Delete verification

The project also tested the database side of deletion.

Before deletion:

```text
click_events count
= 2
```

After deleting the parent link, the expected result was:

```text
click_events count
= 0
```
<br>


The build instructions explicitly used this as a test of the cascade behavior.

This is important because merely receiving:

```text
204 No Content

```

from the API does not prove that the database is internally consistent.
<br>


The test therefore checked the actual PostgreSQL state.

----------

# 37. Database and Redis are deliberately different

For a link:

```text
code = k6lOLa6
```

the state is roughly:

```text
PostgreSQL
────────────────────────────
links
id:          1
code:        k6lOLa6
target_url:  ...
clicks:      2
created_at:  ...

click_events
id:          1
link_id:     1
clicked_at:  ...

id:          2
link_id:     1
clicked_at:  ...
```

while Redis contains approximately:

```text
link:k6lOLa6
        │
        ▼
target URL
```
<br>


The distinction is:

```text
PostgreSQL
    ↓
complete application state

Redis
    ↓
cached lookup
```

Redis can disappear without destroying the database.

----------

# 38. What happens if Redis disappears?

Suppose:

```text
Redis
  ↓
destroyed
```
<br>


The PostgreSQL rows remain.
<br>


The next redirect can:

```text
Redis MISS
    │
    ▼
PostgreSQL
    │
    ▼
get target_url
    │
    ▼
populate Redis again
```

This is why Redis is safe to treat as disposable.

**The README explicitly records that Redis is intentionally not persisted because it is a cache, while PostgreSQL uses a named volume for persistent data.**

----------

# 39. What happens if PostgreSQL disappears?

This is fundamentally different.

If PostgreSQL loses its data:

```text
links
click_events
```

are gone.
<br>


Redis might still contain:

```text
link:k6lOLa6
```

temporarily.
<br>


But Redis is not sufficient to reconstruct the application's complete state because it does not contain:

-   internal link IDs
    
-   click-event history
    
-   click counters as authoritative database state
    
-   creation timestamps
    
-   all other database information
<br>

    

Therefore:

```text
Redis loss
    → cache rebuild

PostgreSQL loss
    → data loss

```

That is the core reason PostgreSQL is the source of truth.

----------

# 40. Why the database is persistent in Docker

PostgreSQL is backed by a named volume:

```text
pgdata

```

mounted at:

```text
/var/lib/postgresql/data

```

inside the PostgreSQL container.
<br>


Conceptually:

```text
Docker host
     │
     ▼
pgdata named volume
     │
     ▼
/var/lib/postgresql/data
     │
     ▼
PostgreSQL

```

The database files therefore live in Docker-managed persistent storage rather than only in the writable layer of the container.

This means removing and recreating the PostgreSQL container does not automatically remove the database volume.

----------

# 41. Why Redis doesn't have a volume

Redis is deliberately treated differently.

If Redis restarts:

```text
cache is empty

```

The application can rebuild entries from PostgreSQL.

That is exactly the behavior desired from a cache.

Persisting Redis would add storage complexity without changing the authoritative data model.

----------

# 42. Database constraints in one place

The important database rules are:

```text
links.id
    PRIMARY KEY

links.code
    UNIQUE
    INDEXED

click_events.id
    PRIMARY KEY

click_events.link_id
    FOREIGN KEY → links.id
    INDEXED
    ON DELETE CASCADE
```

And the relationship is:

```text
links 1 ───────── N click_events
```

This is the complete relational structure of Phase 1.

----------

# 43. The complete database lifecycle

A useful way to remember the database design is to follow a link through its entire life.

## Creation

```text
POST /links
    │
    ▼
generate code
    │
    ▼
INSERT links
    │
    ▼
clicks = 0
    │
    ▼
created_at = database time
```

## Redirect

```text
GET /{code}
    │
    ▼
find links row
    │
    ▼
clicks += 1
    │
    ▼
INSERT click_events
    │
    ▼
COMMIT
```

## Statistics

```text
GET /links/{code}/stats
    │
    ├──► links.clicks
    │
    └──► recent click_events
```

## Deletion

```text
DELETE /links/{code}
    │
    ▼
DELETE links row
    │
    ▼
cascade DELETE click_events
```

The Redis cache is updated/invalidate separately around these operations.

----------

# 44. The key design decisions

These are the database decisions worth remembering when you return to the project later.

### 1. PostgreSQL is authoritative

Redis is never treated as the permanent source of truth.

### 2. `code` is unique

A short code must identify exactly one link.

### 3. `code` is indexed

Redirect lookup is one of the application's most important database queries.

### 4. Clicks are represented twice

```text
clicks
    ↓
fast aggregate

click_events
    ↓
event history
```

This is intentional denormalization.

### 5. Click events belong to links

The relationship is one-to-many.

### 6. Deleting a link deletes its events

Both ORM and database cascade behavior are configured.

### 7. Database supplies timestamps

`func.now()` keeps PostgreSQL authoritative for database timestamps.

### 8. `default` and `server_default` solve different problems

One is ORM-side; the other is database-side.

### 9. Phase 1 uses `create_all()`

This keeps the learning project simple.

### 10. Phase 1 does not have migrations

Alembic is the intended future direction if schema evolution becomes part of the project.

----------

# 45. What this database design is NOT

It is useful to explicitly record what was _not_ built.

This is not:

```text
a full analytics database
```
<br>


It does not have:

-   user accounts
    
-   ownership
    
-   authentication
    
-   IP tracking
    
-   user-agent tracking
    
-   geographic analytics
    
-   click deduplication
    
-   partitioned event tables
    
-   archival policies
    
-   migrations
    
-   sophisticated transaction retry logic
    
-   batched click aggregation
    

Those are outside Phase 1.
<br>


The goal was to create a small relational model that is rich enough to exercise:

```text
primary keys
foreign keys
indexes
unique constraints
relationships
cascades
timestamps
transactions
denormalized counters
persistent storage
```

without turning the project into an unnecessarily complicated production system.

----------

# 46. Final mental model

If you forget everything else, remember this:

```text
                    PostgreSQL
                         │
          ┌──────────────┴──────────────┐
          │                             │
          ▼                             ▼
       links                       click_events
   ┌─────────────┐              ┌───────────────┐
   │ id          │◄─────────────│ link_id       │
   │ code        │              │ id            │
   │ target_url  │              │ clicked_at    │
   │ clicks      │              └───────────────┘
   │ created_at  │
   └─────────────┘
          │
          │ 1
          │
          │ N
          ▼
     click history
```
<br>


And the most important conceptual distinction is:

```text
links
  = current state

click_events
  = historical events
```

The counter:

```text
links.clicks
```

is the cheap summary of those events.
<br>


The event table:

```text
click_events
```

is what gives the application actual history.

That is the core of Snip's database design.
