from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.config import settings
from app.main import app
from app.services import image_service
from app.services.image_service import ImageGenerationFailed


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "database_path", str(tmp_path / "test.db"))
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path / "uploads"))
    monkeypatch.setattr(settings, "generated_dir", str(tmp_path / "generated"))
    monkeypatch.setattr(settings, "public_base_url", "http://testserver")

    with TestClient(app) as test_client:
        yield test_client


def create_profile(client: TestClient) -> str:
    response = client.post(
        "/api/v1/profiles",
        json={
            "display_name": "Test User",
            "style_goal": "modern strict style",
            "preferences": {
                "avoid_items": ["jeans"],
                "preferred_items": ["suits", "hooded coats"],
                "preserve_identity": True,
            },
        },
    )
    assert response.status_code == 201
    data = response.json()
    assert data["free_tries"] == 3
    return data["id"]


def make_image_bytes() -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (640, 800), "white").save(buffer, format="JPEG", quality=90)
    return buffer.getvalue()


def upload_reference(client: TestClient, profile_id: str) -> None:
    response = client.post(
        f"/api/v1/profiles/{profile_id}/photos",
        files={"photo": ("reference.jpg", make_image_bytes(), "image/jpeg")},
    )
    assert response.status_code == 201
    assert response.json()["mime_type"] == "image/webp"


def test_profile_photo_generation_and_change_item(client, monkeypatch):
    profile_id = create_profile(client)
    upload_reference(client, profile_id)

    def fake_call(*args, **kwargs):
        return b"fake-webp-image", {"image_tokens": 123}

    monkeypatch.setattr(image_service, "_call_openai", fake_call)

    first = client.post(
        f"/api/v1/profiles/{profile_id}/generations",
        json={
            "mode": "outfit",
            "instruction": "Dark tailored suit and hooded coat",
            "tier": "preview",
        },
    )
    assert first.status_code == 201
    first_data = first.json()
    assert first_data["status"] == "completed"
    assert first_data["charged_free"] == 1
    assert first_data["charged_paid"] == 0
    assert first_data["image_url"]

    image = client.get(first_data["image_url"])
    assert image.status_code == 200
    assert image.content == b"fake-webp-image"

    change = client.post(
        f"/api/v1/profiles/{profile_id}/generations",
        json={
            "mode": "change_item",
            "instruction": "Replace only the coat with a camel hooded coat",
            "tier": "preview",
            "base_generation_id": first_data["id"],
        },
    )
    assert change.status_code == 201
    assert change.json()["status"] == "completed"

    balance = client.get(f"/api/v1/profiles/{profile_id}/balance")
    assert balance.status_code == 200
    assert balance.json()["free_tries"] == 1
    assert balance.json()["total_available"] == 1

    final = client.post(
        f"/api/v1/profiles/{profile_id}/generations",
        json={
            "mode": "full_look",
            "instruction": "Final polished business look",
            "tier": "final",
        },
    )
    assert final.status_code == 402


def test_failed_generation_refunds_free_try(client, monkeypatch):
    profile_id = create_profile(client)
    upload_reference(client, profile_id)

    def fail_call(*args, **kwargs):
        raise ImageGenerationFailed("provider failed")

    monkeypatch.setattr(image_service, "_call_openai", fail_call)

    response = client.post(
        f"/api/v1/profiles/{profile_id}/generations",
        json={
            "mode": "haircut",
            "instruction": "Neat textured crop",
            "tier": "preview",
        },
    )
    assert response.status_code == 502

    balance = client.get(f"/api/v1/profiles/{profile_id}/balance")
    assert balance.status_code == 200
    assert balance.json()["free_tries"] == 3
    assert balance.json()["paid_credits"] == 0


def test_change_item_requires_base_generation(client, monkeypatch):
    profile_id = create_profile(client)
    upload_reference(client, profile_id)

    monkeypatch.setattr(
        image_service,
        "_call_openai",
        lambda *args, **kwargs: (b"fake", {}),
    )

    response = client.post(
        f"/api/v1/profiles/{profile_id}/generations",
        json={
            "mode": "change_item",
            "instruction": "Change only the shoes",
            "tier": "preview",
        },
    )
    assert response.status_code == 422

    balance = client.get(f"/api/v1/profiles/{profile_id}/balance")
    assert balance.json()["free_tries"] == 3


