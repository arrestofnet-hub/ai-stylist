---
name: ai-stylist
description: Use AI Stylist to try clothing and haircuts on a user's own reference photos while preserving identity, preferences, and credit limits.
---

# AI Stylist

Use this skill when the user wants to try clothing, change a haircut, build a complete look, or edit one element of an existing AI Stylist result.

## Core workflow

1. When authentication is enabled, call `get_my_style_profile` first. If no profile exists, call `create_style_profile`. In private beta without OAuth, create or reuse the known profile ID.
2. Ask for or use the user's real reference photos. Prefer 3-5 useful views when available:
   - clear face/front view;
   - side or three-quarter view;
   - full-body view.
3. Send those files with `add_reference_photos`, assigning viewpoint roles when known. Then use `get_style_readiness` to see which useful views are still missing.
4. For clothing-only requests, use `try_outfit`.
5. For hair-only requests, use `try_haircut`.
6. For coordinated clothing + hair/accessory requests, use `create_full_look`.
7. If the user wants to change only one detail of an existing result, use `change_one_item` with that result's generation ID.
8. After a successful generation, call `get_generated_image` so the actual generated image is shown.
9. Use `get_style_balance` when the user asks about remaining tries or when generation fails for lack of credits.
10. When the user states a durable preference such as “no jeans”, “only hooded coats”, “I prefer navy”, or “do not change my face”, save it with `update_style_preferences`.

## Identity preservation

Identity preservation is the default and highest-priority rule.

Do not ask the image model to:
- replace the user with a model;
- make them younger or older unless explicitly requested;
- change ethnicity or skin tone;
- change height, weight, face geometry or body proportions unless explicitly requested.

When the user asks to “look younger”, interpret it as styling: haircut, silhouette, colors, fit, footwear and accessories. Do not change facial age unless the user explicitly asks for that.

## Editing discipline

For `change_one_item`, name exactly what should change and explicitly preserve everything else.

Good:
“Replace only the dark coat with a camel hooded wool coat. Keep face, haircut, suit, body, pose, background, lighting and framing unchanged.”

Bad:
“Make the look better.”

## Preview vs final

Use `preview` while the user is exploring options. Use `final` only when the user wants a higher-quality final result or explicitly asks for the final version.

Do not waste final credits on exploratory changes.

## Preference handling

Treat direct user corrections as durable preferences when they appear general rather than one-off. Examples:
- “Jeans do not suit me” -> add jeans to avoid_items.
- “All coats should have a hood” -> add hooded coats to preferred_items / notes.
- “Do not change my face” -> preserve_identity remains true.
- “I like navy and charcoal” -> update colors.

Do not infer sensitive personal traits from photographs.

## Failure handling

If no reference photo exists, do not generate. Ask the user to provide one or more reference photos.

If a generation fails, do not claim it succeeded. Credits are refunded automatically by the backend on provider failure.

If the user has insufficient credits, report the current balance and do not retry repeatedly.


## User data controls

When the user asks what photos are stored, use `list_style_reference_photos`.

When the user asks to remove one reference photo, use `remove_style_reference_photo`. This is destructive; make sure the requested photo is unambiguous.

When the user asks to delete the entire stylist profile and its stored data, use `delete_style_profile`. This permanently removes the profile, reference photos, generated files, history, and related ledger records for that profile.

## Reference viewpoints

Prefer roles rather than unlabeled uploads:
- `front` — primary face/identity anchor;
- `three_quarter` — useful for both face and hair shape;
- `full_body` — important for outfit fit and proportions;
- `side` — especially useful for haircut/profile accuracy;
- `other` — only when the view does not fit the roles above.

For outfit work, front + full-body + three-quarter is the preferred trio.
For haircut work, front + three-quarter + side is the preferred trio.
