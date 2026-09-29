import base64
import json
from contextlib import ExitStack
from pathlib import Path
from uuid import uuid4

from openai import OpenAI

from app.config import settings
from app.db import connection, decode_preferences, utc_now
from app.schemas import GenerationMode, GenerationTier


class StylistError(Exception):
    pass


class ProfileNotFound(StylistError):
    pass


class MissingReferencePhotos(StylistError):
    pass


class InsufficientCredits(StylistError):
    pass


class InvalidGenerationRequest(StylistError):
    pass


class ImageProviderUnavailable(StylistError):
    pass


class ImageGenerationFailed(StylistError):
    pass


def _profile(profile_id: str) -> dict:
    with connection() as conn:
        row = conn.execute(
            "SELECT * FROM profiles WHERE id = ?",
            (profile_id,),
        ).fetchone()

    if row is None:
        raise ProfileNotFound("Profile not found")

    data = dict(row)
    data["preferences"] = decode_preferences(data.pop("preferences_json"))
    return data


def _reference_paths(
    profile_id: str,
    requested_ids: list[str] | None,
) -> list[Path]:
    with connection() as conn:
        if requested_ids:
            placeholders = ",".join("?" for _ in requested_ids)
            rows = conn.execute(
                f"""
                SELECT * FROM reference_photos
                WHERE profile_id = ? AND id IN ({placeholders})
                ORDER BY created_at ASC
                """,
                [profile_id, *requested_ids],
            ).fetchall()

            if len(rows) != len(set(requested_ids)):
                raise InvalidGenerationRequest(
                    "One or more reference photos do not belong to this profile"
                )
        else:
            rows = conn.execute(
                """
                SELECT * FROM reference_photos
                WHERE profile_id = ?
                ORDER BY created_at ASC
                LIMIT ?
                """,
                (profile_id, settings.max_reference_images),
            ).fetchall()

    paths = [Path(row["file_path"]) for row in rows]
    paths = [path for path in paths if path.exists()]
    return paths[: settings.max_reference_images]


def _base_generation_path(profile_id: str, generation_id: str) -> Path:
    with connection() as conn:
        row = conn.execute(
            """
            SELECT output_path, status FROM generations
            WHERE id = ? AND profile_id = ?
            """,
            (generation_id, profile_id),
        ).fetchone()

    if row is None:
        raise InvalidGenerationRequest("Base generation not found")
    if row["status"] != "completed" or not row["output_path"]:
        raise InvalidGenerationRequest("Base generation is not completed")

    path = Path(row["output_path"])
    if not path.exists():
        raise InvalidGenerationRequest("Base generation image is unavailable")
    return path


def _credit_cost(tier: GenerationTier) -> int:
    return 1 if tier == GenerationTier.preview else 4


def _model_and_quality(tier: GenerationTier) -> tuple[str, str]:
    if tier == GenerationTier.preview:
        return settings.preview_image_model, settings.preview_image_quality
    return settings.final_image_model, settings.final_image_quality


def _reserve_credits(profile_id: str, cost: int) -> tuple[int, int]:
    with connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT free_tries, paid_credits FROM profiles WHERE id = ?",
            (profile_id,),
        ).fetchone()

        if row is None:
            raise ProfileNotFound("Profile not found")

        free_available = int(row["free_tries"])
        paid_available = int(row["paid_credits"])
        free_used = min(free_available, cost)
        paid_used = cost - free_used

        if paid_used > paid_available:
            raise InsufficientCredits(
                f"Need {cost} credits, available {free_available + paid_available}"
            )

        conn.execute(
            """
            UPDATE profiles
            SET free_tries = free_tries - ?,
                paid_credits = paid_credits - ?,
                updated_at = ?
            WHERE id = ?
            """,
            (free_used, paid_used, utc_now(), profile_id),
        )

    return free_used, paid_used


def _refund_credits(profile_id: str, free_used: int, paid_used: int) -> None:
    with connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            """
            UPDATE profiles
            SET free_tries = free_tries + ?,
                paid_credits = paid_credits + ?,
                updated_at = ?
            WHERE id = ?
            """,
            (free_used, paid_used, utc_now(), profile_id),
        )


