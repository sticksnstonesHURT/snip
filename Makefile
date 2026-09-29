COMPOSE := docker compose

.PHONY: help up down logs ps build rebuild shell db-shell cache-shell health smoke nuke

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
	@echo "→ POST /links"
	@curl -s -X POST http://localhost:8000/links \
		-H 'Content-Type: application/json' \
		-d '{"url":"https://example.com"}' ; echo
	@echo "→ GET /links"
	@curl -s http://localhost:8000/links ; echo
