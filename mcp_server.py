import hashlib
import ipaddress
import socket
from pathlib import Path
from urllib.parse import urljoin, urlparse
from uuid import uuid4

import httpx
from mcp.server import MCPServer
from mcp.server.mcpserver import Image
from mcp.types import ToolAnnotations
from pydantic import BaseModel

from app.auth import current_subject, mcp_auth_kwargs
from app.config import settings
from app.db import connection, utc_now
from app.routes.profiles import (
    create_profile_record,
    get_balance,
    get_profile,
    get_profile_readiness,
    update_profile,
)
from app.schemas import (
    GenerationMode,
    GenerationTier,
    ProfileCreate,
    ProfileUpdate,
    ReferencePhotoRole,
    StylePreferences,
)
from app.services.image_service import (
    StylistError,
    generate_style_image,
    get_generation,
    list_generations,
)
from app.services.image_utils import InvalidImageError, sanitize_image_bytes

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
    **mcp_auth_kwargs(),
)

ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}
MAX_UPLOAD_BYTES = 15 * 1024 * 1024
MAX_REFERENCE_PHOTOS = 5
MAX_DOWNLOAD_REDIRECTS = 4


def _validate_public_https_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("File download URL must use public HTTPS")

    host = parsed.hostname.lower()
    if host in {"localhost"} or host.endswith((".localhost", ".local")):
        raise ValueError("Private file download hosts are not allowed")

    try:
        addresses = socket.getaddrinfo(host, parsed.port or 443, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ValueError("Could not resolve file download host") from exc

    if not addresses:
        raise ValueError("Could not resolve file download host")

    for address in addresses:
        ip = ipaddress.ip_address(address[4][0])
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            raise ValueError("Private or unsafe file download address is not allowed")


class OpenAIFile(BaseModel):
    download_url: str
    file_id: str
    mime_type: str | None = None
    file_name: str | None = None


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
    result["image_available"] = bool(
        row.get("status") == "completed" and row.get("output_path")
    )
    if row.get("error_message"):
        result["error_message"] = row["error_message"]
    return result


def _ensure_profile_access(profile_id: str) -> None:
    with connection() as conn:
        row = conn.execute(
            "SELECT id, owner_subject FROM profiles WHERE id = ?",
            (profile_id,),
        ).fetchone()

    if row is None:
        raise ValueError("Profile not found")

    if settings.mcp_auth_enabled:
        subject = current_subject()
        if not subject or row["owner_subject"] != subject:
            # Do not reveal whether another user's profile exists.
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
    _validate_public_https_url(file.download_url)

    mime_type = (file.mime_type or "").lower()
    if mime_type and mime_type not in ALLOWED_IMAGE_TYPES:
        raise ValueError("Only JPEG, PNG and WEBP reference photos are supported")

    if _photo_count(profile_id) >= MAX_REFERENCE_PHOTOS:
        raise ValueError(
            f"Maximum {MAX_REFERENCE_PHOTOS} reference photos allowed"
        )

    timeout = httpx.Timeout(60.0, connect=15.0)
    raw = bytearray()
    current_url = file.download_url
    effective_type = mime_type

    async with httpx.AsyncClient(
        follow_redirects=False,
        timeout=timeout,
        headers={"User-Agent": "AI-Stylist/0.5"},
    ) as client:
        for _ in range(MAX_DOWNLOAD_REDIRECTS + 1):
            _validate_public_https_url(current_url)

            async with client.stream("GET", current_url) as response:
                if response.status_code in {301, 302, 303, 307, 308}:
                    location = response.headers.get("location")
                    if not location:
                        raise ValueError("File download redirect has no location")
                    current_url = urljoin(current_url, location)
                    continue

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
                break
        else:
            raise ValueError("Too many redirects while downloading reference photo")

    try:
        sanitized, stored_type = sanitize_image_bytes(
            bytes(raw),
            output_format="WEBP",
            quality=95,
        )
    except InvalidImageError as exc:
        raise ValueError(str(exc)) from exc

    content_hash = hashlib.sha256(sanitized).hexdigest()

    with connection() as conn:
        duplicate = conn.execute(
            """
            SELECT id FROM reference_photos
            WHERE profile_id = ? AND sha256 = ?
            """,
            (profile_id, content_hash),
        ).fetchone()
    if duplicate is not None:
        raise ValueError("This reference photo is already saved")

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
                    id, profile_id, file_path, original_name, mime_type,
                    role, sha256, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    photo_id,
                    profile_id,
                    str(destination),
                    file.file_name,
                    stored_type,
                    role.value,
                    content_hash,
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


@server.tool(
    title="Create style profile",
    annotations=ToolAnnotations(
        read_only_hint=False,
        destructive_hint=False,
        idempotent_hint=False,
        open_world_hint=False,
    ),
)
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
    return create_profile_record(
        payload,
        owner_subject=current_subject(),
    ).model_dump(mode="json")


@server.tool(
    title="Add reference photos",
    annotations=ToolAnnotations(
        read_only_hint=False,
        destructive_hint=False,
        idempotent_hint=False,
        open_world_hint=True,
    ),
    meta={
        "openai/fileParams": ["files"],
        "openai/toolInvocation/invoking": "Adding reference photos…",
        "openai/toolInvocation/invoked": "Reference photos added",
    },
)
async def add_reference_photos(
    profile_id: str,
    files: list[OpenAIFile],
    roles: list[ReferencePhotoRole] | None = None,
) -> list[dict]:
    """Attach 1-5 user-selected reference photos to an AI Stylist profile."""
    _ensure_profile_access(profile_id)

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


@server.tool(
    title="Get style profile",
    annotations=ToolAnnotations(
        read_only_hint=True,
        open_world_hint=False,
    ),
)
def get_style_profile(profile_id: str) -> dict:
    """Get a saved AI Stylist profile and its current reference-photo count."""
    _ensure_profile_access(profile_id)
    return get_profile(profile_id).model_dump(mode="json")


@server.tool(
    title="Check reference readiness",
    annotations=ToolAnnotations(
        read_only_hint=True,
        open_world_hint=False,
    ),
)
def get_style_readiness(profile_id: str) -> dict:
    """Check whether saved reference photos are sufficient for outfit and haircut try-ons."""
    _ensure_profile_access(profile_id)
    return get_profile_readiness(profile_id).model_dump(mode="json")


@server.tool(
    title="Get style balance",
    annotations=ToolAnnotations(
        read_only_hint=True,
        open_world_hint=False,
    ),
)
def get_style_balance(profile_id: str) -> dict:
    """Get free tries and paid-credit balance for an AI Stylist profile."""
    _ensure_profile_access(profile_id)
    return get_balance(profile_id).model_dump(mode="json")


@server.tool(
    title="Update style preferences",
    annotations=ToolAnnotations(
        read_only_hint=False,
        destructive_hint=False,
        idempotent_hint=True,
        open_world_hint=False,
    ),
)
def update_style_preferences(
    profile_id: str,
    style_goal: str | None = None,
    preferred_items: list[str] | None = None,
    avoid_items: list[str] | None = None,
    colors: list[str] | None = None,
    notes: str | None = None,
) -> dict:
    """Update what the stylist should remember about the user's clothing and style preferences."""
    _ensure_profile_access(profile_id)
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
    _ensure_profile_access(profile_id)

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


@server.tool(
    title="Try an outfit",
    annotations=ToolAnnotations(
        read_only_hint=False,
        destructive_hint=False,
        idempotent_hint=False,
        open_world_hint=True,
    ),
    meta={
        "openai/toolInvocation/invoking": "Trying the outfit…",
        "openai/toolInvocation/invoked": "Outfit ready",
    },
)
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


@server.tool(
    title="Try a haircut",
    annotations=ToolAnnotations(
        read_only_hint=False,
        destructive_hint=False,
        idempotent_hint=False,
        open_world_hint=True,
    ),
    meta={
        "openai/toolInvocation/invoking": "Trying the haircut…",
        "openai/toolInvocation/invoked": "Haircut ready",
    },
)
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


@server.tool(
    title="Create a full look",
    annotations=ToolAnnotations(
        read_only_hint=False,
        destructive_hint=False,
        idempotent_hint=False,
        open_world_hint=True,
    ),
    meta={
        "openai/toolInvocation/invoking": "Building the full look…",
        "openai/toolInvocation/invoked": "Full look ready",
    },
)
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


@server.tool(
    title="Change one item",
    annotations=ToolAnnotations(
        read_only_hint=False,
        destructive_hint=False,
        idempotent_hint=False,
        open_world_hint=True,
    ),
    meta={
        "openai/toolInvocation/invoking": "Changing one item…",
        "openai/toolInvocation/invoked": "Edit ready",
    },
)
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


@server.tool(
    title="Recent style generations",
    annotations=ToolAnnotations(
        read_only_hint=True,
        open_world_hint=False,
    ),
)
def recent_style_generations(profile_id: str, limit: int = 10) -> list[dict]:
    """Return recent generated looks for a profile."""
    _ensure_profile_access(profile_id)

    try:
        rows = list_generations(profile_id, limit=limit)
    except StylistError as exc:
        raise ValueError(str(exc)) from exc
    return [_generation_payload(row) for row in rows]


@server.tool(
    title="Get generated image",
    structured_output=False,
    annotations=ToolAnnotations(
        read_only_hint=True,
        open_world_hint=False,
    ),
)
def get_generated_image(
    profile_id: str,
    generation_id: str,
) -> Image:
    """Return a completed generated look as an actual image content block."""
    _ensure_profile_access(profile_id)

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
