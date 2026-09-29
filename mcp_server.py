from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

import httpx
from mcp.server import MCPServer
from mcp.server.mcpserver import Image
from pydantic import BaseModel

from app.config import settings
from app.db import connection, utc_now
from app.routes.profiles import create_profile, get_balance, get_profile, update_profile
from app.schemas import (
    GenerationMode,
    GenerationTier,
    ProfileCreate,
    ProfileUpdate,
    ReferencePhotoRole,
    StylePreferences,
)
from app.services.image_utils import InvalidImageError, sanitize_image_bytes
from app.services.image_service import (
    StylistError,
    generate_style_image,
    get_generation,
    list_generations,
)

server = MCPServer(
    "AI Stylist",
    title="AI Stylist",
    description="Personal virtual stylist for clothing and haircut try-ons.",
    version="0.5.0",
    instructions=(
        "Use create_style_profile first when a user has no profile. "
        "Then use add_reference_photos for the user's real photos. "
        "Never generate a try-on before at least one reference photo exists. "
        "Use try_outfit for clothing, try_haircut for hair only, create_full_look "
        "for coordinated changes, and change_one_item when the user wants a precise "
        "edit of an existing result. Preserve the user's identity unless they "
        "explicitly request an identity-changing edit. After a generation succeeds, "
        "use get_generated_image so the actual image is returned to the user."
    ),
)

ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}
MAX_UPLOAD_BYTES = 15 * 1024 * 1024
MAX_REFERENCE_PHOTOS = 5


class OpenAIFile(BaseModel):
    download_url: str
    file_id: str
    mime_type: str | None = None
    file_name: str | None = None


def _image_url(profile_id: str, generation_id: str) -> str:
    base = settings.public_base_url.rstrip("/")
    return (
        f"{base}/api/v1/profiles/{profile_id}/generations/"
        f"{generation_id}/image"
    )


def _generation_payload(row: dict) -> dict:
    result = {
        "id": row["id"],
        "profile_id": row["profile_id"],
        "mode": row["mode"],
        "tier": row["tier"],
        "status": row["status"],
        "model": row["model"],
        "quality": row["quality"],
        "cost_credits": int(row["cost_credits"]),
        "charged_free": int(row["charged_free"]),
        "charged_paid": int(row["charged_paid"]),
        "created_at": row["created_at"],
        "completed_at": row.get("completed_at"),
    }
    if row.get("status") == "completed":
        result["image_url"] = _image_url(row["profile_id"], row["id"])
    if row.get("error_message"):
        result["error_message"] = row["error_message"]
    return result


def _ensure_profile_exists(profile_id: str) -> None:
    with connection() as conn:
        row = conn.execute(
            "SELECT id FROM profiles WHERE id = ?",
            (profile_id,),
        ).fetchone()
    if row is None:
        raise ValueError("Profile not found")


def _photo_count(profile_id: str) -> int:
    with connection() as conn:
        return int(
            conn.execute(
                "SELECT COUNT(*) AS n FROM reference_photos WHERE profile_id = ?",
                (profile_id,),
            ).fetchone()["n"]
        )


