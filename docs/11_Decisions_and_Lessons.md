# Decisions & Lessons

This section records the reasoning behind the project choices and the lessons that came from building Snip.

It intentionally avoids repeating implementation details already covered in the other documentation.

## 1. Build a real application instead of isolated Docker exercises

The project was intentionally built around a small but complete application rather than separate Docker experiments.

This gave each Docker concept a reason to exist:

```text
Application
    ↓
needs database
    ↓
needs cache
    ↓
needs multiple containers
    ↓
needs networking
    ↓
needs persistent storage
    ↓
needs orchestration
```

### Lesson

Docker concepts are easier to understand when they solve an actual application problem rather than being learned as isolated commands.

----------

## 2. Keep the application small, but make the infrastructure realistic

The application itself is deliberately simple.

The infrastructure is where most of the learning happens.

This was a useful balance: the application is small enough to understand completely, while still requiring:

-   PostgreSQL
    
-   Redis
    
-   Docker networking
    
-   persistent storage
    
-   healthchecks
    
-   configuration
    
-   multi-stage builds
    
-   Compose
    

### Lesson

A learning project doesn't need complicated business logic to be technically valuable.

----------

## 3. Use Redis as a cache, not as another database

Redis was introduced specifically to create a meaningful cache layer.

PostgreSQL remains authoritative.

That makes cache failure recoverable rather than catastrophic.

This also made cache invalidation a real design problem instead of just learning how to run Redis.

### Lesson

Adding infrastructure is more useful when its role is clearly defined.

----------

## 4. Keep Phase 1 intentionally imperfect

The project deliberately stops before solving every production concern.

For example:

-   No authentication
    
-   No Alembic migrations
    
-   Click writes still involve PostgreSQL on redirects
    
-   No production deployment configuration
    

These aren't necessarily mistakes. They define the boundary of the learning phase.

### Lesson

Don't prematurely solve production-scale problems in a project whose purpose is learning the fundamentals.

----------

## 5. Prefer understanding over abstraction

The project could have introduced more tooling, libraries, and automation.

Instead, the stack was kept relatively direct so that the relationship between:

```text
FastAPI
PostgreSQL
Redis
Docker
Compose
```

remained visible.

### Lesson

For a learning project, fewer abstractions can be more valuable than a more sophisticated stack.

----------

## 6. Document the reasoning, not just the result

The most useful documentation isn't:

> "This file contains a Redis connection."

It's:

> "Redis exists because the redirect path is a good candidate for caching, while PostgreSQL remains the source of truth."

The distinction matters because the first explains **what exists**; the second explains **why it exists**.

### Final lesson

The repository should be understandable without remembering the original build process.

The documentation therefore records not only **what was built**, but the reasoning that led to the design.
