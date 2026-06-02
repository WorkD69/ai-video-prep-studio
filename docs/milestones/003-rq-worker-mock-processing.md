# Milestone 003 — RQ Worker + Mock Processing

## Summary

Добавить асинхронный жизненный цикл задачи через RQ (Redis Queue) **без реального
media pipeline**. После M003 `POST /jobs/upload` ставит задачу в очередь, а отдельный
worker-процесс прогоняет её через mock-обработку и обновляет статус в БД:

```
pending  ->  queued  ->  processing  ->  done
                                      \-> failed
```

Mock processing = чистая машина состояний без ffmpeg/ffprobe/транскрипции/скриншотов/ZIP.
Цель — зафиксировать enqueue-контракт, поведение воркера, идемпотентность и обработку
dual-write до того, как в M004+ появится реальная обработка.

## Branch naming

- **Spec (docs-only, текущая):** `docs/milestone-003-rq-worker-mock-processing-spec`
- **Future implementation:** `feature/milestone-003-rq-worker-mock-processing`

## In Scope

- RQ enqueue из `POST /jobs/upload` (job_id передаётся в очередь, не ORM-объект).
- Guarded переход `pending -> queued` после успешного enqueue.
- Компенсация при провале enqueue: `pending -> failed` + HTTP 503.
- Worker-функция `process_job(job_id)` с mock-обработкой.
- Guarded переходы в воркере: `pending|queued -> processing -> done|failed`.
- Идемпотентность воркера (безопасная повторная доставка / повторный вызов).
- Детерминированный триггер падения в mock для покрытия `failed`-пути в тестах.
- `worker`-сервис в docker-compose (та же image, команда `rq worker`, общие volumes).
- Тесты: enqueue-поведение upload + поведение воркера + регрессия.

## Out of Scope (Explicit Non-Goals)

- ffmpeg / ffprobe
- транскрипция (faster-whisper / MockTranscriber для реального текста)
- скриншоты / manifest CSV
- ZIP generation
- `GET /download/{job_id}` (mock не создаёт артефакт — добавим в милстоуне с реальным pipeline)
- реальный media pipeline и его архитектура
- frontend UI / Jinja-шаблоны
- auth / payments
- **rate-limiting (1 active job per session/IP, 429)** — отдельный милстоун
- retry-логика для failed jobs
- reaper/cleanup orphan- или expired-задач
- multiple workers / horizontal scaling
- A/B branches (нет реальной архитектурной неопределённости)
- Skills / MCP

## Current state after M002B

- `POST /jobs/upload`: валидация (content-type + extension + magic bytes + streaming size),
  сохранение как `{uuid}{ext}`, server-generated `session_id`, **commit со `status=pending`**,
  `expires_at = created_at + 24h`, cleanup файла при сбое БД. **enqueue отсутствует.**
- `GET /jobs/{job_id}`: 200 / 404 / 422; уже возвращает `status` и `error_message`.
- `JobStatus` enum уже содержит все 5 значений: `pending, queued, processing, done, failed`.
- `Job`-модель уже содержит все нужные поля: `status, error_message, completed_at,
  output_path, duration_seconds, ...` — **схема БД менять не нужно, миграции нет.**
- `rq==2.0.0` уже в requirements.txt, но не используется.
- `app/redis_client.py` уже есть (`get_redis()`, `check_redis()`); `/health` проверяет Redis.
- **Каталога `app/workers/` нет** — создаётся в реализации.

## Endpoint / API behavior changes

### POST /jobs/upload (изменяется)
Логика дополняется ПОСЛЕ существующего commit'а (`status=pending`):

1. (без изменений) Валидация → сохранение файла → `Job(status=pending)` → `db.commit()`.
2. (новое) `enqueue` задачи в RQ с `rq_job_id = str(job.id)`, аргумент функции — `str(job.id)`.
3. (новое) **Успех enqueue → guarded** `UPDATE jobs SET status=queued WHERE id=:id AND status='pending'`,
   commit. Дальше ответ зависит от числа затронутых строк:
   - **затронута 1 строка** → воркер ещё не забрал задачу → `response.status = queued`;
   - **затронуто 0 строк** → воркер уже забрал задачу и продвинул её (`processing`/`done`/`failed`)
     между нашим enqueue и flip'ом. В этом случае **НЕ навязываем `queued`**: делаем
     **re-read job из БД** (`db.refresh(job)` или повторный `db.get(Job, id)`) и возвращаем
     **актуальный текущий статус**. Статус назад не откатываем.