def _preferences_text(profile: dict) -> str:
    preferences = profile.get("preferences") or {}
    lines: list[str] = []

    if profile.get("style_goal"):
        lines.append(f"Style goal: {profile['style_goal']}.")

    preferred = preferences.get("preferred_items") or []
    avoided = preferences.get("avoid_items") or []
    colors = preferences.get("colors") or []
    notes = preferences.get("notes")

    if preferred:
        lines.append("Preferred items: " + ", ".join(preferred) + ".")
    if avoided:
        lines.append("Do not use: " + ", ".join(avoided) + ".")
    if colors:
        lines.append("Preferred colors: " + ", ".join(colors) + ".")
    if notes:
        lines.append("User notes: " + str(notes).strip())

    return "\n".join(lines)


def build_prompt(
    profile: dict,
    mode: GenerationMode,
    instruction: str,
) -> str:
    identity_lock = """
IDENTITY LOCK — highest priority:
The first person shown in the supplied reference photographs is the same real user.
Preserve the user's exact identity. Do not replace them with a generic model.
Keep facial geometry, eyes, eyebrows, nose, lips, ears, skin tone, apparent age,
head shape and recognizable likeness unchanged. Do not beautify, rejuvenate,
change ethnicity, change body shape, make the user taller or thinner, or alter
their proportions unless the user explicitly asks for that exact change.
Keep the result photorealistic and believable. No text or watermarks.
""".strip()

    if mode == GenerationMode.outfit:
        task = """
TASK:
Change only the clothing according to the user's instruction.
Preserve the face, hairstyle, body, pose and identity. Fit garments naturally
to the existing body geometry with realistic fabric folds, occlusion, lighting,
shadows and scale. Do not modify the user's anatomy.
""".strip()
    elif mode == GenerationMode.haircut:
        task = """
TASK:
Change only the hairstyle/haircut according to the user's instruction.
Preserve the exact face, facial features, skin, age, body, clothing, pose and
identity. The haircut must look realistic for the user's existing hairline,
hair density and head shape.
""".strip()
    elif mode == GenerationMode.change_item:
        task = """
TASK:
The first input image is the current look to edit.
Change only the single item or detail named by the user. Keep every other
visible element unchanged: identity, face, body, pose, hairstyle, other
clothing, camera angle, framing, background, lighting and image quality.
""".strip()
    elif mode == GenerationMode.full_look:
        task = """
TASK:
Create the requested complete look on this same person. You may change clothing,
shoes, accessories and hairstyle only when requested. Preserve the exact face,
skin tone, age, body shape, proportions and identity.
""".strip()
    else:
        raise InvalidGenerationRequest("Unsupported generation mode")

    profile_context = _preferences_text(profile)

    return (
        f"{identity_lock}\n\n"
        f"{task}\n\n"
        f"USER REQUEST:\n{instruction.strip()}\n\n"
        f"PROFILE PREFERENCES:\n{profile_context or 'No additional preferences.'}\n\n"
        "OUTPUT: one clean photorealistic image of this same person. "
        "Do not add captions, labels, logos, borders or watermarks."
    )


def _usage_to_dict(result: object) -> dict:
    usage = getattr(result, "usage", None)
    if usage is None:
        return {}
    if hasattr(usage, "model_dump"):
        return usage.model_dump()
    if isinstance(usage, dict):
        return usage
    return {"raw": str(usage)}


def _call_openai(
    profile_id: str,
    image_paths: list[Path],
    prompt: str,
    model: str,
    quality: str,
) -> tuple[bytes, dict]:
    if not settings.openai_api_key:
        raise ImageProviderUnavailable("OPENAI_API_KEY is not configured")

    client = OpenAI(api_key=settings.openai_api_key)

    try:
        with ExitStack() as stack:
            images = [
                stack.enter_context(path.open("rb"))
                for path in image_paths
            ]
            request = {
                "model": model,
                "image": images,
                "prompt": prompt,
                "size": settings.image_size,
                "quality": quality,
                "output_format": settings.image_output_format,
                "user": profile_id,
            }
            if settings.image_output_format.lower() in {"webp", "jpeg"}:
                request["output_compression"] = settings.image_output_compression

            result = client.images.edit(**request)
    except ImageProviderUnavailable:
        raise
    except Exception as exc:
        raise ImageGenerationFailed(str(exc)) from exc

    if not result.data or not result.data[0].b64_json:
        raise ImageGenerationFailed("Image provider returned no image")

    try:
        content = base64.b64decode(result.data[0].b64_json)
    except Exception as exc:
        raise ImageGenerationFailed("Could not decode generated image") from exc

    return content, _usage_to_dict(result)


