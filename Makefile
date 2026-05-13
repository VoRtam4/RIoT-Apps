COMPOSE ?= docker compose
ANALYTICITY_FE_PORT ?= 5173
ANALYTICITY_FE_HOST ?= 0.0.0.0
LISSY_ENV ?= ./env/.env
LISSY_HTTP_PORT ?= 8082

.PHONY: help run-analyticity stop-analyticity restart-analyticity logs-analyticity run-lissy stop-lissy restart-lissy logs-lissy status-lissy print-app-urls run-all stop-all restart-all

help:
	@echo "Analyticity:"
	@echo "  make run-analyticity       Start Analyticity backend and frontend"
	@echo "  make stop-analyticity      Stop Analyticity backend and frontend"
	@echo "  make restart-analyticity   Restart Analyticity backend and frontend"
	@echo "  make logs-analyticity      Show Analyticity backend logs"
	@echo ""
	@echo "Lissy:"
	@echo "  make run-lissy             Start Lissy frontend, backend and supporting services"
	@echo "  make stop-lissy            Stop Lissy stack"
	@echo "  make restart-lissy         Restart Lissy stack"
	@echo "  make logs-lissy            Show Lissy logs"
	@echo "  make status-lissy          Show Lissy containers"
	@echo ""
	@echo "All:"
	@echo "  make run-all               Start both applications"
	@echo "  make stop-all              Stop both applications"
	@echo "  make restart-all           Restart both applications"

run-analyticity:
	$(COMPOSE) -f AnalyticityBE/docker-compose.yaml up --build -d
	@npm --prefix AnalyticityFE install
	@if pgrep -f "[v]ite.*--port $(ANALYTICITY_FE_PORT)" >/dev/null 2>&1; then \
		echo "Analyticity frontend already running on http://localhost:$(ANALYTICITY_FE_PORT)/waze-data-analysis/"; \
	else \
		nohup npm --prefix AnalyticityFE run dev -- --host "$(ANALYTICITY_FE_HOST)" --port "$(ANALYTICITY_FE_PORT)" >/tmp/riot-apps-analyticity-fe.log 2>&1 & \
		echo "Analyticity frontend started on http://localhost:$(ANALYTICITY_FE_PORT)/waze-data-analysis/"; \
	fi
	@echo "Analyticity backend is available on http://localhost:8002"

stop-analyticity:
	-$(COMPOSE) -f AnalyticityBE/docker-compose.yaml down
	-pkill -f "[v]ite.*--port $(ANALYTICITY_FE_PORT)"
	-pkill -f "npm --prefix AnalyticityFE run dev"

restart-analyticity: stop-analyticity run-analyticity

logs-analyticity:
	$(COMPOSE) -f AnalyticityBE/docker-compose.yaml logs --tail=100

run-lissy:
	cd Lissy && LISSY_HTTP_PORT="$(LISSY_HTTP_PORT)" $(COMPOSE) --env-file "$(LISSY_ENV)" up lissy-proxy-server lissy-be-processing lissy-fe lissy-be-api lissy-db-postgis lissy-db-stats lissy-db-cache --build -d
	@echo "Lissy frontend: http://localhost:$(LISSY_HTTP_PORT)/lissy"

stop-lissy:
	cd Lissy && $(COMPOSE) --env-file "$(LISSY_ENV)" down

restart-lissy: stop-lissy run-lissy

logs-lissy:
	cd Lissy && $(COMPOSE) --env-file "$(LISSY_ENV)" logs --tail=100

status-lissy:
	cd Lissy && $(COMPOSE) --env-file "$(LISSY_ENV)" ps

print-app-urls:
	@echo ""
	@echo "Application frontends:"
	@echo "  Analyticity: http://localhost:$(ANALYTICITY_FE_PORT)/waze-data-analysis/"
	@echo "  Lissy:       http://localhost:$(LISSY_HTTP_PORT)/lissy"

run-all: run-analyticity run-lissy print-app-urls

stop-all: stop-analyticity stop-lissy

restart-all: stop-all run-all
