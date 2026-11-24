.PHONY: help install setup test infra migrate seed stand api actions pdp guardrail snapshot smoke hooks \
	eval gate smoke-battery scenarios dataset release verify rollback metrics tco reset

help:
	@echo "make install    - зависимости (pip install -r requirements.txt)"
	@echo "make setup      - install + migrate + seed (так готовит базу автопроверка)"
	@echo "make infra      - postgres+pgvector (docker compose)"
	@echo "make migrate    - применить db/*.sql"
	@echo "make seed       - демо-данные + база знаний в Postgres"
	@echo "make stand      - стенд готового: issuer + read-MCP + моки банка + веб-чат (:8100)"
	@echo "make api        - backend + оркестратор (BACKEND_PORT)         [агенты]"
	@echo "make actions    - action-MCP (:8220)                    [контракты]"
	@echo "make pdp        - PDP с политиками Dogwood (:9100)      [права]"
	@echo "make guardrail  - guardrail-сервис (:8300)              [guardrail]"
	@echo "make snapshot   - пересчитать векторы базы знаний (сервер эмбеддингов из EMBED_*)"
	@echo "make smoke      - проверки по шагам (python tests/smoke.py)"
	@echo "make hooks      - подключить .githooks (pre-push гоняет гейт)"
	@echo "make eval       - прогон eval-батареи в runs/"
	@echo "make gate       - прогон батареи и сверка с thresholds.json"
	@echo "make smoke-battery - короткая батарея для проверки выпуска"
	@echo "make scenarios  - диалоговые сценарии на живой LLM      [сценарии]"
	@echo "make dataset    - датасет дообучения: сбор + синтетика (стенд и api подняты)"
	@echo "make release    - выпуск: python -m scripts.release v0.X --notes ТЕКСТ"
	@echo "make verify     - та ли версия отвечает (check_version)"
	@echo "make rollback   - откат: python -m scripts.rollback v0.X"
	@echo "make metrics    - отчёт по llm_calls"
	@echo "make tco        - стоимость диалога и break-even"
	@echo "make reset      - вернуть демо-данные моков в исходное"

install:
	python -m pip install -r requirements.txt

setup: install migrate seed

test: smoke

infra:
	docker compose -p bank-chat-ai up -d

migrate:
	python -m core.db migrate

seed:
	python -m core.seed

stand:
	python stand.py

api:
	uvicorn app.main:app --host 127.0.0.1 --port $$(python -c 'from core import config; print(config.BACKEND_PORT)') --log-level warning

actions:
	uvicorn actions.server:app --host 127.0.0.1 --port $$(python -c 'from core import config; print(config.ACTIONS_PORT)') --log-level warning

pdp:
	python -m pdp.service

guardrail:
	python -m guardrail.service

snapshot:
	python -m kb.build_snapshot

smoke:
	python tests/smoke.py

hooks:
	git config core.hooksPath .githooks

eval:
	python -m evals.run_evals

gate:
	python -m evals.run_evals --gate

smoke-battery:
	python -m evals.smoke_battery

scenarios:
	python scripts/run_scenarios.py

dataset:
	python -m scripts.build_dataset --all

release:
	python -m scripts.release

verify:
	python -m scripts.check_version

rollback:
	python -m scripts.rollback

metrics:
	python -m scripts.metrics_report

tco:
	python -m scripts.tco

reset:
	curl -s -X POST http://127.0.0.1:8100/mock/admin/reset
