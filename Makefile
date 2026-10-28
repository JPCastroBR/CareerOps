.PHONY: up down logs open status

up: ## Sobe o stack e abre o dashboard no navegador
	docker compose up -d
	@sleep 3
	@xdg-open http://localhost:8080 2>/dev/null || open http://localhost:8080 2>/dev/null || true

down:
	docker compose down

logs:
	docker compose logs -f

open:
	xdg-open http://localhost:8080 2>/dev/null || open http://localhost:8080 2>/dev/null || true

status:
	docker compose ps