def test_prompt_locks_identity():
    prompt = image_service.build_prompt(
        {
            "style_goal": "look younger but strict",
            "preferences": {
                "avoid_items": ["jeans"],
                "preferred_items": ["suits"],
                "colors": ["navy"],
                "preserve_identity": True,
            },
        },
        image_service.GenerationMode.outfit,
        "Try a navy suit",
    )

    assert "Preserve the user's exact identity" in prompt
    assert "Do not use: jeans" in prompt
    assert "Try a navy suit" in prompt


def test_openai_image_edit_parameters_are_supported():
    import inspect

    from openai import OpenAI

    client = OpenAI(api_key="test")
    params = inspect.signature(client.images.edit).parameters
    for name in [
        "model",
        "image",
        "prompt",
        "size",
        "quality",
        "output_format",
        "output_compression",
        "user",
    ]:
        assert name in params


def test_mcp_server_imports():
    import mcp_server

    assert mcp_server.server is not None


def test_update_preferences_api(client):
    profile_id = create_profile(client)

    response = client.patch(
        f"/api/v1/profiles/{profile_id}",
        json={
            "style_goal": "strict but younger",
            "preferences": {
                "avoid_items": ["jeans"],
                "preferred_items": ["suits", "hooded coats"],
                "colors": ["navy", "charcoal"],
                "notes": "Keep the face unchanged",
                "preserve_identity": True,
            },
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["style_goal"] == "strict but younger"
    assert data["preferences"]["avoid_items"] == ["jeans"]
    assert data["preferences"]["preserve_identity"] is True


def test_mcp_file_param_schema():
    import asyncio

    import mcp_server

    tools = asyncio.run(mcp_server.server.list_tools())
    upload_tool = next(tool for tool in tools if tool.name == "add_reference_photos")

    assert upload_tool.meta["openai/fileParams"] == ["files"]

    schema = upload_tool.input_schema
    file_schema = schema["properties"]["files"]["items"]
    if "$ref" in file_schema:
        ref_name = file_schema["$ref"].split("/")[-1]
        file_schema = schema["$defs"][ref_name]

    properties = file_schema["properties"]
    required = set(file_schema["required"])

    assert {"download_url", "file_id", "mime_type", "file_name"} <= set(properties)
    assert required == {"download_url", "file_id"}


def test_mcp_server_has_workflow_instructions():
    import mcp_server

    assert mcp_server.server.instructions
    assert "add_reference_photos" in mcp_server.server.instructions
    assert "get_generated_image" in mcp_server.server.instructions


def test_admin_credit_grant(client, monkeypatch):
    profile_id = create_profile(client)
    monkeypatch.setattr(settings, "admin_api_key", "secret-test-key")

    unauthorized = client.post(
        f"/api/v1/admin/profiles/{profile_id}/credits",
        json={"amount": 10, "reason": "test"},
    )
    assert unauthorized.status_code == 401

    granted = client.post(
        f"/api/v1/admin/profiles/{profile_id}/credits",
        headers={"X-Admin-Key": "secret-test-key"},
        json={"amount": 10, "reason": "test"},
    )
    assert granted.status_code == 201
    assert granted.json()["amount"] == 10

    balance = client.get(f"/api/v1/profiles/{profile_id}/balance")
    assert balance.status_code == 200
    assert balance.json()["paid_credits"] == 10
    assert balance.json()["total_available"] == 13


def test_invalid_reference_image_is_rejected(client):
    profile_id = create_profile(client)
    response = client.post(
        f"/api/v1/profiles/{profile_id}/photos",
        files={"photo": ("fake.jpg", b"not-an-image", "image/jpeg")},
    )
    assert response.status_code == 400


def test_generation_idempotency_prevents_double_charge(client, monkeypatch):
    profile_id = create_profile(client)
    upload_reference(client, profile_id)

    calls = {"count": 0}

    def fake_call(*args, **kwargs):
        calls["count"] += 1
        return b"fake-webp-image", {"image_tokens": 50}

    monkeypatch.setattr(image_service, "_call_openai", fake_call)

    payload = {
        "mode": "outfit",
        "instruction": "Charcoal suit",
        "tier": "preview",
        "idempotency_key": "request-00000001",
    }

    first = client.post(
        f"/api/v1/profiles/{profile_id}/generations",
        json=payload,
    )
    second = client.post(
        f"/api/v1/profiles/{profile_id}/generations",
        json=payload,
    )

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["id"] == second.json()["id"]
    assert calls["count"] == 1

    balance = client.get(f"/api/v1/profiles/{profile_id}/balance").json()
    assert balance["free_tries"] == 2


def test_generated_result_can_be_deleted(client, monkeypatch):
    profile_id = create_profile(client)
    upload_reference(client, profile_id)
    monkeypatch.setattr(
        image_service,
        "_call_openai",
        lambda *args, **kwargs: (b"fake-webp-image", {}),
    )

    result = client.post(
        f"/api/v1/profiles/{profile_id}/generations",
        json={
            "mode": "outfit",
            "instruction": "Navy suit",
            "tier": "preview",
        },
    )
    assert result.status_code == 201
    generation_id = result.json()["id"]

    deleted = client.delete(
        f"/api/v1/profiles/{profile_id}/generations/{generation_id}"
    )
    assert deleted.status_code == 204

    missing = client.get(
        f"/api/v1/profiles/{profile_id}/generations/{generation_id}"
    )
    assert missing.status_code == 422


def test_admin_stats(client, monkeypatch):
    monkeypatch.setattr(settings, "admin_api_key", "secret-test-key")
    profile_id = create_profile(client)

    grant = client.post(
        f"/api/v1/admin/profiles/{profile_id}/credits",
        headers={"X-Admin-Key": "secret-test-key"},
        json={"amount": 7, "reason": "stats test"},
    )
    assert grant.status_code == 201

    stats = client.get(
        "/api/v1/admin/stats",
        headers={"X-Admin-Key": "secret-test-key"},
    )
    assert stats.status_code == 200
    data = stats.json()
    assert data["profiles"] >= 1
    assert data["paid_credits_granted"] >= 7
    assert data["paid_credits_remaining"] >= 7


def test_reference_photo_role_is_stored(client):
    profile_id = create_profile(client)

    response = client.post(
        f"/api/v1/profiles/{profile_id}/photos",
        data={"role": "front"},
        files={"photo": ("front.jpg", make_image_bytes(), "image/jpeg")},
    )
    assert response.status_code == 201
    assert response.json()["role"] == "front"

    listed = client.get(f"/api/v1/profiles/{profile_id}/photos")
    assert listed.status_code == 200
    assert listed.json()[0]["role"] == "front"


def test_admin_usage_stats(client, monkeypatch):
    monkeypatch.setattr(settings, "admin_api_key", "secret-test-key")
    monkeypatch.setattr(settings, "usd_kzt_rate", 443.0)
    profile_id = create_profile(client)
    upload_reference(client, profile_id)

    monkeypatch.setattr(
        image_service,
        "_call_openai",
        lambda *args, **kwargs: (
            b"fake-webp-image",
            {
                "input_tokens": 120,
                "input_tokens_details": {
                    "image_tokens": 100,
                    "text_tokens": 20,
                },
                "output_tokens": 300,
                "output_tokens_details": {
                    "image_tokens": 300,
                    "text_tokens": 0,
                },
                "total_tokens": 420,
            },
        ),
    )

    generated = client.post(
        f"/api/v1/profiles/{profile_id}/generations",
        json={
            "mode": "outfit",
            "instruction": "Navy suit",
            "tier": "preview",
        },
    )
    assert generated.status_code == 201
    assert generated.json()["duration_ms"] is not None

    usage = client.get(
        "/api/v1/admin/usage",
        headers={"X-Admin-Key": "secret-test-key"},
    )
    assert usage.status_code == 200
    items = usage.json()["items"]
    assert items
    preview = next(item for item in items if item["tier"] == "preview")
    assert preview["input_tokens"] >= 120
    assert preview["input_image_tokens"] >= 100
    assert preview["output_tokens"] >= 300
    assert preview["total_tokens"] >= 420
    assert preview["estimated_cost_usd"] > 0
    assert preview["estimated_cost_kzt"] > 0
    assert preview["avg_cost_per_generation_usd"] > 0


def test_readiness_reports_provider_configuration(client, monkeypatch):
    monkeypatch.setattr(settings, "openai_api_key", None)
    not_ready = client.get("/ready")
    assert not_ready.status_code == 503
    assert not_ready.json()["database"] == "ready"
    assert not_ready.json()["image_provider"] == "not_configured"

    monkeypatch.setattr(settings, "openai_api_key", "test-key")
    ready = client.get("/ready")
    assert ready.status_code == 200
    assert ready.json()["status"] == "ready"


def test_mcp_tools_have_safety_annotations():
    import asyncio

    import mcp_server

    tools = asyncio.run(mcp_server.server.list_tools())
    by_name = {tool.name: tool for tool in tools}

    assert by_name["get_style_profile"].annotations.read_only_hint is True
    assert by_name["get_style_balance"].annotations.read_only_hint is True
    assert by_name["try_outfit"].annotations.read_only_hint is False
    assert by_name["try_outfit"].annotations.destructive_hint is False
    assert by_name["update_style_preferences"].annotations.idempotent_hint is True


def test_mcp_rejects_private_file_download_urls():
    import mcp_server

    with pytest.raises(ValueError):
        mcp_server._validate_public_https_url("http://example.com/file.jpg")

    with pytest.raises(ValueError):
        mcp_server._validate_public_https_url("https://127.0.0.1/file.jpg")


def test_processing_generation_blocks_parallel_charge(client, monkeypatch):
    from app.db import connection, utc_now

    profile_id = create_profile(client)
    upload_reference(client, profile_id)

    with connection() as conn:
        conn.execute(
            """
            INSERT INTO generations (
                id, profile_id, mode, tier, instruction, prompt, model,
                quality, size, status, cost_credits, charged_free,
                charged_paid, created_at
            )
            VALUES (?, ?, 'outfit', 'preview', 'existing', 'existing',
                    'test-model', 'medium', '1024x1536', 'processing',
                    1, 0, 0, ?)
            """,
            ("busy-generation", profile_id, utc_now()),
        )

    monkeypatch.setattr(
        image_service,
        "_call_openai",
        lambda *args, **kwargs: (b"fake", {}),
    )

    before = client.get(f"/api/v1/profiles/{profile_id}/balance").json()
    response = client.post(
        f"/api/v1/profiles/{profile_id}/generations",
        json={
            "mode": "outfit",
            "instruction": "Second outfit",
            "tier": "preview",
        },
    )
    after = client.get(f"/api/v1/profiles/{profile_id}/balance").json()

    assert response.status_code == 409
    assert after == before


def test_stale_processing_generation_is_refunded(client):
    from app.db import connection

    profile_id = create_profile(client)

    with connection() as conn:
        conn.execute(
            "UPDATE profiles SET free_tries = 2 WHERE id = ?",
            (profile_id,),
        )
        conn.execute(
            """
            INSERT INTO generations (
                id, profile_id, mode, tier, instruction, prompt, model,
                quality, size, status, cost_credits, charged_free,
                charged_paid, created_at
            )
            VALUES (?, ?, 'outfit', 'preview', 'stale', 'stale',
                    'test-model', 'medium', '1024x1536', 'processing',
                    1, 1, 0, '2000-01-01T00:00:00+00:00')
            """,
            ("stale-generation", profile_id),
        )

    recovered = image_service.recover_stale_generations()
    assert recovered == 1

    balance = client.get(f"/api/v1/profiles/{profile_id}/balance").json()
    assert balance["free_tries"] == 3

    generation = client.get(
        f"/api/v1/profiles/{profile_id}/generations/stale-generation"
    )
    assert generation.status_code == 200
    assert generation.json()["status"] == "failed"


def test_paid_credit_failure_creates_charge_and_refund_ledger(client, monkeypatch):
    from app.db import connection

    monkeypatch.setattr(settings, "admin_api_key", "secret-test-key")
    profile_id = create_profile(client)
    upload_reference(client, profile_id)

    with connection() as conn:
        conn.execute(
            "UPDATE profiles SET free_tries = 0 WHERE id = ?",
            (profile_id,),
        )

    grant = client.post(
        f"/api/v1/admin/profiles/{profile_id}/credits",
        headers={"X-Admin-Key": "secret-test-key"},
        json={"amount": 5, "reason": "paid ledger test"},
    )
    assert grant.status_code == 201

    monkeypatch.setattr(
        image_service,
        "_call_openai",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            ImageGenerationFailed("provider failed")
        ),
    )

    failed = client.post(
        f"/api/v1/profiles/{profile_id}/generations",
        json={
            "mode": "outfit",
            "instruction": "Paid generation",
            "tier": "preview",
        },
    )
    assert failed.status_code == 502

    balance = client.get(f"/api/v1/profiles/{profile_id}/balance").json()
    assert balance["paid_credits"] == 5

    stats = client.get(
        "/api/v1/admin/stats",
        headers={"X-Admin-Key": "secret-test-key"},
    ).json()
    assert stats["paid_credits_charged"] >= 1
    assert stats["paid_credits_refunded"] >= 1


def test_portable_plugin_manifest_and_skill_metadata():
    import json
    from pathlib import Path

    manifest = json.loads(Path("plugin.json").read_text(encoding="utf-8"))
    assert manifest["name"] == "ai-stylist"
    assert manifest["$schema"].endswith("/plugin.schema.json")

    skill = Path("skills/ai-stylist/SKILL.md").read_text(encoding="utf-8")
    assert skill.startswith("---\n")
    assert "name: ai-stylist" in skill
    assert "description:" in skill


def test_plugin_mcp_url_generator():
    from scripts.configure_plugin import normalize_mcp_url

    assert (
        normalize_mcp_url("https://stylist.example.com")
        == "https://stylist.example.com/mcp"
    )
    assert (
        normalize_mcp_url("https://stylist.example.com/mcp")
        == "https://stylist.example.com/mcp"
    )

    with pytest.raises(ValueError):
        normalize_mcp_url("http://stylist.example.com/mcp")

    with pytest.raises(ValueError):
        normalize_mcp_url("https://stylist.example.com/not-mcp")


def test_oauth_is_optional_for_closed_beta(monkeypatch):
    from app import auth

    monkeypatch.setattr(settings, "mcp_auth_enabled", False)
    assert auth.mcp_auth_kwargs() == {}


def test_oauth_configuration_validation(monkeypatch):
    from app import auth

    monkeypatch.setattr(settings, "mcp_auth_enabled", True)
    monkeypatch.setattr(settings, "oauth_issuer_url", None)
    monkeypatch.setattr(settings, "oauth_jwks_url", None)
    monkeypatch.setattr(settings, "mcp_resource_url", None)

    with pytest.raises(RuntimeError):
        auth.mcp_auth_kwargs()

    monkeypatch.setattr(settings, "oauth_issuer_url", "https://auth.example.com")
    monkeypatch.setattr(
        settings,
        "oauth_jwks_url",
        "https://auth.example.com/.well-known/jwks.json",
    )
    monkeypatch.setattr(
        settings,
        "mcp_resource_url",
        "https://stylist.example.com/mcp",
    )
    configured = auth.mcp_auth_kwargs()
    assert configured["token_verifier"] is not None
    assert configured["auth"].required_scopes == ["stylist"]


def test_mcp_profile_ownership_is_enforced(client, monkeypatch):
    import mcp_server
    from app.db import connection

    profile_id = create_profile(client)
    with connection() as conn:
        conn.execute(
            "UPDATE profiles SET owner_subject = ? WHERE id = ?",
            ("user-a", profile_id),
        )

    monkeypatch.setattr(settings, "mcp_auth_enabled", True)
    monkeypatch.setattr(mcp_server, "current_subject", lambda: "user-a")
    mcp_server._ensure_profile_access(profile_id)

    monkeypatch.setattr(mcp_server, "current_subject", lambda: "user-b")
    with pytest.raises(ValueError, match="Profile not found"):
        mcp_server._ensure_profile_access(profile_id)


def test_reference_readiness_reports_missing_views(client):
    profile_id = create_profile(client)

    empty = client.get(f"/api/v1/profiles/{profile_id}/readiness")
    assert empty.status_code == 200
    assert empty.json()["ready_for_try_on"] is False

    front = client.post(
        f"/api/v1/profiles/{profile_id}/photos",
        data={"role": "front"},
        files={"photo": ("front.jpg", make_image_bytes(), "image/jpeg")},
    )
    assert front.status_code == 201

    one_view = client.get(f"/api/v1/profiles/{profile_id}/readiness").json()
    assert one_view["ready_for_try_on"] is True
    assert one_view["outfit_ready"] is False
    assert one_view["haircut_ready"] is False
    assert "full_body" in one_view["missing_recommended_roles"]

    body = client.post(
        f"/api/v1/profiles/{profile_id}/photos",
        data={"role": "full_body"},
        files={"photo": ("body.jpg", make_image_bytes(), "image/jpeg")},
    )
    assert body.status_code == 201

    outfit = client.get(f"/api/v1/profiles/{profile_id}/readiness").json()
    assert outfit["outfit_ready"] is True


def test_reference_role_priority_depends_on_generation_mode():
    haircut = image_service._reference_role_priority(
        image_service.GenerationMode.haircut
    )
    outfit = image_service._reference_role_priority(
        image_service.GenerationMode.outfit
    )

    assert haircut["side"] < haircut["full_body"]
    assert outfit["full_body"] < outfit["side"]
