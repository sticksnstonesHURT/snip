# Compose Migration Orchestration

## 1. Purpose

Docker Compose orchestrates the startup of Snip's services.

- In Phase 3, database migrations became a separate startup step. 
- Instead of allowing the API to create database tables during application startup, Snip uses a dedicated `migrate` service to apply schema changes before the API starts.


This document explains:

-   How the Compose services depend on one another.
    
-   Why PostgreSQL health checks matter.
    
-   How the one-shot migration service works.
    
-   How Compose controls API startup.
    
-   How to verify and troubleshoot the startup sequence.
    

---------------------------

## 2. Services Involved

Three services participate directly in the migration startup sequence.


| Service   | Responsibility                                                     |
| --------- | ------------------------------------------------------------------ |
| `db`      | Runs PostgreSQL and stores persistent application data.            |
| `migrate` | Runs Alembic migrations and exits after completion.                |
| `api`     | Runs the Snip API after successful migration completion.           |


**The migration service uses the same application image as the API. This keeps the migration environment aligned with the application code and dependencies.**

**The `migrate` service is not another API instance. It is a short-lived process that performs a task and exits.**

-------


## 3. Startup Dependency Graph

The intended startup order is:

```
                PostgreSQL (db)
                       |
                       v
               Database healthy
                       |
                       v
                Migration service
                 alembic upgrade head
                       |
                       v
                Migration succeeds?
                  /          \
                No            Yes
                |              |
                v              v
          API stays off     API starts
```
<br>

This sequence separates three distinct states:

1.  **Container started:** The container process has been launched.
    
2.  **Database healthy:** PostgreSQL has passed its configured health check.
    
3.  **Schema ready:** Pending migrations have completed successfully.
    

A running database container does not automatically mean the database is ready to accept connections. Likewise, a healthy database does not automatically mean the application schema is current.

Snip handles these dependencies as separate steps.


---------

## 4. PostgreSQL Health Checks

Compose can use a health check to determine whether PostgreSQL is ready, rather than relying only on whether its container is running.

**In Snip, the migration service depends on the database becoming healthy before it runs.**

A health check commonly uses PostgreSQL's `pg_isready` utility. 
The exact command, interval, timeout, retry count, and startup grace period should match the values in the project's actual `docker-compose.yml`.

<br>

An illustrative health check is:

```docker
healthcheck:
  test: ["CMD-SHELL", "pg_isready -U $${POSTGRES_USER} -d $${POSTGRES_DB}"]
  interval: 5s
  timeout: 3s
  retries: 5
```

This example illustrates the mechanism; not a claim that these are Snip's exact configured values.

The doubled dollar signs defer variable expansion to the container shell instead of having Compose expand the variables prematurely.

A successful `pg_isready` check indicates that PostgreSQL is accepting connections. It does not verify that application migrations have been applied.

----------

## 5. The One-Shot Migration Service

The `migrate` service runs:
```
alembic upgrade head
```

<br>

Alembic applies the pending migrations required to bring the database to the latest revision available to the application.

The service then exits.

<br>

Its exit status is important:

-   **Exit code** `**0**`**:** The migration command completed successfully.
    
-   **Non-zero exit code:** The migration command failed.

<br>    

**Compose can use the `service_completed_successfully` dependency condition to require the migration service to exit successfully before starting the API.**

An illustrative dependency configuration is:

```docker
services:
  migrate:
    image: project_backend:v1
    command: ["alembic", "upgrade", "head"]
    depends_on:
      db:
        condition: service_healthy

  api:
    image: project_backend:v1
    depends_on:
      db:
        condition: service_healthy
      migrate:
        condition: service_completed_successfully
```

This is a conceptual example, not a replacement for Snip's actual Compose file. 
The image name, environment variables, volumes, build configuration, and other service settings must match the repository.

**The key configuration is the dependency condition between `api` and `migrate`. It expresses a completion requirement rather than merely requiring the migration container to have started.**

----

## 6. Why the API and Migration Service Share an Image

**The API and migration service use the same application image.**

This matters because migration scripts depend on the application's code, configuration, and installed dependencies.

<br>

Using the same image reduces the chance of a version mismatch, such as:

-   The migration service running newer migration scripts than the API expects.
    
-   The API using dependencies that differ from those used by Alembic.
    
-   The migration service being built from a different application revision.
    

The shared image does not, by itself, guarantee that every environment is identical. Environment variables, database configuration, and deployment settings still need to be correct.


---------------

## 7. Failure Behaviour

### PostgreSQL fails its health check

The migration service cannot satisfy its database readiness dependency.

Consequently, the migration step should not proceed as though the database were ready, and the API's migration dependency remains unsatisfied.

<br>

### The migration command fails

The migration service exits with a non-zero status.

With `condition: service_completed_successfully`, Compose does not satisfy the API's dependency on successful migration completion. The API should therefore not start through this dependency chain.

Inspect the migration logs and resolve the underlying issue before retrying.

<br>

### The migration succeeds

The migration service exits successfully.

Compose can then start the API, provided its other dependencies are also satisfied.

<br>

### The API fails after migration succeeds

A successful migration only confirms that the migration command completed. It does not guarantee that the API will run correctly.

The API may still fail because of application errors, invalid runtime configuration, unavailable dependencies, or other issues. Inspect the API logs separately.

<br>


--------------------

## 8. Running and Inspecting the Stack

Snip's recorded cold-start verification used:

```
make down
make up
```

The goal was to test startup from a stopped Compose environment.

<br>

To inspect service status:

```
docker compose ps -a
```

The `-a` flag is useful because the migration service exits after completing its work and may not appear as a running service.
<br>

To inspect migration output:

```
docker compose logs migrate
```
<br>

To inspect API startup:

```
docker compose logs api
```
<br>

To follow logs while services run:

```
docker compose logs -f api
```
<br>

**Use the project's Makefile and Compose configuration as the source of truth for service names and supported commands.**


------------------

## 9. Verification Results

The recorded Phase 3 cold-start test established that:

-   The stack was brought down and started again.
    
-   The migration service completed with exit code `0`.
    
-   The API started after the migration service completed successfully.
    

These results support the intended startup ordering.

They do not establish that every possible failure mode has been tested. A stronger failure-path test would deliberately make a migration fail and confirm that the API remains stopped, then restore the migration and verify that startup succeeds.


--------

## 10. Operational Considerations

### Migrations are deployment operations

Migration scripts should be reviewed and version-controlled. Destructive changes, large table alterations, and data transformations require particular care.

<br>

### Database backups remain necessary

Alembic tracks schema versions; it is not a backup system. A successful migration does not provide a way to recover data that was accidentally deleted or transformed incorrectly.

<br>

### Startup ordering is not the same as runtime recovery

Compose dependency conditions govern service startup. They should not be treated as a complete solution for every runtime failure or later database outage.

<br>

### Multiple API replicas require deployment planning

The migration service separates schema changes from API startup, but expanding the deployment to multiple API instances introduces additional operational considerations. Migrations should be coordinated so that concurrent deployments do not independently run conflicting schema changes.

-----------


## 11. Key Takeaways

-   PostgreSQL health checks establish database readiness.
    
-   The `migrate` service applies pending Alembic migrations and then exits.
    
-   The API depends on successful migration completion, not merely migration container startup.
    
-   The API and migration service share the same image to reduce version drift.
    
-   `docker compose ps -a` helps inspect completed one-shot services.
    
-   Migration failures should be diagnosed before retrying API startup.
    
-   Compose orchestration, schema migration, and database backup are separate responsibilities.
