import hmac
import json
from collections import defaultdict
from uuid import uuid4

from fastapi import APIRouter, Header, HTTPException, status

from app.config import settings
from app.db import connection, utc_now
from app.schemas import (
    AdminStatsOut,
    CreditGrantRequest,
    CreditTransactionOut,
    MaintenanceOut,
    UsageModelStats,
    UsageStatsOut,
)
from app.services.image_service import (
    purge_expired_generated_files,
    recover_stale_generations,
)

router = APIRouter(prefix="/admin", tags=["admin"])


def _require_admin_key(x_admin_key: str | None) -> None:
    configured = settings.admin_api_key
    if not configured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Admin API is disabled",
        )
    if not x_admin_key or not hmac.compare_digest(x_admin_key, configured):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid admin key",
        )


@router.post(
    "/profiles/{profile_id}/credits",
    response_model=CreditTransactionOut,
    status_code=status.HTTP_201_CREATED,
)
def grant_paid_credits(
    profile_id: str,
    payload: CreditGrantRequest,
    x_admin_key: str | None = Header(default=None, alias="X-Admin-Key"),
) -> CreditTransactionOut:
    """Testing/admin credit grant. Production payments should call the same ledger logic after verified webhook settlement."""
    _require_admin_key(x_admin_key)

    transaction_id = str(uuid4())
    created_at = utc_now()

    with connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        profile = conn.execute(
            "SELECT id FROM profiles WHERE id = ?",
            (profile_id,),
        ).fetchone()
        if profile is None:
            raise HTTPException(status_code=404, detail="Profile not found")

        conn.execute(
            """
            UPDATE profiles
            SET paid_credits = paid_credits + ?,
                updated_at = ?
            WHERE id = ?
            """,
            (payload.amount, created_at, profile_id),
        )
        conn.execute(
            """
            INSERT INTO credit_transactions (
                id, profile_id, amount, kind, reason,
                external_reference, created_at
            )
            VALUES (?, ?, ?, 'admin_grant', ?, ?, ?)
            """,
            (
                transaction_id,
                profile_id,
                payload.amount,
                payload.reason,
                payload.external_reference,
                created_at,
            ),
        )

    return CreditTransactionOut(
        id=transaction_id,
        profile_id=profile_id,
        amount=payload.amount,
        kind="admin_grant",
        reason=payload.reason,
        external_reference=payload.external_reference,
        created_at=created_at,
    )


@router.get("/stats", response_model=AdminStatsOut)
def admin_stats(
    x_admin_key: str | None = Header(default=None, alias="X-Admin-Key"),
) -> AdminStatsOut:
    _require_admin_key(x_admin_key)

    with connection() as conn:
        profile = conn.execute(
            """
            SELECT COUNT(*) AS profiles,
                   COALESCE(SUM(free_tries), 0) AS free_tries_remaining,
                   COALESCE(SUM(paid_credits), 0) AS paid_credits_remaining
            FROM profiles
            """
        ).fetchone()

        photos = conn.execute(
            "SELECT COUNT(*) AS n FROM reference_photos"
        ).fetchone()

        generations = conn.execute(
            """
            SELECT COUNT(*) AS total,
                   SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) AS completed,
                   SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed,
                   SUM(CASE WHEN status = 'processing' THEN 1 ELSE 0 END) AS processing
            FROM generations
            """
        ).fetchone()

        ledger = conn.execute(
            """
            SELECT
                COALESCE(SUM(CASE WHEN kind = 'admin_grant' THEN amount ELSE 0 END), 0)
                    AS granted,
                COALESCE(SUM(CASE WHEN kind = 'generation_charge' THEN -amount ELSE 0 END), 0)
                    AS charged,
                COALESCE(SUM(CASE WHEN kind = 'generation_refund' THEN amount ELSE 0 END), 0)
                    AS refunded
            FROM credit_transactions
            """
        ).fetchone()

    return AdminStatsOut(
        profiles=int(profile["profiles"]),
        reference_photos=int(photos["n"]),
        generations_total=int(generations["total"]),
        generations_completed=int(generations["completed"] or 0),
        generations_failed=int(generations["failed"] or 0),
        generations_processing=int(generations["processing"] or 0),
        free_tries_remaining=int(profile["free_tries_remaining"]),
        paid_credits_remaining=int(profile["paid_credits_remaining"]),
        paid_credits_granted=int(ledger["granted"]),
        paid_credits_charged=int(ledger["charged"]),
        paid_credits_refunded=int(ledger["refunded"]),
    )


@router.post("/maintenance", response_model=MaintenanceOut)
def run_maintenance(
    x_admin_key: str | None = Header(default=None, alias="X-Admin-Key"),
) -> MaintenanceOut:
    _require_admin_key(x_admin_key)
    return MaintenanceOut(
        stale_generations_recovered=recover_stale_generations(),
        generated_files_deleted=purge_expired_generated_files(),
    )


