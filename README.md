# AI Stylist

Персональный AI-стилист и виртуальная примерочная для ChatGPT и веб-клиентов.

Проект полностью изолирован от `ai-lawyer`: отдельный репозиторий, порты, база данных, фото, generated-файлы, переменные окружения и процессы.

## Что уже умеет MVP

- Создание персонального профиля.
- Рост, вес, возрастной диапазон, цель по стилю и предпочтения.
- Хранение до 5 референсных фото.
- 3 бесплатных preview-кредита для нового профиля.
- Примерка одежды.
- Подбор причёски.
- Создание полного образа.
- Режим «измени только один элемент».
- Жёсткий identity-lock: лицо, возраст, пропорции и узнаваемость пользователя запрещено менять без прямой просьбы.
- История генераций.
- Автоматическое списание кредитов только за успешный результат.
- Автоматический возврат кредита при ошибке генерации.
- Удаление профиля вместе с фото и результатами.
- REST API на FastAPI.
- MCP-сервер для подключения к ChatGPT Plugins.
- GitHub Actions тесты.
- PM2, Docker Compose и Nginx-конфиги для деплоя.

## Image-модели

По умолчанию:

- preview: `gpt-image-2.5-flare`, quality `medium`;
- final: `gpt-image-2.5-sunburst`, quality `medium`;
- размер: `1024x1536`;
- формат: WebP.

Все эти параметры можно менять через `.env`.

## Кредиты

По умолчанию:

- новый профиль: 3 бесплатных кредита;
- preview: 1 кредит;
- final: 4 кредита.

Значения вынесены в env-переменные и не зашиты намертво в бизнес-логику.

## Быстрый запуск

Создать окружение:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Добавить в `.env` реальный ключ:

```env
OPENAI_API_KEY=...
```

Запустить API:

```bash
uvicorn app.main:app --host 127.0.0.1 --port 8010
```

В другом процессе запустить MCP:

```bash
python mcp_server.py
```

API health:

```
GET http://127.0.0.1:8010/health
```

MCP по умолчанию работает на порту 8011 через Streamable HTTP.

## Основные REST endpoints

```
POST   /api/v1/profiles
GET    /api/v1/profiles/{profile_id}
PATCH  /api/v1/profiles/{profile_id}
DELETE /api/v1/profiles/{profile_id}

GET    /api/v1/profiles/{profile_id}/balance

GET    /api/v1/profiles/{profile_id}/photos
POST   /api/v1/profiles/{profile_id}/photos
DELETE /api/v1/profiles/{profile_id}/photos/{photo_id}

POST   /api/v1/profiles/{profile_id}/generations
GET    /api/v1/profiles/{profile_id}/generations
GET    /api/v1/profiles/{profile_id}/generations/{generation_id}
GET    /api/v1/profiles/{profile_id}/generations/{generation_id}/image
```

Режимы генерации:

- `outfit` — изменить одежду;
- `haircut` — изменить только причёску;
- `full_look` — собрать полный образ;
- `change_item` — изменить только один элемент существующего результата.

Уровни:

- `preview`;
- `final`.

Пример:

```json
{
  "mode": "outfit",
  "instruction": "Темно-синий костюм и строгое пальто с капюшоном, без джинсов",
  "tier": "preview"
}
```

Для `change_item` нужно передать `base_generation_id`.

## MCP tools

Сервер предоставляет инструменты:

- `create_style_profile`;
- `get_style_profile`;
- `get_style_balance`;
- `try_outfit`;
- `try_haircut`;
- `create_full_look`;
- `change_one_item`;
- `recent_style_generations`.

После публичного деплоя MCP URL будет вида:

```
https://stylist.example.com/mcp
```

В ChatGPT Developer Mode этот URL можно подключить как личный plugin.

## Деплой рядом с AI Lawyer

Рекомендуемая схема на одном Ubuntu-сервере:

```
/opt/ai-lawyer   -> отдельный проект
/opt/ai-stylist  -> отдельный проект

AI Lawyer        -> свои порты, база и env
AI Stylist API   -> 127.0.0.1:8010
AI Stylist MCP   -> 127.0.0.1:8011
```

PM2-конфиг: `ecosystem.config.cjs`.

Nginx-шаблон: `deploy/nginx-ai-stylist.conf`.

В Nginx-шаблоне заменить `stylist.example.com` на реальный домен, затем подключить HTTPS/Cloudflare.

## Docker

Можно запустить API и MCP раздельными сервисами:

```bash
docker compose up -d --build
```

SQLite, reference photos и generated-файлы лежат в отдельных volumes.

## Тесты

```bash
pip install -r requirements-dev.txt
python -m pytest -q
```

GitHub Actions автоматически запускает тесты при каждом push в `main`.

Тесты проверяют:

- создание профиля;
- бесплатный баланс;
- загрузку референса;
- preview-генерацию через mock;
- получение generated-image;
- режим change-item;
- недостаток кредитов;
- возврат кредита при ошибке;
- identity-lock в prompt;
- совместимость параметров с текущим OpenAI Python SDK;
- импорт MCP-сервера.

## Безопасность и данные

- Реальный `OPENAI_API_KEY` не хранится в GitHub.
- `.env`, SQLite, uploads и generated исключены из git.
- Фото ограничены JPEG/PNG/WebP, максимум 15 MB на файл.
- До 5 reference photos на профиль.
- Профиль можно удалить вместе с локальными фото и generated-файлами.
- Перед публичным запуском необходимо добавить полноценную пользовательскую авторизацию/OAuth и платёжный webhook. UUID профиля достаточен только для закрытого MVP-теста.

## Что нужно для реального end-to-end теста

1. Реальный `OPENAI_API_KEY`.
2. Деплой на сервер.
3. Публичный HTTPS-домен.
4. Загрузить 3–5 референсных фото.
5. Проверить реальные Flare/Sunburst генерации и фактический `usage`.
6. После этого окончательно зафиксировать цены пакетов и маржу.

