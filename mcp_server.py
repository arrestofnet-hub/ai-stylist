from mcp.server.fastmcp import FastMCP

from app.config import settings
from app.routes.profiles import create_profile, get_balance, get_profile
from app.schemas import (
    GenerationMode,
    GenerationTier,
    ProfileCreate,
    StylePreferences,
)
from app.services.image_service import (
    StylistError,
    generate_style_image,
    list_generations,
)

server = FastMCP(
    "ai-stylist",
    host=settings.host,
    port=settings.mcp_port,
    stateless_http=True,
)


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
    """Create an AI Stylist profile. New profiles receive three free preview credits."""
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


@server.tool()
def get_style_profile(profile_id: str) -> dict:
    """Get a saved AI Stylist profile and its current reference-photo count."""
    return get_profile(profile_id).model_dump(mode="json")


@server.tool()
def get_style_balance(profile_id: str) -> dict:
    """Get free tries and paid-credit balance for an AI Stylist profile."""
    return get_balance(profile_id).model_dump(mode="json")


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


if __name__ == "__main__":
    server.run(transport="streamable-http")
