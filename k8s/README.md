# vLLM в minikube — локальный провайдер для контура

vLLM поднимается в кластере и отдаёт OpenAI-совместимый API на
`http://127.0.0.1:8001/v1` — контуру всё равно, что за ним Kubernetes.
Кластерная ветка работает на Linux (+ WSL2) с GPU NVIDIA; на macOS и на
AMD официальный образ vLLM не запускается — для этих машин замер провайдеров идёт
через llama.cpp / LM Studio / Ollama, этот каталог можно игнорировать.

## Файлы

| файл | что делает |
|---|---|
| `configmap.yaml` | конфигурация vLLM: модель, окно, слоты, tool calls |
| `deployment.yaml` | под vLLM: образ, readiness, GPU-ресурс |
| `service.yaml` | адрес внутри кластера (`http://vllm`) |
| `nvidia-device-plugin.yaml` | объявляет `nvidia.com/gpu` на узле |

## Подъём

```bash
# 1. Preflight: NVIDIA Container Toolkit, чтобы docker видел GPU
sudo nvidia-ctk runtime configure --runtime=docker --set-as-default=true
sudo systemctl restart docker
docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi

# 2. Кластер с GPU
minikube start --driver docker --gpus all

# 3. Device plugin: узел объявляет ресурс nvidia.com/gpu
kubectl apply -f k8s/nvidia-device-plugin.yaml
kubectl describe node minikube | grep -A3 "Allocatable"   # должен быть nvidia.com/gpu: 1

# 4. vLLM
kubectl apply -f k8s/configmap.yaml -f k8s/deployment.yaml -f k8s/service.yaml
kubectl rollout status deployment/vllm
#   первый rollout долгий: под качает веса с Hugging Face;

# 5. Мост в хост-систему — отдельный терминал, держать живым
kubectl port-forward svc/vllm 8001:80

# 6. Проверка
curl -s http://127.0.0.1:8001/v1/models    # {"data":[{"id":"qwen3-4b",...}]}
```

## Подключение к контуру (`.env`)

```bash
LLM_LOCAL_BASE_URL=http://127.0.0.1:8001/v1
LLM_LOCAL_MODEL=qwen3-4b
LLM_LOCAL_API_KEY=local
LLM_LOCAL_STACK=vllm
```

`LLM_LOCAL_API_KEY` непустой: vLLM без `--api-key` ключ не проверяет,
но OpenAI SDK требует непустое значение. `LLM_LOCAL_STACK=vllm` — явно:
авто-детект по порту 8001 не отличит vLLM от llama-server.

## Смена модели или конфига

Правится `configmap.yaml`, затем:

```bash
kubectl apply -f k8s/configmap.yaml && kubectl rollout restart deployment/vllm
```

## Нюансы, которые экономят час

- **OOM у vLLM случается не на старте, а на первых же запросах** — когда
  KV-кэш дорос до заявленных слотов. Лечится уменьшением `max-num-seqs`
  или `gpu-memory-utilization` в `configmap.yaml`.
- **port-forward — единственный мост** хост ↔ кластер: упал процесс —
  «отвисли» все вызовы контура. Проверяйте терминал с port-forward
  раньше логов модели.
- Смена модели не меняет версию сама: `versions.json` и выпуск версии
  рассчитаны на стек с локальным файлом модели (GGUF, sha256); для vLLM
  версия — это пара «образ × конфиг», она живёт в git этого каталога.

## Снести

```bash
minikube delete
```
