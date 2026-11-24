"""Запуск локального сервера по versions.json: конфигурация как код.

    python -m scripts.launch_local           # текущая версия (releases[current])
    python -m scripts.launch_local v0.1      # конкретная версия

Берёт путь GGUF из models, контекст и порт из .env (LLM_LOCAL_CTX,
LLM_LOCAL_BASE_URL) и дописывает к llama-server флаги вашего стека из
releases[<версия>].launch.args; для LM Studio / Ollama печатает подсказку; для vLLM печатает путь к k8s/README.md — сервер
поднимается в Kubernetes, не из этого скрипта. Ждёт /v1/models и зовёт
check_version: подъём без подтверждения версии трафик не получает.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    import httpx2 as httpx
except ImportError:  # pragma: no cover
    import httpx

VERSIONS_FILE = ROOT / "versions.json"


def wait_models(base_url: str, timeout_s: int = 120) -> bool:
    """Ждать /v1/models: сервер считается поднятым, когда отвечает."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            with httpx.Client(trust_env=False, timeout=3) as client:
                r = client.get(f"{base_url.rstrip('/')}/models")
            if r.status_code == 200:
                return True
        except Exception:  # noqa: BLE001 — сервер ещё не поднялся
            pass
        time.sleep(2)
    return False


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("version", nargs="?", default=None)
    parser.add_argument("--check", action="store_true", help="после подъёма запустить check_version")
    args = parser.parse_args()

    data = json.loads(VERSIONS_FILE.read_text(encoding="utf-8"))
    version = args.version or data.get("current", "")
    release = data.get("releases", {}).get(version)
    if not release:
        print(f"версия {version or '(пусто)'} не найдена в versions.json")
        raise SystemExit(1)
    if release.get("model") == "cloud-baseline":
        print(f"{version} — облачная: запуск локального сервера не требуется")
        raise SystemExit(0)

    stack = os.environ.get("LLM_LOCAL_STACK", "llama.cpp")
    if stack.lower() == "vllm":
        print("launch (vllm): сервер поднимается в Kubernetes — не из этого скрипта")
        print("  подъём: k8s/README.md (minikube, манифесты в k8s/)")
        print("  после подъёма держите живым во втором терминале:")
        print("    kubectl port-forward svc/vllm 8001:80")
        print("  .env: LLM_LOCAL_BASE_URL=http://127.0.0.1:8001/v1, LLM_LOCAL_STACK=vllm")
        raise SystemExit(0)

    model = data["models"][release["model"]]
    path = model.get("path") or os.environ.get("LLM_LOCAL_MODEL_PATH", "")
    if not path or not Path(path).exists():
        print(f"GGUF не найден: {path} — проверьте versions.json / LLM_LOCAL_MODEL_PATH")
        raise SystemExit(1)

    base_url = os.environ.get("LLM_LOCAL_BASE_URL", "http://127.0.0.1:8081/v1")
    if stack == "llama.cpp":
        # контекст и порт — под машину: LLM_LOCAL_CTX, порт из LLM_LOCAL_BASE_URL
        port = base_url.rsplit(":", 1)[-1].split("/")[0] or "8081"
        if not port.isdigit():
            port = "8081"
        cmd = [
            "llama-server",
            "-m", path,
            "-c", os.environ.get("LLM_LOCAL_CTX", "16384"),
            "-ngl", "99", "-fa", "on",
            "--host", "127.0.0.1", "--port", port,
            "--parallel", "1", "--jinja",
            "--alias", release["model"],
            *release.get("launch", {}).get("args", []),
        ]
        print("launch (llama.cpp):", " ".join(cmd))
        proc = subprocess.Popen(cmd)
    elif stack == "ollama":
        print("launch (ollama): создайте Modelfile FROM " + path)
        print("  ollama create <имя> -f Modelfile && ollama serve")
        raise SystemExit(0)
    else:  # lmstudio
        print("launch (lmstudio): загрузите модель в LM Studio и запустите сервер;")
        print(f"  файл: {path}")
        raise SystemExit(0)

    print(f"ждём {base_url}/models …")
    if not wait_models(base_url):
        print("сервер не поднялся за таймаут")
        proc.terminate()
        raise SystemExit(1)
    print(f"сервер поднялся (pid {proc.pid})")

    if args.check:
        from scripts.check_version import main as check_main

        sys.argv = ["check_version"]
        check_main()


if __name__ == "__main__":
    main()