4. (новое) **Провал enqueue** (Redis down и т.п.) → **guarded** `UPDATE ... SET status=failed,
   error_message='enqueue_failed', completed_at=now() WHERE id=:id AND status='pending'`,
   commit, **ответ HTTP 503** (`{"detail": "job_enqueue_failed"}`).

**Контракт ответа (форма не меняется):** на happy-path `UploadResponse.status` теперь `queued`
(или реальный продвинутый статус при гонке — см. п.3), вместо `pending`. Это аддитивная
эволюция (значения enum уже валидны, поля те же).

### GET /jobs/{job_id} (логически без изменений)
Уже возвращает `status` и `error_message`. В M003 будет естественно отражать живые значения
`queued | processing | done | failed`. **Структура ответа и схема не меняются.**

## RQ enqueue contract

- **Очередь:** одна очередь, имя `default` (через `Settings`, по умолчанию `"default"`).
- **Функция:** `app.workers.process_job.process_job(job_id: str)`.
- **Аргумент:** только `str(job.id)` — НЕ ORM-объект. Воркер живёт в отдельном процессе и
  обязан открыть свежую DB-сессию и перечитать job из БД (нельзя доверять сериализованному payload).
- **rq job_id:** `str(job.id)` — для трассировки и идемпотентности на уровне RQ.
- **job_timeout:** из `Settings` (`rq_job_timeout`, для mock короткий, напр. 600s).
- **Соединение:** RQ `Queue` поверх того же `redis_url` из `Settings`.
- **Helper:** `get_queue()` (в `app/redis_client.py` или новом `app/queue.py`), возвращает `rq.Queue`.

## Worker behavior

Запуск: отдельный процесс/контейнер командой `rq worker <queue>` (см. docker-compose).
Worker подключается к Redis и держит **собственную** DB-сессию (`SessionLocal`).

`process_job(job_id: str)`:
1. Открыть свежую DB-сессию.
2. `job = db.get(Job, job_id)`. Если `None` → лог `worker_job_not_found` (job_id, stage), **no-op return**.
3. **Guarded переход в processing:**
   `UPDATE jobs SET status='processing' WHERE id=:id AND status IN ('pending','queued')`, commit.
   Если 0 строк → задача уже в processing/done/failed (повторная доставка) → лог + **no-op return**
   (идемпотентность, без двойной обработки).
4. **Mock processing:** опциональная небольшая задержка (`mock_processing_delay_seconds`, default 0).
   Никакого ffmpeg/транскрипции/скриншотов/ZIP. Проверка триггера падения (см. Failure semantics).
5. **Успех (guarded):**
   `UPDATE jobs SET status='done', completed_at=now() WHERE id=:id AND status='processing'`,
   commit. `output_path` остаётся `NULL` (артефакта нет). Если затронуто 0 строк → статус уже
   не `processing` (чужое финальное состояние / гонка) → **log + no-op**, не перетирать, не откатывать.
6. **Падение (триггер или исключение, guarded):** перехватить, затем
   `UPDATE jobs SET status='failed', error_message='processing_failed', completed_at=now()
   WHERE id=:id AND status='processing'`, commit. Если затронуто 0 строк → статус уже не
   `processing` → **log + no-op**, не перетирать чужое финальное состояние. После обработки
   падения — **re-raise**, чтобы оно отразилось в RQ failed registry (re-raise происходит
   независимо от числа затронутых строк, но **без overwrite** уже выставленного финального
   статуса). (Retry — out of scope, авто-ретрая нет.)

Все переходы — **guarded** и монотонные: и вход в `processing`, и финальные записи (`done`/`failed`)
ограничены `WHERE status='processing'`, поэтому статус не откатывается назад и не перетирается
ни при гонке, ни при повторной доставке.

