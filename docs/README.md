# bank-chat-ai

Это шаблон проекта «AI-чат банковского приложения на локальной модели». Контур банка готов, ваша работа это модельный слой в _inference/_ и всё, что вокруг него. Задание каждого шага лежит в материалах проекта на Хекслете.

## Быстрый старт

```bash
python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env            # заполнить провайдеров модели
make infra && make migrate && make seed
make stand                      # терминал 1: стенд :8100
make pdp                        # терминал 2
make guardrail                  # терминал 3
make actions                    # терминал 4
make api                        # терминал 5: backend на BACKEND_PORT
python tests/smoke.py 0         # контур жив
```

## Где ваше

Места для вашего кода помечены `TODO(…)` с именем этапа.

```bash
grep -rn "TODO(" inference/ scripts/ evals/
```

| Метка | Что пишете | Проверка |
| --- | --- | --- |
| `TODO(провайдеры)` | _inference/providers.py_ | `python tests/smoke.py 6` |
| eval-батарея, без метки | сценарии в _evals/scenarios/_, _evals/thresholds.json_ | `make gate` |
| `TODO(guided)` | _inference/guided.py_ и промпты | `python tests/smoke.py 8` |
| `TODO(датасет)` | _scripts/build_dataset.py_, _data/finetune/_ | `python tests/smoke.py 13` |
| `TODO(выпуск)` | _scripts/release.py_, _versions.json_ | `make verify` |
| `TODO(малые модели)` | _inference/router.py_, _inference/anonymizer.py_, _evals/pii_cases.json_ | `python tests/smoke.py 10` |
| `TODO(гибрид)` | _inference/degrade.py_ | `python tests/smoke.py 11` |
| `TODO(экономика)` | _scripts/tco.py_, _scripts/metrics_report.py_, _docs/economics.md_ | `python tests/smoke.py 12` |

Контур (_stand.py_, _core/_, _services/_, _actions/_, _app/_), раннеры, каркас батареи, гейт, хук и CI готовы. Их правка нужна только при явной причине.

## Группы smoke

Группы 0–5 проверяют контур, 6 провайдеры, 7 батарею, 8 guided decoding, 9 выпуск, 10 малые модели, 11 гибрид и деградацию, 12 экономику, 13 датасет дообучения. Группа становится зелёной, когда готов код её шага, до этого её проверки красные.
