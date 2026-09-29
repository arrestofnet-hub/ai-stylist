import sqlite3
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse

from app.config import settings
from app.db import connection, init_db
from app.routes.admin import router as admin_router
from app.routes.generations import router as generations_router
from app.routes.profiles import router as profiles_router
from app.services.image_service import recover_stale_generations

from mcp_server import server as mcp_server


mcp_http_app = mcp_server.streamable_http_app(
    streamable_http_path="/mcp",
    json_response=True,
    stateless_http=True,
    host=settings.host,
)


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    recover_stale_generations()
    async with mcp_server.session_manager.run():
        yield


app = FastAPI(
    title=settings.app_name,
    version="0.6.0",
    description="AI Stylist backend and virtual try-on API",
    lifespan=lifespan,
)

app.include_router(profiles_router, prefix="/api/v1")
app.include_router(generations_router, prefix="/api/v1")
app.include_router(admin_router, prefix="/api/v1")


@app.get("/")
def root() -> dict[str, str]:
    return {
        "service": settings.app_name,
        "status": "ok",
        "version": "0.6.0",
    }


@app.get("/.well-known/openai-apps-challenge", response_class=PlainTextResponse)
def openai_domain_challenge():
    if not settings.openai_apps_challenge_token:
        return PlainTextResponse("Not configured", status_code=404)
    return PlainTextResponse(
        settings.openai_apps_challenge_token,
        headers={"Cache-Control": "no-store"},
    )


@app.get("/support", response_class=HTMLResponse)
def support():
    return HTMLResponse(
        """<!doctype html><html lang="ru"><head><meta charset="utf-8">
<title>AI Стилист — Поддержка</title></head>
<body style="font-family:system-ui;max-width:820px;margin:40px auto;padding:0 20px;line-height:1.55">
<h1>Поддержка AI Стилист</h1>
<p>По вопросам подключения, виртуальной примерки, удаления профиля и технических ошибок используйте
<a href="https://github.com/arrestofnet-hub/ai-stylist/issues">GitHub Issues</a>.</p>
<p>Не публикуйте в открытом issue свои фотографии, документы, контактные данные или другие конфиденциальные материалы.</p>
</body></html>"""
    )


@app.get("/privacy", response_class=HTMLResponse)
def privacy():
    return HTMLResponse(
        """<!doctype html><html lang="ru"><head><meta charset="utf-8">
<title>AI Стилист — Политика конфиденциальности</title></head>
<body style="font-family:system-ui;max-width:820px;margin:40px auto;padding:0 20px;line-height:1.55">
<h1>Политика конфиденциальности</h1>
<p>AI Стилист обрабатывает только данные, необходимые для выбранных пользователем функций виртуальной примерки.</p>
<h2>Какие данные обрабатываются</h2>
<p>Референсные фотографии, параметры профиля (например, рост, вес или возрастной диапазон, если пользователь их указал),
стилевые предпочтения, инструкции к примерке, созданные изображения, история генераций и технические данные аутентификации.</p>
<h2>Для чего</h2>
<p>Для сохранения профиля и предпочтений, примерки одежды и причесок, создания образов, точечных изменений,
показа истории результатов, учета кредитов и обеспечения безопасности сервиса.</p>
<h2>Получатели</h2>
<p>Изображения и инструкции могут передаваться выбранному провайдеру генерации изображений только для выполнения запроса.
Инфраструктурные поставщики могут обрабатывать данные в объеме, необходимом для хостинга и хранения. Данные не продаются рекламодателям.</p>
<h2>Хранение и удаление</h2>
<p>Профиль и референсные фотографии хранятся до удаления профиля пользователем. Сгенерированные результаты имеют ограниченный
срок хранения, задаваемый конфигурацией сервиса; по умолчанию — 30 дней. Удаление профиля удаляет связанные локальные фотографии,
результаты и историю.</p>
<h2>Контроль пользователя</h2>
<p>Пользователь может просматривать и менять предпочтения, удалять отдельные референсные фото и полностью удалить свой профиль.</p>
<h2>Безопасность</h2>
<p>В публичном многопользовательском режиме доступ к профилю должен быть привязан к аутентифицированному пользователю.
Сервис ограничивает тип и размер загружаемых изображений и не раскрывает наличие чужого профиля.</p>
<p>Версия политики: 29 сентября 2026.</p>
</body></html>"""
    )


@app.get("/terms", response_class=HTMLResponse)
def terms():
    return HTMLResponse(
        """<!doctype html><html lang="ru"><head><meta charset="utf-8">
<title>AI Стилист — Условия использования</title></head>
<body style="font-family:system-ui;max-width:820px;margin:40px auto;padding:0 20px;line-height:1.55">
<h1>Условия использования</h1>
<p>AI Стилист предоставляет виртуальную примерку и рекомендации по образу на основе материалов, которые пользователь вправе использовать.</p>
<h2>Результаты</h2>
<p>Сгенерированное изображение является визуальной симуляцией. Оно не гарантирует точную посадку, цвет, ткань,
размер или фактический результат стрижки в реальном мире.</p>
<h2>Материалы пользователя</h2>
<p>Пользователь должен загружать только фотографии, которые он вправе использовать. Нельзя использовать сервис
для выдачи себя за другого человека, обмана или иных противоправных целей.</p>
<h2>Оплата и кредиты</h2>
<p>До публичного коммерческого запуска цены и правила платных кредитов считаются тестовыми.
Кредит списывается только по правилам текущей версии сервиса; при технической ошибке генерации предусмотрен возврат.</p>
<h2>Изменения</h2>
<p>Функции, модели и условия могут обновляться по мере развития сервиса.</p>
<p>Версия условий: 29 сентября 2026.</p>
</body></html>"""
    )


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "healthy"}


@app.get("/ready")
def readiness():
    database_ready = False
    try:
        with connection() as conn:
            conn.execute("SELECT 1").fetchone()
        database_ready = True
    except sqlite3.Error:
        database_ready = False

    provider_ready = bool(settings.openai_api_key)
    ready = database_ready and provider_ready

    payload = {
        "status": "ready" if ready else "not_ready",
        "database": "ready" if database_ready else "unavailable",
        "image_provider": "configured" if provider_ready else "not_configured",
    }

    if ready:
        return payload

    return JSONResponse(status_code=503, content=payload)


# Keep MCP on the same public process/port as the REST and policy routes.
# This mount is last so FastAPI's explicit routes win; unmatched /mcp requests
# are then handled by the MCP Streamable HTTP app.
app.mount("/", mcp_http_app)