## Job status semantics

| Статус | Значение |
|---|---|
| `pending` | Создан и сохранён в БД; ещё не поставлен в очередь (или enqueue в процессе/компенсации). |
| `queued` | Успешно поставлен в RQ, ждёт воркера. |
| `processing` | Воркер забрал и выполняет mock-обработку. |
| `done` | Mock-обработка успешно завершена; `completed_at` установлен. |
| `failed` | Провал enqueue ИЛИ падение воркера; `error_message` и `completed_at` установлены. |

## Failure semantics

- **Enqueue failed (Redis недоступен):** guarded `pending -> failed`, `error_message='enqueue_failed'`,
  HTTP **503**. Нет задач, застрявших в `pending`/`queued`.
- **Worker exception / триггер:** guarded переход в `failed` + безопасный `error_message` +
  `completed_at`, re-raise для RQ failed registry.
- **Job не найден воркером:** no-op + лог (не падаем).
- **Повторная доставка / двойной вызов:** guarded-переход отсекает (0 строк → no-op).
- **Гонка flip-vs-worker:** если воркер успел продвинуть job (`processing`+) до flip'а API —
  guarded flip (`WHERE status='pending'`) затронет 0 строк; статус не откатывается, а upload
  возвращает реально текущий статус из БД (re-read, см. Endpoint behavior п.3).
- **Триггер падения (для тестов):** детерминированный, **выключен в production по умолчанию**.
  Два независимых механизма, оба test/dev-only:
  - `mock_force_fail=true` (`Settings`, default `false`) — глобально форсирует падение mock;
  - filename-based: `original_filename`, начинающийся с `fail` (case-insensitive), вызывает
    падение **только** при `mock_failure_trigger_enabled=true` (`Settings`, default `false`).
  При дефолтной (production) конфигурации оба флага `false`, поэтому файл `fail*.mp4`
  обрабатывается нормально и **НЕ вызывает** failure. Это закрывает риск, что пользователь
  именем файла принудит задачу падать в проде.

## Config / env changes

Новых **обязательных** env нет (`redis_url` уже есть). Добавляются опциональные в `Settings`:

| Setting | Default | Назначение |
|---|---|---|
| `rq_queue_name` | `"default"` | Имя RQ-очереди. |
| `rq_job_timeout` | `600` | Таймаут RQ-job (сек). |
| `mock_processing_delay_seconds` | `0` | Имитация работы (для ручной проверки). |
| `mock_force_fail` | `false` | Глобальное принудительное падение mock (test/dev-only). **Default false.** |
| `mock_failure_trigger_enabled` | `false` | Разрешает filename-based триггер падения (`fail*`). **Default false** — в production-конфиге выключен. |

**Безопасность триггера падения:** filename-based триггер (`original_filename`, начинающийся с
`fail`) срабатывает **только** если `mock_failure_trigger_enabled=true`. При дефолтной
(production) конфигурации `mock_failure_trigger_enabled=false` и `mock_force_fail=false` —
файл с именем `fail*.mp4` обрабатывается как обычный и **НЕ вызывает** падение. Оба флага
по умолчанию `false` и предназначены только для test/dev окружения.

docker-compose: добавляется `worker`-сервис (та же image, `command: rq worker default`,
те же env, общие volumes `uploads/`/`outputs/`, `depends_on: [redis, db]`).

## Files expected to change during implementation (НЕ в spec-фазе)

- `app/workers/__init__.py` — новый
- `app/workers/process_job.py` — новый: mock `process_job(job_id)` + guarded-переходы + триггер
- `app/redis_client.py` (или новый `app/queue.py`) — `get_queue()` helper
- `app/api/jobs.py` — enqueue + guarded flip + re-read при гонке + компенсация в `POST /jobs/upload`
- `app/config.py` — новые опциональные settings
- `docker-compose.yml` — `worker`-сервис
- `tests/test_worker.py` — новый
- `tests/test_upload.py` — расширить (enqueue-поведение)
- `AI_WORKLOG.md` — запись о милстоуне
- `docs/ARCHITECTURE.md` — (опционально) отметить worker-процесс
- `requirements.txt` — без изменений (`rq` уже есть)
- **Миграции Alembic — НЕ нужны** (схема и enum не меняются)

