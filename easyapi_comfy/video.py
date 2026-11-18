"""Map ComfyUI's typed video inputs to the EasyAPI unified video request."""
from __future__ import annotations

from collections import Counter
from dataclasses import replace

from . import media


VIDEO_SLOTS = (
    ("first_frame", "image", "first_frame"),
    ("last_frame", "image", "last_frame"),
    ("reference_image_1", "image", "reference_image"),
    ("reference_image_2", "image", "reference_image"),
    ("reference_video_1", "video", "reference_video"),
    ("reference_video_2", "video", "reference_video"),
    ("reference_audio_1", "audio", "reference_audio"),
    ("reference_audio_2", "audio", "reference_audio"),
)
VIDEO_SCENARIOS = ("text-to-video", "image-to-video", "reference-to-video")
FRAME_ROLES = {"first_frame", "last_frame"}
VIDEO_ROLES = {role for _, _, role in VIDEO_SLOTS}


async def encode_video_sources(slots, references=None):
    sources = []
    for name, kind, role in VIDEO_SLOTS:
        value = slots.get(name)
        if value is None:
            continue
        encoded = await media.encode_inputs(**{kind: value})
        if len(encoded) != 1:
            raise ValueError(f"{name} accepts one {kind}. Use the reference group for a batch.")
        sources.append(replace(encoded[0], role=role))
    for source in await media.encode_inputs(references=references):
        if source.kind not in {"image", "audio", "video"}:
            raise ValueError(f"Video reference groups cannot contain {source.kind}.")
        role = source.role
        if not role or role == "reference":
            role = f"reference_{source.kind}"
        if role not in VIDEO_ROLES:
            raise ValueError(f"Unknown video reference role: {role}.")
        if role in FRAME_ROLES and source.kind != "image":
            raise ValueError(f"{role} requires an image.")
        if role.startswith("reference_") and role != f"reference_{source.kind}":
            raise ValueError(f"{role} does not match the connected {source.kind}.")
        sources.append(replace(source, role=role))
    counts = Counter(source.role for source in sources)
    if any(counts[role] > 1 for role in FRAME_ROLES):
        raise ValueError("Connect a single first frame and a single last frame.")
    return sources


def select_video_scenario(mode, roles, capabilities, *, model=""):
    """Select a documented form variant. This scenario is never a wire parameter."""
    requested = mode or "auto"
    if requested != "auto" and requested not in VIDEO_SCENARIOS:
        raise ValueError(f"Unknown video mode: {requested}.")

    has_media = bool(roles)
    if model.lower().startswith("wan3") and "last_frame" in roles and "first_frame" not in roles:
        raise ValueError("Wan 3 requires a first frame when a last frame is connected.")
    inferred = ("reference-to-video" if roles - FRAME_ROLES else
                "image-to-video" if has_media else "text-to-video")
    capabilities = capabilities or {}
    variants = capabilities.get("variants") or {}
    declared = capabilities.get("scenarios") or list(variants)
    supported_roles = capabilities.get("roles")
    if supported_roles is not None and (declared or supported_roles) and roles - set(supported_roles):
        names = ", ".join(sorted(roles - set(supported_roles)))
        raise ValueError(f"The selected model does not support these connected video inputs: {names}.")

    candidates = []
    for scenario in declared:
        if (scenario != "text-to-video") != has_media:
            continue
        if scenario == "reference-to-video" and not (roles - FRAME_ROLES):
            continue
        allowed = variants.get(scenario, {}).get("roles")
        if allowed is None or roles <= set(allowed):
            candidates.append(scenario)
    if not declared:
        return inferred if requested == "auto" else requested
    if requested != "auto":
        if requested in candidates:
            return requested
        raise ValueError(f"The selected video mode does not accept the connected inputs: {requested}.")
    if inferred in candidates:
        return inferred
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        if not has_media:
            raise ValueError("Connect the reference media required by this video model.")
        raise ValueError("The selected video model doesn't accept this combination of inputs.")
    raise ValueError("Choose a video mode for this combination of inputs.")
