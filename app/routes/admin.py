import hmac
from uuid import uuid4

from fastapi import APIRouter, Header, HTTPException, status

from app.config import settings
from app.db import connection, utc_now
from app.schemas import CreditGrantRequest, CreditTransactionOut

router = APIRouter(prefix="/admin", tags=["admin"])


def _require_admin_key(x_admin_key: str | None) -> None:
    configured = settings.admin_api_key
    if not configured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Admin credit management is disabled",
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