## Test matrix

**Upload / enqueue (queue замокана):**
- `test_upload_enqueues_and_flips_to_queued` — happy: enqueue вызван 1 раз с `str(job.id)`,
  guarded flip затронул 1 строку, финальный response status `queued`.
- `test_upload_enqueue_failure_marks_failed_503` — enqueue бросает → статус `failed`,
  `error_message='enqueue_failed'`, HTTP 503, нет orphan в `pending`/`queued`.
- `test_upload_flip_noop_returns_actual_status` — воркер успел продвинуть job в `processing`
  до guarded flip → flip затрагивает 0 строк → БД-статус остаётся `processing` (race-safety)
  **и** upload re-read'ит job и возвращает `processing` в response (не навязывает `queued`).

**Worker (`process_job` вызывается напрямую с тестовой БД):**
- `test_worker_happy_path_done` — `queued` job → `done`, `completed_at` установлен.
- `test_worker_picks_pending_or_queued` — guarded-переход принимает и `pending`, и `queued`.
- `test_worker_idempotent_skip_if_done` — job уже `done` → no-op, без изменений и исключений.
- `test_worker_skip_if_already_processing_or_failed` — статус не откатывается.
- `test_worker_failure_trigger_marks_failed` — триггер при включённом флаге → `failed` +
  `error_message='processing_failed'` + `completed_at`.
- `test_worker_job_not_found` — неизвестный `job_id` → no-op, без падения.
- `test_worker_filename_fail_trigger_disabled_by_default` — `mock_failure_trigger_enabled=false`
  (дефолт) + `original_filename='fail_video.mp4'` → job доходит до `done`, **не** падает
  (filename-триггер выключен в production-конфиге).
- `test_worker_success_guarded_does_not_overwrite_non_processing` — финальный success-UPDATE
  при статусе ≠ `processing` (напр. job уже `failed`) затрагивает 0 строк → статус не перетирается, no-op.
- `test_worker_failure_guarded_does_not_overwrite_non_processing` — финальный failure-UPDATE
  при статусе ≠ `processing` (напр. job уже `done`) затрагивает 0 строк → статус не перетирается,
  no-op; re-raise при этом не делает overwrite.

**Регрессия:**
- Все существующие 15 тестов проходят; `GET /jobs/{job_id}` корректно отдаёт новые статусы.

Требования QA: детерминированно, без сети, без faster-whisper, < 60 секунд.

## Security gate

- В M003 нет новых file/subprocess путей (нет ffmpeg) → subprocess-injection N/A для этого милстоуна.
- В очередь и воркер передаётся **UUID job_id** (валидируется), не пользовательский путь → нет traversal.
- `redis_url` — из Settings/env, секреты не хардкодятся.
- Воркер перечитывает job из БД по UUID (не доверяет сериализованному payload).
- `error_message`, возвращаемый в `GET`, — **короткая безопасная строка** (`enqueue_failed`,
  `processing_failed`), без сырых traceback/исключений и без секретов.
