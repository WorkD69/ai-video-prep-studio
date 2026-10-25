# AI Video Prep Studio

Локальный сервис: видео (файл или ссылка) → ZIP, который можно отдать AI-агенту (Claude и др.),
чтобы он «посмотрел» ролик.

В архиве:

| Файл | Что это |
|---|---|
| `README.md` | инструкция для агента: что внутри и как читать |
| `transcript.md` | **главное**: главы, речь с таймкодами `[ЧЧ:ММ:СС]`, ключевые кадры прямо в тексте |
| `frames/frame_NNNNNNs.jpg` | кадры по смене сцен (~5 в минуту, до 300 штук, 1280px); NNNNNN — секунда видео |
| `transcript.json` | тот же транскрипт структурой |
| `frames_manifest.csv` | список кадров с таймкодами |
| `failed_parts.md` | куски без речи (музыка, демо без голоса) |
| `metadata.json` | источник, язык, откуда текст, режим кадров |
| `subtitles.<lang>.vtt` | оригинальные субтитры автора, если были |

Откуда текст: если у ролика есть **ручные субтитры автора** на языке видео — берутся они, иначе речь
распознаётся локально **faster-whisper `turbo` на видеокарте**. Кадры выбирает библиотека
[claude-real-video](https://github.com/HUANGCHIHHUNGLeo/claude-real-video) (проверенный коммит, см.
`docs/adr/006-crv-frames-real-transcription-url-intake.md`); если она падает — кадр каждые 20 секунд.

Лимиты: до 3 часов и 5 ГБ; одна задача за раз; файлы удаляются через 24 часа.

## Запуск

Нужно: Docker Desktop с WSL2, видеокарта NVIDIA (в Docker должен быть `nvidia` runtime:
`docker info | grep -i runtimes`). Без GPU тоже работает, но распознавание речи будет в разы медленнее.

1. Создай `.env` (рядом с `docker-compose.yml`):

   ```
   SECRET_KEY=<длинная случайная строка>
   DATA_DIR=A:/aivps-data            # куда класть загрузки и готовые ZIP (нужно много места)
   HF_CACHE_DIR=A:/AI/hf-cache       # кеш модели whisper (~1.6 ГБ, качается один раз)
   ```

2. Собери и подними:

   ```
   docker compose build
   docker compose run --rm app python -m alembic upgrade head
   docker compose up -d
   ```

   Если ссылки отклоняются с «local or private address», а в контейнере сайты резолвятся в
   `198.18.x.x`, у тебя VPN/прокси в fake-IP режиме (Clash, sing-box). Добавь в `.env`
   `URL_EXTRA_ALLOWED_NETWORKS=["198.18.0.0/15"]` и сделай `docker compose up -d`. Цена: адрес
   за тебя резолвит прокси, поэтому защита от ссылок на локальную сеть в этом режиме слабее.

3. Открой http://127.0.0.1:8000 — вкладка «Файл» или «Ссылка», при желании язык и термины
   (например `Cowork, Supabase` — whisper будет писать их правильно).

4. Дождись «Готово» → «Скачать ZIP» → отдай архив агенту:
   «Проанализируй видео из архива, начни с README.md».

Проверка, что всё живо: http://127.0.0.1:8000/health → `status=ok, db=ok, redis=ok`.

### После перезагрузки

У всех контейнеров `restart: unless-stopped`: стек сам поднимается, как только запущен Docker Desktop,
и воркер сам перезапускается после обрыва связи с Redis (например, после сна ноутбука).
Нужно только, чтобы Docker Desktop был запущен — либо вручную, либо включи
Settings → General → «Start Docker Desktop when you sign in to your computer».
Остановить сервис совсем: `docker compose stop` (после этого сам он уже не поднимется,
вернуть — `docker compose up -d`).

## Скорость (RTX 3070, видео 51 мин)

- распознавание речи: ~4 мин;
- выбор кадров: основная часть времени (декодирование видео на CPU);
- для ссылок видео качается в 720p — этого хватает, чтобы читался текст на экране.

## Разработка

```
python3 -m venv .venv && .venv/Scripts/pip install -r requirements.txt
.venv/Scripts/python -m pytest tests/ -q
```

Тесты не требуют GPU, ffmpeg, сети и crv: используются фейки (`MEDIA_BACKEND=fake`,
`TRANSCRIBER_BACKEND=mock`, `FRAME_BACKEND=fake`, см. `tests/conftest.py`). Тяжёлые зависимости
(`requirements-media.txt`, `requirements-crv.txt`) ставятся только в Docker.

Архитектура и решения: `docs/adr/`, правила проекта: `CLAUDE.md`.
