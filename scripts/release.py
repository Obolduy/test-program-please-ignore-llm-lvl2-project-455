"""Выпуск версии модельного слоя: тег ставится только на зелёном прогоне.

    python -m scripts.release v0.1 --notes "облачный baseline"
    python -m scripts.release v0.2 --local --notes "Qwen3-8B Q4_K_M, iter 3"

Required check без CI: release проверяет чистый git и зелёный runs/last
на текущем rev — красный прогон блокирует выпуск. Для локальных версий
считается sha256 GGUF (путь — LLM_LOCAL_MODEL_PATH) и запоминается
команда запуска. Локальные версии прогоняют smoke-батарею после подъёма
(check_version.py), облачные — включаются в пул как baseline.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core import config  # noqa: E402,F401  — загружает .env, как у бэкенда
VERSIONS_FILE = ROOT / "versions.json"
RUNS_LAST = ROOT / "runs" / "last"


def git_rev() -> str:
    out = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                         capture_output=True, text=True, cwd=ROOT)
    return out.stdout.strip() or "nogit"


def git_dirty() -> bool:
    out = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True, cwd=ROOT)
    return bool(out.stdout.strip())


def run_is_green_and_current() -> tuple[bool, str]:
    """runs/last зелёный и снят на текущем git rev?

    TODO(выпуск): прочитайте runs/last/manifest.json и runs/last/metrics.json.
    Верните (False, причина), если файлов нет, они не читаются, git_rev
    манифеста не равен git_rev(), в metrics нет outcome_pass или прогон
    не прошёл гейт. Гейт уже написан: evals.gate.check_thresholds(RUNS_LAST)
    возвращает verdict pass или fail и строки с упавшими метриками. Иначе
    (True, строка с outcome_pass). Причина печатается студенту, поэтому
    она называет, что сделать дальше.
    """
    raise NotImplementedError("TODO(выпуск)")


def sha256_of(path: Path) -> str:
    """TODO(выпуск): sha256 файла в hex, чтение кусками, GGUF весит гигабайты."""
    raise NotImplementedError("TODO(выпуск)")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("version", help="например v0.2")
    parser.add_argument("--local", action="store_true", help="локальная модель (GGUF + launch)")
    parser.add_argument("--status", default="candidate", help="baseline | candidate | stable")
    parser.add_argument("--notes", default="")
    args = parser.parse_args()

    ok, why = run_is_green_and_current()
    print(why)
    if not ok:
        print("выпуск блокирован гейтом: тег не ставится")
        raise SystemExit(1)
    if git_dirty():
        print("git dirty: закоммитьте изменения — версия фиксирует rev")
        raise SystemExit(1)

    data = json.loads(VERSIONS_FILE.read_text(encoding="utf-8"))
    release: dict = {
        "date": datetime.now().isoformat(timespec="seconds"),
        "git_rev": git_rev(),
        "run": f"runs/{RUNS_LAST.name}",
        "status": args.status,
        "notes": args.notes,
    }

    if args.local:
        model_path = os.environ.get("LLM_LOCAL_MODEL_PATH", "")
        if not model_path or not Path(model_path).exists():
            print("LLM_LOCAL_MODEL_PATH не задан или файла нет — sha256 не посчитать")
            raise SystemExit(1)
        path = Path(model_path)
        digest = sha256_of(path)
        key = os.environ.get("LLM_LOCAL_MODEL", "local")
        data.setdefault("models", {})[key] = {
            "file": path.name,
            "sha256": digest,
            "bytes": path.stat().st_size,
            "path": str(path),
        }
        release["model"] = key
        release["launch"] = {
            "cmd": "llama-server",
            "args": [],
            "note": "args — флаги стека сверх базовых из scripts/launch_local.py",
        }
        release["provider"] = {"name": "local", "model": os.environ.get("LLM_LOCAL_MODEL", "")}
    else:
        release["model"] = "cloud-baseline"
        release["provider"] = {
            "name": "cloud",
            "model": os.environ.get("LLM_CLOUD_MODEL", ""),
        }

    data["releases"][args.version] = release
    data["current"] = args.version
    VERSIONS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    subprocess.run(["git", "add", str(VERSIONS_FILE.relative_to(ROOT))], cwd=ROOT)
    commit = subprocess.run(
        ["git", "commit", "-m", f"release {args.version}: {args.notes or release['model']}"],
        capture_output=True, text=True, cwd=ROOT,
    )
    if commit.returncode == 0:
        subprocess.run(["git", "tag", args.version], cwd=ROOT)
        print(f"выпущено {args.version}: тег поставлен, current обновлён")
    else:
        print(f"versions.json записан, но коммит не создан: {commit.stderr.strip()[:120]}")
        print(f"тег поставьте вручную: git tag {args.version}")
    raise SystemExit(0)


if __name__ == "__main__":
    main()
