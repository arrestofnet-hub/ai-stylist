import json
import shutil
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, File, HTTPException, UploadFile, status

from app.config import settings
from app.db import connection, decode_preferences, utc_now
from app.schemas import (
    BalanceOut,
    PhotoOut,
    ProfileCreate,
    ProfileOut,
    ProfileUpdate,
    StylePreferences,
)

router = APIRouter(prefix="/profiles", tags=["profiles"])

ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}
MAX_REFERENCE_PHOTOS = 5


def _profile_or_404(profile_id: str) -> dict:
    with connection() as conn:
        row = conn.execute(
            """
            SELECT p.*,
                   (SELECT COUNT(*) FROM reference_photos r WHERE r.profile_id = p.id)
                   AS reference_photos_count
            FROM profiles p
            WHERE p.id = ?
            """,
            (profile_id,),
        ).fetchone()

    if row is None:
        raise HTTPException(status_code=404, detail="Profile not found")

    data = dict(row)
    data["preferences"] = StylePreferences(
        **decode_preferences(data.pop("preferences_json"))
    )
    return data


@router.post("", response_model=ProfileOut, status_code=status.HTTP_201_CREATED)
def create_profile(payload: ProfileCreate) -> ProfileOut:
    profile_id = str(uuid4())
    now = utc_now()

    with connection() as conn:
        conn.execute(
            """
            INSERT INTO profiles (
                id, display_name, age_range, gender, height_cm, weight_kg,
                style_goal, preferences_json, free_tries, paid_credits,
                created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 3, 0, ?, ?)
            """,
            (
                profile_id,
                payload.display_name,
                payload.age_range,
                payload.gender,
                payload.height_cm,
                payload.weight_kg,
                payload.style_goal,
                json.dumps(payload.preferences.model_dump(), ensure_ascii=False),
                now,
                now,
            ),
        )

    return ProfileOut(**_profile_or_404(profile_id))


@router.get("/{profile_id}", response_model=ProfileOut)
def get_profile(profile_id: str) -> ProfileOut:
    return ProfileOut(**_profile_or_404(profile_id))


@router.patch("/{profile_id}", response_model=ProfileOut)
def update_profile(profile_id: str, payload: ProfileUpdate) -> ProfileOut:
    current = _profile_or_404(profile_id)
    changes = payload.model_dump(exclude_unset=True)

    if "preferences" in changes and changes["preferences"] is not None:
        changes["preferences_json"] = json.dumps(
            changes.pop("preferences"), ensure_ascii=False
        )

    allowed = {
        "display_name",
        "age_range",
        "gender",
        "height_cm",
        "weight_kg",
        "style_goal",
        "preferences_json",
    }
    changes = {k: v for k, v in changes.items() if k in allowed}

    if changes:
        changes["updated_at"] = utc_now()
        fields = ", ".join(f"{key} = ?" for key in changes)
        values = list(changes.values()) + [profile_id]
        with connection() as conn:
            conn.execute(f"UPDATE profiles SET {fields} WHERE id = ?", values)

    return ProfileOut(**_profile_or_404(profile_id))


@router.get("/{profile_id}/balance", response_model=BalanceOut)
def get_balance(profile_id: str) -> BalanceOut:
    profile = _profile_or_404(profile_id)
    free_tries = int(profile["free_tries"])
    paid_credits = int(profile["paid_credits"])
    return BalanceOut(
        profile_id=profile_id,
        free_tries=free_tries,
        paid_credits=paid_credits,
        total_available=free_tries + paid_credits,
    )


@router.post(
    "/{profile_id}/photos",
    response_model=PhotoOut,
    status_code=status.HTTP_201_CREATED,
)
def upload_reference_photo(
    profile_id: str,
    photo: UploadFile = File(...),
) -> PhotoOut:
    _profile_or_404(profile_id)

    if photo.content_type not in ALLOWED_IMAGE_TYPES:
        raise HTTPException(
            status_code=415,
            detail="Only JPEG, PNG and WEBP images are supported",
        )

    with connection() as conn:
        count = conn.execute(
            "SELECT COUNT(*) AS n FROM reference_photos WHERE profile_id = ?",
            (profile_id,),
        ).fetchone()["n"]

    if count >= MAX_REFERENCE_PHOTOS:
        raise HTTPException(
            status_code=409,
            detail=f"Maximum {MAX_REFERENCE_PHOTOS} reference photos allowed",
        )

    suffix = {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
    }[photo.content_type]

    photo_id = str(uuid4())
    profile_dir = Path(settings.upload_dir) / profile_id
    profile_dir.mkdir(parents=True, exist_ok=True)
    destination = profile_dir / f"{photo_id}{suffix}"

    with destination.open("wb") as output:
        shutil.copyfileobj(photo.file, output)

    now = utc_now()
    with connection() as conn:
        conn.execute(
            """
            INSERT INTO reference_photos (
                id, profile_id, file_path, original_name, mime_type, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                photo_id,
                profile_id,
                str(destination),
                photo.filename,
                photo.content_type,
                now,
            ),
        )

    return PhotoOut(
        id=photo_id,
        profile_id=profile_id,
        original_name=photo.filename,
        mime_type=photo.content_type,
        created_at=now,
    )
