"""Проверка «та ли версия отвечает»: чексумма, alias, smoke-батарея.

    python -m scripts.check_version

Четыре вопроса после подъёма:
  1. файл модели тот же?     sha256 GGUF == versions.json
  2. сервер крутит её?       /v1/models содержит ожидаемую модель
  3. поведение то же?        smoke-батарея зелёная
  4. отвечает выпущенная?    llm_calls.model_version последнего вызова == current
Расхождение llm_calls.model_version с current — инцидент конфигурации.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core import config  # noqa: E402,F401  — загружает .env, как у бэкенда

try:
    import httpx2 as httpx
except ImportError:  # pragma: no cover
    import httpx

VERSIONS_FILE = ROOT / "versions.json"
FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"[{'ok ' if ok else 'FAIL'}] {name} {detail}")
    if not ok:
        FAILURES.append(name)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-battery", action="store_true", help="без smoke-батареи (быстрая проверка)")
    args = parser.parse_args()

    data = json.loads(VERSIONS_FILE.read_text(encoding="utf-8"))
    current = data.get("current", "")
    if not current:
        print("versions.json пуст: выпустите версию (scripts/release.py)")
        raise SystemExit(1)
    release = data["releases"][current]
    print(f"current: {current} ({release.get('model')}, rev {release.get('git_rev')})\n")

    if release.get("model") == "cloud-baseline":
        print("облачная версия: чексумма файла не применима;")
        print("проверка — гейт на выпущенном прогоне и llm_calls.provider = cloud")
    else:
        # 1. чексумма файла
        model_key = release["model"]
        model = data.get("models", {}).get(model_key, {})
        path_str = model.get("path") or os.environ.get("LLM_LOCAL_MODEL_PATH", "")
        path = Path(path_str)
        if not path.exists():
            check("model_file_exists", False, str(path))
        else:
            h = hashlib.sha256()
            with path.open("rb") as f:
                for chunk in iter(lambda: f.read(1024 * 1024), b""):
                    h.update(chunk)
            check("sha256_matches", h.hexdigest() == model.get("sha256"), f"{path.name}")

        # 2. сервер крутит ожидаемую модель
        base_url = os.environ.get("LLM_LOCAL_BASE_URL", "").rstrip("/")
        try:
            with httpx.Client(trust_env=False, timeout=10) as client:
                r = client.get(f"{base_url}/models")
                ids = [m.get("id", "") for m in r.json().get("data", [])]
            check("server_serves_model",
                  any(model_key in i or release["provider"]["model"] in i for i in ids),
                  f"{base_url} → {ids[:4]}")
        except Exception as e:  # noqa: BLE001 — сервер недоступен тоже результат
            check("server_serves_model", False, repr(e)[:80])

    # 3. поведение: smoke-батарея
    if not args.skip_battery:
        from evals.smoke_battery import main_async as battery_main

        battery_failed = asyncio_run_battery()
        check("smoke_battery", battery_failed == 0)

    # 4. ответы подписаны выпущенной версией: последний вызов модели в llm_calls
    from core import db

    last = db.fetchone("select model_version, provider from llm_calls order by id desc limit 1")
    if last is None:
        check("llm_calls_model_version", False, "в llm_calls нет вызовов: прогоните smoke-батарею")
    else:
        check("llm_calls_model_version", last["model_version"] == current,
              f"llm_calls: {last['model_version'] or '(пусто)'} через {last['provider']}, current {current}")

    print()
    if FAILURES:
        print(f"check_version: {len(FAILURES)} падений — версия не подтверждена")
        raise SystemExit(1)
    print(f"check_version: выпущено {current} = крутится {current}")
    raise SystemExit(0)


def asyncio_run_battery() -> int:
    import asyncio

    from evals.smoke_battery import main_async

    return asyncio.run(main_async())


if __name__ == "__main__":
    main()