def _generation_row(generation_id: str, profile_id: str) -> dict:
    with connection() as conn:
        row = conn.execute(
            """
            SELECT * FROM generations
            WHERE id = ? AND profile_id = ?
            """,
            (generation_id, profile_id),
        ).fetchone()

    if row is None:
        raise InvalidGenerationRequest("Generation not found")
    return dict(row)


def generate_style_image(
    profile_id: str,
    mode: GenerationMode,
    instruction: str,
    tier: GenerationTier = GenerationTier.preview,
    base_generation_id: str | None = None,
    reference_photo_ids: list[str] | None = None,
) -> dict:
    profile = _profile(profile_id)

    if mode == GenerationMode.change_item and not base_generation_id:
        raise InvalidGenerationRequest(
            "change_item requires base_generation_id"
        )

    reference_paths = _reference_paths(profile_id, reference_photo_ids)

    if mode == GenerationMode.change_item:
        base_path = _base_generation_path(profile_id, base_generation_id or "")
        image_paths = [base_path, *reference_paths]
    else:
        image_paths = reference_paths

    if not image_paths:
        raise MissingReferencePhotos(
            "Upload at least one reference photo before generating"
        )

    max_inputs = settings.max_reference_images + (
        1 if mode == GenerationMode.change_item else 0
    )
    image_paths = image_paths[:max_inputs]

    prompt = build_prompt(profile, mode, instruction)
    model, quality = _model_and_quality(tier)
    cost = _credit_cost(tier)
    free_used, paid_used = _reserve_credits(profile_id, cost)

    generation_id = str(uuid4())
    created_at = utc_now()

    try:
        with connection() as conn:
            conn.execute(
                """
                INSERT INTO generations (
                    id, profile_id, mode, tier, instruction, prompt, model,
                    quality, size, status, base_generation_id, cost_credits,
                    charged_free, charged_paid, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'processing', ?, ?, ?, ?, ?)
                """,
                (
                    generation_id,
                    profile_id,
                    mode.value,
                    tier.value,
                    instruction,
                    prompt,
                    model,
                    quality,
                    settings.image_size,
                    base_generation_id,
                    cost,
                    free_used,
                    paid_used,
                    created_at,
                ),
            )
    except Exception:
        _refund_credits(profile_id, free_used, paid_used)
        raise

    try:
        content, usage = _call_openai(
            profile_id=profile_id,
            image_paths=image_paths,
            prompt=prompt,
            model=model,
            quality=quality,
        )

        output_dir = Path(settings.generated_dir) / profile_id
        output_dir.mkdir(parents=True, exist_ok=True)
        extension = settings.image_output_format.lower()
        output_path = output_dir / f"{generation_id}.{extension}"
        output_path.write_bytes(content)

        completed_at = utc_now()
        with connection() as conn:
            conn.execute(
                """
                UPDATE generations
                SET status = 'completed',
                    output_path = ?,
                    usage_json = ?,
                    completed_at = ?
                WHERE id = ?
                """,
                (
                    str(output_path),
                    json.dumps(usage, ensure_ascii=False),
                    completed_at,
                    generation_id,
                ),
            )
    except Exception as exc:
        _refund_credits(profile_id, free_used, paid_used)
        with connection() as conn:
            conn.execute(
                """
                UPDATE generations
                SET status = 'failed',
                    error_message = ?,
                    completed_at = ?
                WHERE id = ?
                """,
                (str(exc)[:2000], utc_now(), generation_id),
            )
        if isinstance(exc, StylistError):
            raise
        raise ImageGenerationFailed(str(exc)) from exc

    return _generation_row(generation_id, profile_id)


def get_generation(profile_id: str, generation_id: str) -> dict:
    _profile(profile_id)
    return _generation_row(generation_id, profile_id)


def list_generations(profile_id: str, limit: int = 20) -> list[dict]:
    _profile(profile_id)
    safe_limit = max(1, min(int(limit), 100))
    with connection() as conn:
        rows = conn.execute(
            """
            SELECT * FROM generations
            WHERE profile_id = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (profile_id, safe_limit),
        ).fetchall()
    return [dict(row) for row in rows]
