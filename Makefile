COMPOSE := docker compose

.PHONY: help up down logs ps build rebuild shell db-shell cache-shell health smoke nuke migrate migration check key

help:
	@echo "up          start the stack (detached)"
	@echo "down        stop the stack (keeps data)"
	@echo "nuke        stop + DELETE volumes (wipes DB)"
	@echo "build       build the api image"
	@echo "rebuild     build with no cache"
	@echo "ps          show container status"
	@echo "logs        follow api logs"
	@echo "shell       bash inside the api container"
	@echo "db-shell    psql inside postgres"
	@echo "cache-shell redis-cli inside redis"
	@echo "health      hit the /health endpoint"
	@echo "smoke       create a link and list them"
	@echo "migrate     apply alembic migrations"
	@echo "migration   generate a migration (make migration m=\"msg\")"
	@echo "check       verify models vs DB migrations"
	@echo "key         mint an API key (make key n=\"laptop\")"

up:
	$(COMPOSE) up -d

down:
	$(COMPOSE) down

nuke:
	$(COMPOSE) down -v

build:
	$(COMPOSE) build

rebuild:
	$(COMPOSE) build --no-cache

ps:
	$(COMPOSE) ps

logs:
	$(COMPOSE) logs -f api

shell:
	$(COMPOSE) exec api bash

db-shell:
	$(COMPOSE) exec db psql -U $${POSTGRES_USER:-snip} -d $${POSTGRES_DB:-snip}

cache-shell:
	$(COMPOSE) exec cache redis-cli

health:
	@curl -s http://localhost:8000/health ; echo

smoke:
	@echo "→ POST /links (auth required)"
	@curl -s -X POST http://localhost:8000/links \
		-H 'Content-Type: application/json' \
		-H "X-API-Key: $${SNIP_API_KEY:?set SNIP_API_KEY first}" \
		-d '{"url":"https://example.com"}' ; echo
	@echo "→ GET /links"
	@curl -s http://localhost:8000/links ; echo

migrate:
	$(COMPOSE) run --rm api alembic upgrade head

migration:
	$(COMPOSE) run --rm -v "$$PWD":/app api alembic revision --autogenerate -m "$(m)"

check:
	$(COMPOSE) run --rm api alembic check

key:
	$(COMPOSE) run --rm api python -m app.createkey "$(n)"
