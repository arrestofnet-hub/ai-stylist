from enum import Enum

from pydantic import BaseModel, Field


class StylePreferences(BaseModel):
    colors: list[str] = Field(default_factory=list)
    avoid_items: list[str] = Field(default_factory=list)
    preferred_items: list[str] = Field(default_factory=list)
    notes: str | None = None
    preserve_identity: bool = True


class ProfileCreate(BaseModel):
    display_name: str | None = None
    age_range: str | None = None
    gender: str | None = None
    height_cm: int | None = Field(default=None, ge=120, le=230)
    weight_kg: float | None = Field(default=None, ge=30, le=250)
    style_goal: str | None = None
    preferences: StylePreferences = Field(default_factory=StylePreferences)


class ProfileUpdate(BaseModel):
    display_name: str | None = None
    age_range: str | None = None
    gender: str | None = None
    height_cm: int | None = Field(default=None, ge=120, le=230)
    weight_kg: float | None = Field(default=None, ge=30, le=250)
    style_goal: str | None = None
    preferences: StylePreferences | None = None


class ProfileOut(ProfileCreate):
    id: str
    free_tries: int
    paid_credits: int
    reference_photos_count: int
    created_at: str
    updated_at: str


class BalanceOut(BaseModel):
    profile_id: str
    free_tries: int
    paid_credits: int
    total_available: int


class PhotoOut(BaseModel):
    id: str
    profile_id: str
    original_name: str | None
    mime_type: str
    created_at: str


class GenerationMode(str, Enum):
    outfit = "outfit"
    haircut = "haircut"
    change_item = "change_item"
    full_look = "full_look"


class GenerationTier(str, Enum):
    preview = "preview"
    final = "final"


class GenerationRequest(BaseModel):
    mode: GenerationMode
    instruction: str = Field(min_length=2, max_length=4000)
    tier: GenerationTier = GenerationTier.preview
    base_generation_id: str | None = None
    reference_photo_ids: list[str] | None = None
    idempotency_key: str | None = Field(default=None, min_length=8, max_length=128)


class GenerationOut(BaseModel):
    id: str
    profile_id: str
    mode: GenerationMode
    tier: GenerationTier
    status: str
    model: str
    quality: str
    size: str
    cost_credits: int
    charged_free: int
    charged_paid: int
    image_url: str | None = None
    error_message: str | None = None
    created_at: str
    completed_at: str | None = None


class GenerationListOut(BaseModel):
    items: list[GenerationOut]


class CreditGrantRequest(BaseModel):
    amount: int = Field(gt=0, le=100000)
    reason: str | None = Field(default=None, max_length=500)
    external_reference: str | None = Field(default=None, max_length=200)


class CreditTransactionOut(BaseModel):
    id: str
    profile_id: str
    amount: int
    kind: str
    reason: str | None = None
    external_reference: str | None = None
    created_at: str


class AdminStatsOut(BaseModel):
    profiles: int
    reference_photos: int
    generations_total: int
    generations_completed: int
    generations_failed: int
    generations_processing: int
    free_tries_remaining: int
    paid_credits_remaining: int
    paid_credits_granted: int
    paid_credits_charged: int
    paid_credits_refunded: int


class MaintenanceOut(BaseModel):
    stale_generations_recovered: int = 0
    generated_files_deleted: int = 0
