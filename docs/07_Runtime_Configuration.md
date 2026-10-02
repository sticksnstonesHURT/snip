# Configuration

Snip keeps runtime configuration outside the application code and Docker image.

The basic flow is:

```text
.env
  ↓
Docker Compose
  ↓
Environment variables
  ↓
API container
  ↓
app/config.py
```

## 1. `.env` and `.env.example`

### `.env`

Contains the actual local configuration used when running the application.

Example:

```env
POSTGRES_USER=snip
POSTGRES_PASSWORD=snip
POSTGRES_DB=snip
CODE_LENGTH=7
```

`.env` is local configuration and should not be committed to Git, especially when it contains real credentials.
<br>

### `.env.example`

Contains the expected configuration structure without relying on real secrets.

It exists so someone cloning the project knows which variables need to be provided:

```bash
cp .env.example .env
```

Then they can modify `.env` for their environment.

----------

## 2. Application configuration

The API reads its configuration through `app/config.py`.
<br>

| Variable | Purpose |
|----------|---------|
| `DATABASE_URL` | PostgreSQL connection string |
| `REDIS_URL` | Redis connection string |
| `CODE_LENGTH` | Length of generated short codes |


The application does not hardcode these values.

----------

## 3. Database configuration

PostgreSQL itself receives:

```env
POSTGRES_USER
POSTGRES_PASSWORD
POSTGRES_DB
```

These are passed into the PostgreSQL container by Docker Compose.
<br>

The API's `DATABASE_URL` is constructed from the same values:

```text
postgresql+psycopg://USER:PASSWORD@db:5432/DATABASE
```

This keeps the database credentials consistent between the PostgreSQL container and the API.
<br>

The hostname is `db` because `db` is the PostgreSQL service name in Compose.

----------

## 4. Redis configuration

The API uses:

```text
redis://cache:6379/0
```

`cache` is the Redis service name defined in Compose.
<br>

The API therefore connects to Redis through the internal Compose network rather than using `localhost`.

----------

## 5. `CODE_LENGTH`

`CODE_LENGTH` controls how long generated short codes are.

The Compose configuration provides a default:

```yaml
CODE_LENGTH: ${CODE_LENGTH:-7}
```

So if no value is supplied, the application uses:

```text
7
```

This allows the behavior to be changed through configuration without modifying the application code.

----------

## 6. Why configuration is separated

The same application image can be used with different runtime configurations.

For example:

```text
Application image
       │
       ├── local database
       ├── development database
       └── production database
```

The image itself does not need to change just because the database credentials, Redis location, or code length changes.
<br>

This keeps **application code/image** separate from **environment-specific configuration**.