- **Триггер падения mock безопасен в prod.** Filename-based триггер (`fail*`) активен **только**
  при `mock_failure_trigger_enabled=true`; глобальный `mock_force_fail` — только при `true`.
  Оба флага default `false`, поэтому в production-конфиге пользователь **не может** именем файла
  (`fail*.mp4`) принудить задачу к падению — это закрытый вектор (Codex Finding #1).

## Reliability gate

- Dual-write разрешён: guarded-переходы + компенсация при провале enqueue + re-read при гонке flip.
- Воркер идемпотентен (безопасная повторная доставка).
- На happy-path orphan нет. **Остаточный риск:** падение процесса между commit (`pending`) и
  enqueue оставит job в `pending` (recoverable, "не поставлен в очередь"). Reaper/перенэнкью —
  out of scope M003, задокументировано, принято для MVP.
- Redis down обрабатывается корректно (503, без orphan).
- DB-сессия воркера изолирована от request-сессии.

## Docker / manual gates (canonical)

```
docker compose run --rm app python -m alembic upgrade head   # подтвердить: новой миграции нет
docker compose up -d app worker
# /health -> status=ok, db=ok, redis=ok
```
- Manual happy: `POST /jobs/upload` → 201, `status=queued`; затем poll `GET /jobs/{job_id}` →
  доходит до `done`; в логах воркера видны `job_id` + `stage`-переходы.
- Manual enqueue-fail: остановить redis → `POST /jobs/upload` → **503**, job в `failed`.
- (Windows) не запускать host-native проверку БД — только Docker canonical gate.

## Acceptance criteria

- **AC1** — Успешный `POST /jobs/upload` ставит ровно одну RQ-задачу (`rq_job_id=str(job.id)`,
  аргумент `str(job.id)`); при guarded flip с 1 затронутой строкой возвращает `status=queued`.
- **AC2** — `queued`-задача воркером проходит `queued → processing → done`, `completed_at` установлен.
- **AC3** — Провал enqueue → job `failed` + `error_message='enqueue_failed'` + HTTP 503; нет задач в `pending`/`queued`.
- **AC4** — Воркер идемпотентен: повторная обработка `done`/`failed`/`processing` не откатывает статус.
- **AC5** — Триггер падения воркера (при включённом `mock_force_fail` или
  `mock_failure_trigger_enabled`) → `failed` + `error_message='processing_failed'` + `completed_at`.
- **AC6** — При дефолтной (production) конфигурации (`mock_force_fail=false`,
  `mock_failure_trigger_enabled=false`) файл с именем `fail*.mp4` **НЕ** вызывает падение —
  job доходит до `done`. (Codex Finding #1.)
- **AC7** — Финальные записи воркера guarded `WHERE status='processing'`: при 0 затронутых строк
  (статус уже не `processing`) — no-op, без отката и без overwrite чужого финального состояния;
  re-raise при failure не перетирает уже выставленный статус. (Codex Finding #2.)
- **AC8** — `GET /jobs/{job_id}` отражает живой статус во всех 5 состояниях.
- **AC9** — Нет изменений схемы БД / миграций; `JobStatus` enum без изменений.
- **AC10** — Все переходы монотонны (guarded на входе в `processing` и на финальных записях);
  откатов назад и перетирания при гонке нет.
- **AC11** — Если воркер продвинул job до завершения guarded flip (flip затронул 0 строк),
  `POST /jobs/upload` re-read'ит job и возвращает **актуальный текущий статус** из БД,
  без навязывания `queued`.
- **AC12** — pytest зелёный (15 существующих + новые), без сети, < 60s.
- **AC13** — Docker canonical gate зелёный; `worker`-контейнер стартует и доводит mock-задачу до конца.
- **AC14** — Не введены ffmpeg/ffprobe/транскрипция/скриншоты/ZIP/download.

## Implementation order

1. `get_queue()` helper + новые опциональные `Settings` (queue name, timeout, mock-флаги).
2. `app/workers/process_job.py` — mock `process_job(job_id)` с guarded-переходами и триггером падения.
3. Встроить enqueue + guarded flip + re-read при гонке + компенсацию в `POST /jobs/upload`.
4. Добавить `worker`-сервис в docker-compose.
5. Тесты: `tests/test_worker.py` + расширить `tests/test_upload.py`.
6. `pytest tests/ -v` → зелёный.
7. Docker canonical gate + manual smoke (happy + redis-down).
8. Запись в `AI_WORKLOG.md`.
9. Security Agent + Codex Reviewer в чистом контексте (diff + acceptance criteria).

## Открытые вопросы / остаточные риски (задокументировано, не блокирует)

- Orphan-риск при крэше между commit(`pending`) и enqueue → recoverable `pending`; reaper отложен.
- `done` mock-задача имеет `output_path=NULL` (артефакта нет) — нормально для M003.
- Имя очереди `default` фиксируется в Settings — при росте можно вынести в несколько очередей.
