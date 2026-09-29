from fastapi.testclient import TestClient
import pytest

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


def upload_reference(client: TestClient, profile_id: str) -> None:
    response = client.post(
        f"/api/v1/profiles/{profile_id}/photos",
        files={"photo": ("reference.jpg", b"fake-image-bytes", "image/jpeg")},
    )
    assert response.status_code == 201


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
