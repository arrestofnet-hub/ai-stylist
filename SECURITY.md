# Security

AI Stylist processes personal photographs. Treat reference photos and generated images as private user data.

## Secrets

Never commit:
- `OPENAI_API_KEY`;
- `ADMIN_API_KEY`;
- OAuth client/provider secrets;
- production database files.

The repository ignores `.env`, SQLite databases, uploads, and generated images.

## Public network surface

The production Nginx template exposes only:
- `/mcp`;
- `/.well-known/oauth-protected-resource/mcp` when OAuth is enabled;
- `/health`.

The internal REST and admin APIs stay on localhost. Do not expose ports 8010/8011 directly to the internet.

## Authentication

Closed/private beta can run with:

```env
MCP_AUTH_ENABLED=false
```

Before public multi-user launch, configure a real OAuth/OIDC identity provider:

```env
MCP_AUTH_ENABLED=true
OAUTH_ISSUER_URL=https://...
OAUTH_JWKS_URL=https://.../.well-known/jwks.json
MCP_RESOURCE_URL=https://stylist.example.com/mcp
OAUTH_REQUIRED_SCOPES=stylist
```

The MCP resource server validates JWT signature, issuer, audience/resource, expiration, and required scopes. Authenticated profiles are bound to the token `sub` and another user receives the same “Profile not found” response rather than profile-existence information.

## Image handling

Reference images:
- accept only JPEG/PNG/WebP;
- maximum 15 MB;
- minimum 256×256;
- maximum 40 million pixels;
- are decoded and re-encoded before storage;
- have EXIF metadata stripped;
- are stored as sanitized WebP;
- duplicate sanitized images are rejected per profile.

MCP file downloads:
- require HTTPS;
- reject localhost, private, loopback, link-local, multicast, reserved, and unspecified addresses;
- revalidate every redirect;
- limit redirect count.

## Credits and generation integrity

Credit deduction and generation-record creation occur in one SQLite immediate transaction.

The service also provides:
- request idempotency;
- one active generation per profile by default;
- automatic refunds on provider failure;
- stale-processing recovery after restart;
- paid-credit transaction ledger.

## Retention and deletion

Generated files can be expired automatically according to `GENERATED_RETENTION_DAYS`.

Users can remove individual reference photos and can permanently delete their entire style profile. Profile deletion cascades database records and removes local reference/generated files.

## Operational checks

Use:
- `/health` for liveness;
- `/ready` internally for database/provider readiness;
- `/api/v1/admin/stats` internally for service counters;
- `/api/v1/admin/usage` internally for measured model usage and cost estimates;
- `/api/v1/admin/maintenance` internally for stale recovery and file retention.

All admin endpoints require `X-Admin-Key` and remain behind the localhost-only API surface.
