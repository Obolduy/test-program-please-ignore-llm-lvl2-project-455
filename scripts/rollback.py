"""Откат версии: current → прежняя, перезапуск, подтверждение прогоном.

    python -m scripts.rollback v0.1

«Откат — тоже прогон»: rollback переключает current в versions.json и
запускает smoke-батарею; зелёный прогон = откат подтверждён. Красный —
возврату нет доверия: разбирайтесь, почему прежняя версия не зелёная.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VERSIONS_FILE = ROOT / "versions.json"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("version", help="версия, к которой откатываемся")
    args = parser.parse_args()

    data = json.loads(VERSIONS_FILE.read_text(encoding="utf-8"))
    target = data.get("releases", {}).get(args.version)
    if target is None:
        print(f"версия {args.version} не найдена; есть: {', '.join(data.get('releases', {}))}")
        raise SystemExit(1)
    previous = data.get("current", "")
    if args.version == previous:
        print(f"{args.version} уже current")
        raise SystemExit(0)

    data["current"] = args.version
    data["releases"][args.version]["status"] = "stable"
    data.setdefault("_rollbacks", []).append({
        "date": datetime.now().isoformat(timespec="seconds"),
        "from": previous,
        "to": args.version,
    })
    VERSIONS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"current: {previous} → {args.version}")

    if target.get("model") != "cloud-baseline":
        print("перезапустите локальный сервер: python -m scripts.launch_local --check")

    print("подтверждение прогоном: smoke-батарея…")
    import asyncio

    from evals.smoke_battery import main_async as battery_main

    failed = asyncio.run(battery_main())
    if failed == 0:
        print(f"откат на {args.version} подтверждён прогоном")
        raise SystemExit(0)
    print("smoke-батарея красная: откату нет доверия — разберитесь, почему прежняя версия не зелёная")
    raise SystemExit(1)


if __name__ == "__main__":
    main()