async def _download_openai_file(
    profile_id: str,
    file: OpenAIFile,
    role: ReferencePhotoRole = ReferencePhotoRole.other,
) -> dict:
    parsed = urlparse(file.download_url)
    if parsed.scheme != "https":
        raise ValueError("File download URL must use HTTPS")

    mime_type = (file.mime_type or "").lower()
    if mime_type and mime_type not in ALLOWED_IMAGE_TYPES:
        raise ValueError("Only JPEG, PNG and WEBP reference photos are supported")

    if _photo_count(profile_id) >= MAX_REFERENCE_PHOTOS:
        raise ValueError(
            f"Maximum {MAX_REFERENCE_PHOTOS} reference photos allowed"
        )

    timeout = httpx.Timeout(60.0, connect=15.0)
    raw = bytearray()

    async with httpx.AsyncClient(
        follow_redirects=True,
        timeout=timeout,
        headers={"User-Agent": "AI-Stylist/0.5"},
    ) as client:
        async with client.stream("GET", file.download_url) as response:
            response.raise_for_status()

            response_type = (
                response.headers.get("content-type", "")
                .split(";", 1)[0]
                .strip()
                .lower()
            )
            effective_type = mime_type or response_type
            if effective_type not in ALLOWED_IMAGE_TYPES:
                raise ValueError(
                    "Downloaded file is not a supported JPEG, PNG or WEBP image"
                )

            async for chunk in response.aiter_bytes(1024 * 1024):
                raw.extend(chunk)
                if len(raw) > MAX_UPLOAD_BYTES:
                    raise ValueError(
                        "Reference photo is too large (max 15 MB)"
                    )

    try:
        sanitized, stored_type = sanitize_image_bytes(
            bytes(raw),
            output_format="WEBP",
            quality=95,
        )
    except InvalidImageError as exc:
        raise ValueError(str(exc)) from exc

    photo_id = str(uuid4())
    profile_dir = Path(settings.upload_dir) / profile_id
    profile_dir.mkdir(parents=True, exist_ok=True)
    destination = profile_dir / f"{photo_id}.webp"
    destination.write_bytes(sanitized)

    now = utc_now()
    try:
        with connection() as conn:
            conn.execute(
                """
                INSERT INTO reference_photos (
                    id, profile_id, file_path, original_name, mime_type, role, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    photo_id,
                    profile_id,
                    str(destination),
                    file.file_name,
                    stored_type,
                    role.value,
                    now,
                ),
            )
    except Exception:
        destination.unlink(missing_ok=True)
        raise

    return {
        "id": photo_id,
        "profile_id": profile_id,
        "file_id": file.file_id,
        "file_name": file.file_name,
        "mime_type": stored_type,
        "role": role.value,
        "created_at": now,
    }


@server.tool()
def create_style_profile(
    display_name: str | None = None,
    age_range: str | None = None,
    gender: str | None = None,
    height_cm: int | None = None,
    weight_kg: float | None = None,
    style_goal: str | None = None,
    preferred_items: list[str] | None = None,
    avoid_items: list[str] | None = None,
    colors: list[str] | None = None,
    notes: str | None = None,
) -> dict:
    """Create an AI Stylist profile. New profiles receive free preview credits."""
    payload = ProfileCreate(
        display_name=display_name,
        age_range=age_range,
        gender=gender,
        height_cm=height_cm,
        weight_kg=weight_kg,
        style_goal=style_goal,
        preferences=StylePreferences(
            preferred_items=preferred_items or [],
            avoid_items=avoid_items or [],
            colors=colors or [],
            notes=notes,
            preserve_identity=True,
        ),
    )
    return create_profile(payload).model_dump(mode="json")


@server.tool(
    meta={"openai/fileParams": ["files"]},
)
async def add_reference_photos(
    profile_id: str,
    files: list[OpenAIFile],
    roles: list[ReferencePhotoRole] | None = None,
) -> list[dict]:
    """Attach 1-5 user-selected reference photos to an AI Stylist profile."""
    _ensure_profile_exists(profile_id)

    if not files:
        raise ValueError("At least one reference photo is required")
    if len(files) > MAX_REFERENCE_PHOTOS:
        raise ValueError(
            f"Upload no more than {MAX_REFERENCE_PHOTOS} photos at once"
        )

    remaining = MAX_REFERENCE_PHOTOS - _photo_count(profile_id)
    if len(files) > remaining:
        raise ValueError(
            f"This profile can accept only {remaining} more reference photos"
        )

    if roles is not None and len(roles) != len(files):
        raise ValueError("roles must have the same number of items as files")

    effective_roles = roles or [ReferencePhotoRole.other] * len(files)

    saved: list[dict] = []
    for file, role in zip(files, effective_roles):
        saved.append(await _download_openai_file(profile_id, file, role))
    return saved


@server.tool()
def get_style_profile(profile_id: str) -> dict:
    """Get a saved AI Stylist profile and its current reference-photo count."""
    return get_profile(profile_id).model_dump(mode="json")


@server.tool()
def get_style_balance(profile_id: str) -> dict:
    """Get free tries and paid-credit balance for an AI Stylist profile."""
    return get_balance(profile_id).model_dump(mode="json")


@server.tool()
def update_style_preferences(
    profile_id: str,
    style_goal: str | None = None,
    preferred_items: list[str] | None = None,
    avoid_items: list[str] | None = None,
    colors: list[str] | None = None,
    notes: str | None = None,
) -> dict:
    """Update what the stylist should remember about the user's clothing and style preferences."""
    current = get_profile(profile_id)

    current_preferences = current.preferences.model_dump()
    if preferred_items is not None:
        current_preferences["preferred_items"] = preferred_items
    if avoid_items is not None:
        current_preferences["avoid_items"] = avoid_items
    if colors is not None:
        current_preferences["colors"] = colors
    if notes is not None:
        current_preferences["notes"] = notes
    current_preferences["preserve_identity"] = True

    payload = ProfileUpdate(
        style_goal=style_goal if style_goal is not None else current.style_goal,
        preferences=StylePreferences(**current_preferences),
    )
    return update_profile(profile_id, payload).model_dump(mode="json")


def _run_generation(
    profile_id: str,
    mode: GenerationMode,
    instruction: str,
    tier: str,
    base_generation_id: str | None = None,
) -> dict:
    try:
        generation_tier = GenerationTier(tier)
    except ValueError as exc:
        raise ValueError("tier must be preview or final") from exc

    try:
        row = generate_style_image(
            profile_id=profile_id,
            mode=mode,
            instruction=instruction,
            tier=generation_tier,
            base_generation_id=base_generation_id,
        )
    except StylistError as exc:
        raise ValueError(str(exc)) from exc

    return _generation_payload(row)


@server.tool()
def try_outfit(
    profile_id: str,
    instruction: str,
    tier: str = "preview",
) -> dict:
    """Dress the saved person in a requested outfit while preserving identity."""
    return _run_generation(
        profile_id=profile_id,
        mode=GenerationMode.outfit,
        instruction=instruction,
        tier=tier,
    )


@server.tool()
def try_haircut(
    profile_id: str,
    instruction: str,
    tier: str = "preview",
) -> dict:
    """Change only the hairstyle while preserving the person's exact face and identity."""
    return _run_generation(
        profile_id=profile_id,
        mode=GenerationMode.haircut,
        instruction=instruction,
        tier=tier,
    )


@server.tool()
def create_full_look(
    profile_id: str,
    instruction: str,
    tier: str = "preview",
) -> dict:
    """Create a complete requested look using the saved person and preferences."""
    return _run_generation(
        profile_id=profile_id,
        mode=GenerationMode.full_look,
        instruction=instruction,
        tier=tier,
    )


@server.tool()
def change_one_item(
    profile_id: str,
    base_generation_id: str,
    instruction: str,
    tier: str = "preview",
) -> dict:
    """Edit one item in an existing generated look and preserve everything else."""
    return _run_generation(
        profile_id=profile_id,
        mode=GenerationMode.change_item,
        instruction=instruction,
        tier=tier,
        base_generation_id=base_generation_id,
    )


@server.tool()
def recent_style_generations(profile_id: str, limit: int = 10) -> list[dict]:
    """Return recent generated looks for a profile."""
    try:
        rows = list_generations(profile_id, limit=limit)
    except StylistError as exc:
        raise ValueError(str(exc)) from exc
    return [_generation_payload(row) for row in rows]


@server.tool(structured_output=False)
def get_generated_image(
    profile_id: str,
    generation_id: str,
) -> Image:
    """Return a completed generated look as an actual image content block."""
    try:
        row = get_generation(profile_id, generation_id)
    except StylistError as exc:
        raise ValueError(str(exc)) from exc

    if row.get("status") != "completed" or not row.get("output_path"):
        raise ValueError("Generation is not completed")

    path = Path(row["output_path"])
    if not path.exists():
        raise ValueError("Generated image file is unavailable")
    return Image(path=path)


if __name__ == "__main__":
    server.run(
        transport="streamable-http",
        host=settings.host,
        port=settings.mcp_port,
        streamable_http_path="/mcp",
        stateless_http=True,
        json_response=True,
    )
