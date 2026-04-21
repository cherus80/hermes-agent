# HyperFrames Integration Design

## Goal

Добавить в Hermes Agent нативный video tool на базе HyperFrames, чтобы агент мог:

- проверять среду рендера;
- создавать новый HyperFrames-проект;
- редактировать композиции через существующие file tools;
- рендерить итоговое видео в MP4, MOV или WebM;
- выполнять всё это внутри контейнера `hermes-local`, а не только на хосте.

## Recommended Approach

### Option 1: Native Hermes tool

Добавить `tools/hyperframes_tool.py`, зарегистрировать его в `model_tools.py` и `toolsets.py`, а контейнер Hermes расширить до среды с `Node.js 22+`, `Chromium`, `FFmpeg` и установленным `hyperframes`.

Плюсы:

- естественно вписывается в текущую архитектуру Hermes;
- агент может сочетать HyperFrames с `read_file`/`write_file`/`patch`;
- меньше зависимости от внешних MCP/plugin-слоёв;
- легко покрывается тестами.

Минусы:

- нужен кастомный образ вместо `nousresearch/hermes-agent:latest`.

### Option 2: MCP-обёртка поверх внешнего CLI

Вынести HyperFrames в отдельный MCP-сервис и подключать как внешний tool.

Плюсы:

- слабее связка с кодом Hermes;
- можно переиспользовать сервис отдельно.

Минусы:

- выше операционная сложность;
- всё равно нужно собирать среду с Node/Chrome/FFmpeg;
- интеграция с файловым рабочим пространством Hermes хуже.

### Option 3: Terminal-only workflow

Ничего не добавлять в Hermes, а заставлять агента пользоваться только `terminal`.

Плюсы:

- почти нет кода.

Минусы:

- плохой UX;
- больше вероятность неустойчивых вызовов и плохих подсказок модели;
- нет декларативной схемы tool parameters.

## Selected Design

Выбран Option 1.

### Tool contract

Новый tool: `hyperframes_video`

Поддерживаемые действия:

- `doctor`
- `init`
- `compositions`
- `render`

### Workspace model

- проекты живут в `$HERMES_HOME/workspace/hyperframes`;
- tool не позволяет выходить за пределы этой директории;
- созданный проект затем редактируется стандартными file tools Hermes.

### Runtime model

- Hermes запускается из локально собранного образа `hermes-agent:hyperframes`;
- в образе есть `Node.js 22.16.0`, `Chromium`, `FFmpeg`, локально установленный `hyperframes`;
- `docker-compose.yml` переключён на `build`, чтобы новые Python-файлы реально использовались контейнером.

### Error handling

- tool скрывается из доступных, если не выполняются требования среды;
- обработчик возвращает JSON с `success/error/command/stdout/stderr`;
- `doctor` дополнительно возвращает снимок runtime-status для диагностики.

### Testing

- unit tests на выбор бинаря, проверку `Node 22+`, валидацию `render`;
- интеграционный smoke test через `model_tools`, чтобы tool был реально зарегистрирован.
