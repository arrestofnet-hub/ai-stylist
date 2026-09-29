from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import FileResponse

from app.schemas import GenerationListOut, GenerationOut, GenerationRequest
from app.services.image_service import (
    ImageGenerationFailed,
    ImageProviderUnavailable,
    InsufficientCredits,
    InvalidGenerationRequest,
    MissingReferencePhotos,
    ProfileNotFound,
    delete_generation,
    generate_style_image,
    get_generation,
    list_generations,
)

router = APIRouter(
    prefix="/profiles/{profile_id}/generations",
    tags=["generations"],
)


def _to_output(row: dict) -> GenerationOut:
    image_url = None
    if row.get("status") == "completed" and row.get("output_path"):
        image_url = (
            f"/api/v1/profiles/{row['profile_id']}/generations/"
            f"{row['id']}/image"
        )

    return GenerationOut(
        id=row["id"],
        profile_id=row["profile_id"],
        mode=row["mode"],
        tier=row["tier"],
        status=row["status"],
        model=row["model"],
        quality=row["quality"],
        size=row["size"],
        cost_credits=int(row["cost_credits"]),
        charged_free=int(row["charged_free"]),
        charged_paid=int(row["charged_paid"]),
        image_url=image_url,
        error_message=row.get("error_message"),
        created_at=row["created_at"],
        completed_at=row.get("completed_at"),
        duration_ms=row.get("duration_ms"),
    )


def _raise_http(exc: Exception) -> None:
    if isinstance(exc, ProfileNotFound):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, MissingReferencePhotos):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if isinstance(exc, InsufficientCredits):
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=str(exc),
        ) from exc
    if isinstance(exc, InvalidGenerationRequest):
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if isinstance(exc, ImageProviderUnavailable):
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if isinstance(exc, ImageGenerationFailed):
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    raise exc


@router.post(
    "",
    response_model=GenerationOut,
    status_code=status.HTTP_201_CREATED,
)
def create_generation(
    profile_id: str,
    payload: GenerationRequest,
) -> GenerationOut:
    try:
        row = generate_style_image(
            profile_id=profile_id,
            mode=payload.mode,
            instruction=payload.instruction,
            tier=payload.tier,
            base_generation_id=payload.base_generation_id,
            reference_photo_ids=payload.reference_photo_ids,
            idempotency_key=payload.idempotency_key,
        )
        return _to_output(row)
    except Exception as exc:
        _raise_http(exc)
        raise


@router.get("", response_model=GenerationListOut)
def generation_history(
    profile_id: str,
    limit: int = Query(default=20, ge=1, le=100),
) -> GenerationListOut:
    try:
        rows = list_generations(profile_id, limit=limit)
        return GenerationListOut(items=[_to_output(row) for row in rows])
    except Exception as exc:
        _raise_http(exc)
        raise


@router.get("/{generation_id}", response_model=GenerationOut)
def generation_details(
    profile_id: str,
    generation_id: str,
) -> GenerationOut:
    try:
        return _to_output(get_generation(profile_id, generation_id))
    except Exception as exc:
        _raise_http(exc)
        raise


@router.get("/{generation_id}/image")
def generation_image(
    profile_id: str,
    generation_id: str,
) -> FileResponse:
    try:
        row = get_generation(profile_id, generation_id)
    except Exception as exc:
        _raise_http(exc)
        raise

    if row["status"] != "completed" or not row.get("output_path"):
        raise HTTPException(status_code=409, detail="Generation is not completed")

    path = Path(row["output_path"])
    if not path.exists():
        raise HTTPException(status_code=404, detail="Generated image file not found")

    media_type = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
    }.get(path.suffix.lower(), "application/octet-stream")

    return FileResponse(
        path=path,
        media_type=media_type,
        filename=path.name,
    )


@router.delete(
    "/{generation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def remove_generation(
    profile_id: str,
    generation_id: str,
):
    try:
        delete_generation(profile_id, generation_id)
    except Exception as exc:
        _raise_http(exc)
        raise
