"""Замеры инференса: TTFT, токены/с, память — на формулировках батареи.

    python -m bench.bench --label local-q4 --clients 1 --turns 2
    python -m bench.bench --label cloud --cloud --clients 1 --turns 1

Формулировки берутся из bench/prompts.json — те же сообщения, что и в
eval-батарее: замеры и качество считаются на одинаковых входах, диффы
прогонов сравнимы. Результат пишется в runs/bench/<label>.json и
markdown-таблицей в stdout — строка для docs/benchmarks.md.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    import httpx2 as httpx  # noqa: F401 — единый клиентский стек контура
except ImportError:  # pragma: no cover
    import httpx

import psutil
from dotenv import load_dotenv
from openai import AsyncOpenAI

load_dotenv(ROOT / ".env")

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout.reconfigure(encoding="utf-8")  # консоль Windows: «ток/с» в cp1251 не печатаются

GEN_TEMPERATURE = 0.1
GEN_MAX_TOKENS = 512

SERVER_PROC_NAMES = {"llama-server", "llama-server.exe", "ollama", "ollama.exe",
                     "LM Studio", "LM Studio.exe", "llama.cpp", "vllm"}

BENCH_DIR = ROOT / "runs" / "bench"


def _provider_config(cloud: bool) -> tuple[str, str, str, str]:
    """(base_url, api_key, model, provider_name) из .env."""
    if cloud:
        return (
            os.environ.get("LLM_CLOUD_BASE_URL", ""),
            os.environ.get("LLM_CLOUD_API_KEY", "none"),
            os.environ.get("LLM_CLOUD_MODEL", ""),
            "cloud",
        )
    return (
        os.environ.get("LLM_LOCAL_BASE_URL", os.environ.get("LLM_BASE_URL", "")),
        os.environ.get("LLM_LOCAL_API_KEY", "none"),
        os.environ.get("LLM_LOCAL_MODEL", os.environ.get("LLM_MODEL", "")),
        "local",
    )


def fmt(x: float | None, nd=2) -> str:
    return f"{x:.{nd}f}" if x is not None else "—"


def percentile(values: list[float], p: int) -> float:
    ordered = sorted(values)
    idx = int(p / 100 * (len(ordered) - 1))
    return ordered[idx]


async def measure_request(client: AsyncOpenAI, model: str, messages: list[dict]) -> dict:
    t0 = time.perf_counter()
    ttft_s = None
    chunks = 0
    usage_tokens = None
    stream = await client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=GEN_TEMPERATURE,
        max_tokens=GEN_MAX_TOKENS,
        stream=True,
        stream_options={"include_usage": True},
    )
    async for chunk in stream:
        if chunk.usage:
            usage_tokens = chunk.usage.completion_tokens
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta
        # reasoning-модель сначала стримит рассуждение в reasoning_content
        text = delta.content or getattr(delta, "reasoning_content", None)
        if not text:
            continue
        if ttft_s is None:
            ttft_s = time.perf_counter() - t0
        chunks += 1
    t_end = time.perf_counter()
    # в куске потока бывает несколько токенов: точное число даёт usage,
    # без него остаётся оценка по кускам
    out_tokens = usage_tokens or chunks
    if out_tokens == 0 or ttft_s is None:
        return {"ttft_s": ttft_s, "tok_s": None, "out_tokens": out_tokens}
    return {"ttft_s": ttft_s, "tok_s": out_tokens / (t_end - t0), "out_tokens": out_tokens}


def _num_gb(value) -> float | None:
    """Число из значения утилиты: байты, МБ или ГБ → ГБ."""
    if isinstance(value, (int, float)):
        num = float(value)
        # число без единицы: rocm-smi отдаёт байты, МБ-значения на порядки меньше
        return num / 1024 ** 3 if num > 10 ** 7 else num / 1024
    m = re.match(r"^([\d.]+)\s*(mib|mb|gb|b)?", str(value).strip().lower())
    if not m:
        return None
    num, unit = float(m.group(1)), m.group(2) or ""
    if unit in ("", "b"):
        return num / 1024 ** 3
    if unit in ("mib", "mb"):
        return num / 1024
    return num


def _vram_json_gb(out: str) -> float | None:
    """VRAM из JSON amd-smi/rocm-smi: первое значение вида «used … vram/memory»."""
    data = json.loads(out)

    def walk(node):
        if isinstance(node, dict):
            for key, val in node.items():
                kl = str(key).lower()
                if "used" in kl and ("vram" in kl or "memory" in kl):
                    num = _num_gb(val)
                    if num is not None:
                        return num
                num = walk(val)
                if num is not None:
                    return num
        elif isinstance(node, list):
            for item in node:
                num = walk(item)
                if num is not None:
                    return num
        return None

    return walk(data)


# VRAM-проба: первый успешный ответ определяет вендора (NVIDIA / AMD ROCm)
VRAM_PROBES = [
    (["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
     lambda out: int(out.strip().splitlines()[0]) / 1024),
    (["rocm-smi", "--showmeminfo", "vram", "--json"], _vram_json_gb),
    (["amd-smi", "metric", "--vram-used", "--json"], _vram_json_gb),
]


def _run_probe(cmd: list[str], parse) -> float | None:
    """Любая неудача утилиты (нет команды, таймаут, незнакомый формат) — None."""
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        if out.returncode != 0 or not out.stdout.strip():
            return None
        return parse(out.stdout)
    except (OSError, subprocess.TimeoutExpired, ValueError, TypeError,
            KeyError, IndexError):
        return None


def probe_memory() -> dict:
    """RAM серверов инференса из psutil; VRAM из GPU-утилиты, если есть."""
    ram_gb = 0.0
    for proc in psutil.process_iter(["name", "memory_info"]):
        try:
            if proc.info["name"] in SERVER_PROC_NAMES:
                ram_gb += proc.info["memory_info"].rss / (1024 ** 3)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    vram_gb = None
    for cmd, parse in VRAM_PROBES:
        vram_gb = _run_probe(cmd, parse)
        if vram_gb is not None:
            break
    return {"ram_gb": ram_gb, "vram_gb": vram_gb}


def load_suite() -> tuple[str, list[str]]:
    suite = json.loads((ROOT / "bench" / "prompts.json").read_text(encoding="utf-8"))
    return suite["system"], suite["prompts"]


def build_messages(system: str, prompt: str) -> list[dict]:
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": prompt},
    ]


@dataclass
class Record:
    client: int
    turn: int
    prompt: str = ""
    ttft_s: float | None = None
    tok_s: float | None = None
    out_tokens: int = 0


@dataclass
class Result:
    records: list[Record] = field(default_factory=list)
    memory: list[dict] = field(default_factory=list)


async def run_client(client: AsyncOpenAI, model: str, system: str, prompts: list[str],
                     client_id: int, turns: int, result: Result) -> None:
    for turn in range(1, turns + 1):
        prompt = prompts[(client_id + turn - 1) % len(prompts)]
        rec = Record(client=client_id, turn=turn, prompt=prompt)
        m = await measure_request(client, model, build_messages(system, prompt))
        rec.ttft_s, rec.tok_s, rec.out_tokens = m["ttft_s"], m["tok_s"], m["out_tokens"]
        result.records.append(rec)
        state = "ok" if rec.tok_s else "FAIL"
        print(f"  клиент {client_id} ход {turn}: ttft={fmt(rec.ttft_s, 3)}s  {fmt(rec.tok_s)} ток/с  ({state})")


async def main_async(args: argparse.Namespace) -> int:
    base_url, api_key, model, provider_name = _provider_config(args.cloud)
    if not base_url or not model:
        print("нет провайдера в .env: задайте LLM_LOCAL_* (или LLM_CLOUD_* с --cloud)")
        return 1

    system, prompts = load_suite()
    client = AsyncOpenAI(
        base_url=base_url, api_key=api_key or "none", timeout=300,
        http_client=httpx.AsyncClient(trust_env=False, timeout=300),
    )
    result = Result()

    print(f"endpoint: {base_url}   model: {model}   клиентов: {args.clients}   ходов: {args.turns}")
    if args.warmup:
        print("прогрев (в метрики не входит)…")
        await measure_request(client, model, build_messages(system, prompts[0]))

    result.memory.append(probe_memory())
    print("прогон:")
    t0 = time.perf_counter()
    await asyncio.gather(*(run_client(client, model, system, prompts, i, args.turns, result)
                           for i in range(1, args.clients + 1)))
    wall = time.perf_counter() - t0
    result.memory.append(probe_memory())

    ttfts = [r.ttft_s for r in result.records if r.ttft_s is not None]
    tokss = [r.tok_s for r in result.records if r.tok_s is not None]
    ram = max((m.get("ram_gb") or 0) for m in result.memory)
    vram = max((m.get("vram_gb") or 0) for m in result.memory)

    artifact = {
        "label": args.label,
        "provider": provider_name,
        "endpoint": base_url,
        "model": model,
        "date": datetime.now().isoformat(timespec="seconds"),
        "clients": args.clients,
        "turns": args.turns,
        "ttft_p50_s": percentile(ttfts, 50) if ttfts else None,
        "ttft_p95_s": percentile(ttfts, 95) if ttfts else None,
        "tok_s_avg": sum(tokss) / len(tokss) if tokss else None,
        "wall_s": wall,
        "ram_gb": ram or None,
        "vram_gb": vram,
        "records": [vars(r) for r in result.records],
    }
    BENCH_DIR.mkdir(parents=True, exist_ok=True)
    out_file = BENCH_DIR / f"{args.label}.json"
    out_file.write_text(json.dumps(artifact, ensure_ascii=False, indent=2), encoding="utf-8")

    print()
    print(f"label: {args.label} → {out_file.relative_to(ROOT)}")
    print("| метрика | значение |")
    print("|---|---|")
    print(f"| провайдер | {provider_name} ({model}) |")
    print(f"| клиентов × ходов | {args.clients} × {args.turns} |")
    print(f"| TTFT p50, с | {fmt(artifact['ttft_p50_s'], 3)} |")
    print(f"| TTFT p95, с | {fmt(artifact['ttft_p95_s'], 3)} |")
    print(f"| токенов/с (среднее по запросам) | {fmt(artifact['tok_s_avg'])} |")
    print(f"| стенка-время прогона, с | {fmt(wall)} |")
    print(f"| память RAM сервера, ГБ | {fmt(ram or None)} |")
    print(f"| память VRAM, ГБ | {fmt(vram or None)} |")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--clients", type=int, default=1)
    parser.add_argument("--turns", type=int, default=2)
    parser.add_argument("--warmup", type=int, default=1)
    parser.add_argument("--label", default="run")
    parser.add_argument("--cloud", action="store_true", help="мерить облачного провайдера из LLM_CLOUD_*")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main_async(args)))


if __name__ == "__main__":
    main()
