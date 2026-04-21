# HyperFrames Integration Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Добавить HyperFrames как нативный video tool Hermes и подготовить контейнер Hermes к локальному видеорендеру.

**Architecture:** Отдельный Python tool вызывает HyperFrames CLI через subprocess, хранит проекты в `$HERMES_HOME/workspace/hyperframes`, а контейнер Hermes собирается из кастомного Dockerfile с `Node 22+`, `Chromium`, `FFmpeg` и установленным `hyperframes`.

**Tech Stack:** Python, Hermes tool registry, Docker Compose, HyperFrames CLI, Node.js 22, Chromium, FFmpeg

---

### Task 1: Add failing tests for the tool contract

**Files:**
- Create: `tests/tools/test_hyperframes_tool.py`
- Create: `tests/test_hyperframes_model_tools.py`

**Step 1: Write the failing tests**

- Проверить выбор локального `node_modules/.bin/hyperframes`
- Проверить отказ при `Node < 22`
- Проверить, что `render` требует `project_dir`
- Проверить регистрацию `hyperframes_video` в `model_tools`

**Step 2: Run test to verify it fails**

Run: `python3 -m pytest -o addopts='' tests/tools/test_hyperframes_tool.py tests/test_hyperframes_model_tools.py -q`

Expected: FAIL, потому что `tools/hyperframes_tool.py` ещё не существует и tool не зарегистрирован.

**Step 3: Commit**

```bash
git add tests/tools/test_hyperframes_tool.py tests/test_hyperframes_model_tools.py
git commit -m "test: add hyperframes tool coverage"
```

### Task 2: Implement the HyperFrames tool

**Files:**
- Create: `tools/hyperframes_tool.py`
- Modify: `model_tools.py`
- Modify: `toolsets.py`

**Step 1: Write minimal implementation**

- Добавить `hyperframes_video` с действиями `doctor/init/compositions/render`
- Сделать проверки `Node.js 22+`, `FFmpeg`, `FFprobe`, `Chromium`, `hyperframes`
- Ограничить `project_dir` рабочей директорией Hermes

**Step 2: Run tests to verify they pass**

Run: `python3 -m pytest -o addopts='' tests/tools/test_hyperframes_tool.py tests/test_hyperframes_model_tools.py -q`

Expected: PASS

**Step 3: Commit**

```bash
git add tools/hyperframes_tool.py model_tools.py toolsets.py
git commit -m "feat: add hyperframes video tool"
```

### Task 3: Build a container image that can render videos

**Files:**
- Create: `Dockerfile.hyperframes`
- Modify: `../docker-compose.yml`

**Step 1: Extend the runtime**

- Базироваться на `nousresearch/hermes-agent:latest`
- Установить `Node.js 22.16.0`, `Chromium`, `FFmpeg`
- Установить `hyperframes` в `/opt/hermes/node_modules`

**Step 2: Wire compose to the custom build**

- Переключить `docker-compose.yml` на локальную сборку
- Прописать env vars для Chromium/HyperFrames
- Увеличить `shm_size` для браузерного рендера

**Step 3: Commit**

```bash
git add Dockerfile.hyperframes ../docker-compose.yml
git commit -m "build: add hyperframes runtime image"
```

### Task 4: Verify behavior with focused checks

**Files:**
- Modify: none

**Step 1: Run Python tests**

Run: `python3 -m pytest -o addopts='' tests/tools/test_hyperframes_tool.py tests/test_hyperframes_model_tools.py -q`

Expected: PASS

**Step 2: Build image**

Run: `docker build -f Dockerfile.hyperframes -t hermes-agent:hyperframes .`

Expected: exit 0, image contains Node 22 and Chromium

**Step 3: Smoke-check runtime**

Run: `docker run --rm hermes-agent:hyperframes sh -lc 'node -v && ffmpeg -version | head -n 1 && chromium --version && /opt/hermes/node_modules/.bin/hyperframes doctor'`

Expected: visible `v22.x`, FFmpeg, Chromium, HyperFrames diagnostics

**Step 4: Commit**

```bash
git add docs/plans/2026-04-21-hyperframes-design.md docs/plans/2026-04-21-hyperframes-implementation.md
git commit -m "docs: document hyperframes integration"
```
