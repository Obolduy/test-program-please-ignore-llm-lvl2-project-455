# Таблица версий (выпуск)

Версия = git-тег = запись в versions.json; у каждой — прогон-доказательство.

| версия | состав | метрики (run) | статус |
|---|---|---|---|
| v0.1 | облачный baseline | TODO: outcome/schema/refusal из runs/… | baseline |
| v0.2 | локаль + адаптация | TODO | candidate |

Правила: тег ставит scripts/release.py только на зелёном runs/last;
откат — scripts/rollback.py + smoke-батарея («откат — тоже прогон»).