@router.get("/usage", response_model=UsageStatsOut)
def usage_stats(
    x_admin_key: str | None = Header(default=None, alias="X-Admin-Key"),
) -> UsageStatsOut:
    _require_admin_key(x_admin_key)

    with connection() as conn:
        rows = conn.execute(
            """
            SELECT model, tier, usage_json, duration_ms
            FROM generations
            WHERE status = 'completed'
            ORDER BY created_at ASC
            """
        ).fetchall()

    buckets: dict[tuple[str, str], dict[str, object]] = defaultdict(
        lambda: {
            "generations": 0,
            "input_tokens": 0,
            "input_image_tokens": 0,
            "input_text_tokens": 0,
            "output_tokens": 0,
            "output_image_tokens": 0,
            "output_text_tokens": 0,
            "total_tokens": 0,
            "durations": [],
        }
    )

    for row in rows:
        key = (str(row["model"]), str(row["tier"]))
        bucket = buckets[key]
        bucket["generations"] = int(bucket["generations"]) + 1

        usage: dict = {}
        if row["usage_json"]:
            try:
                parsed = json.loads(row["usage_json"])
                if isinstance(parsed, dict):
                    usage = parsed
            except json.JSONDecodeError:
                usage = {}

        input_details = usage.get("input_tokens_details") or {}
        output_details = usage.get("output_tokens_details") or {}

        bucket["input_tokens"] = int(bucket["input_tokens"]) + int(
            usage.get("input_tokens") or 0
        )
        bucket["input_image_tokens"] = int(bucket["input_image_tokens"]) + int(
            input_details.get("image_tokens") or 0
        )
        bucket["input_text_tokens"] = int(bucket["input_text_tokens"]) + int(
            input_details.get("text_tokens") or 0
        )
        bucket["output_tokens"] = int(bucket["output_tokens"]) + int(
            usage.get("output_tokens") or 0
        )
        bucket["output_image_tokens"] = int(bucket["output_image_tokens"]) + int(
            output_details.get("image_tokens") or 0
        )
        bucket["output_text_tokens"] = int(bucket["output_text_tokens"]) + int(
            output_details.get("text_tokens") or 0
        )
        bucket["total_tokens"] = int(bucket["total_tokens"]) + int(
            usage.get("total_tokens") or 0
        )

        if row["duration_ms"] is not None:
            bucket["durations"].append(int(row["duration_ms"]))

    items: list[UsageModelStats] = []
    for (model, tier), bucket in sorted(buckets.items()):
        durations = bucket["durations"]
        avg_duration = (
            sum(durations) / len(durations)
            if isinstance(durations, list) and durations
            else None
        )
        generations_count = int(bucket["generations"])
        input_tokens = int(bucket["input_tokens"])
        input_image_tokens = int(bucket["input_image_tokens"])
        input_text_tokens = int(bucket["input_text_tokens"])
        output_tokens = int(bucket["output_tokens"])
        output_image_tokens = int(bucket["output_image_tokens"])
        output_text_tokens = int(bucket["output_text_tokens"])

        unclassified_input = max(
            0,
            input_tokens - input_image_tokens - input_text_tokens,
        )
        unclassified_output = max(
            0,
            output_tokens - output_image_tokens - output_text_tokens,
        )

        estimated_cost_usd = (
            input_text_tokens * settings.image_text_input_usd_per_million
            + (input_image_tokens + unclassified_input)
            * settings.image_input_usd_per_million
            + (output_image_tokens + unclassified_output)
            * settings.image_output_usd_per_million
        ) / 1_000_000

        estimated_cost_kzt = (
            estimated_cost_usd * settings.usd_kzt_rate
            if settings.usd_kzt_rate > 0
            else None
        )

        items.append(
            UsageModelStats(
                model=model,
                tier=tier,
                generations=generations_count,
                input_tokens=input_tokens,
                input_image_tokens=input_image_tokens,
                input_text_tokens=input_text_tokens,
                output_tokens=output_tokens,
                output_image_tokens=output_image_tokens,
                output_text_tokens=output_text_tokens,
                total_tokens=int(bucket["total_tokens"]),
                avg_duration_ms=avg_duration,
                estimated_cost_usd=round(estimated_cost_usd, 6),
                estimated_cost_kzt=(
                    round(estimated_cost_kzt, 2)
                    if estimated_cost_kzt is not None
                    else None
                ),
                avg_cost_per_generation_usd=round(
                    estimated_cost_usd / generations_count,
                    6,
                ) if generations_count else 0.0,
            )
        )

    return UsageStatsOut(items=items)
