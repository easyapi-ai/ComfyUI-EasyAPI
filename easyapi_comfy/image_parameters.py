"""EasyAPI's size extension supplements JSON Schema's preset enum."""
from __future__ import annotations

import math
import re


MAX_SAFE_INTEGER = 9007199254740991


def _dimensions(value):
    match = re.fullmatch(r"([0-9]+)\s*[xX*×]\s*([0-9]+)", value)
    if not match:
        return None
    parts = [part.lstrip("0") or "0" for part in match.groups()]
    if any(len(part) > 16 for part in parts):
        return None
    width, height = map(int, parts)
    if min(width, height) <= 0 or width * height > MAX_SAFE_INTEGER:
        return None
    return width, height


def normalize_image_size(definition, value):
    """Accept declared presets or a custom size inside the declared bounds."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("模型参数 size：请输入正整数宽高，例如 1024x1024")
    raw = value.strip()
    dimensions = _dimensions(raw)
    choices = definition.get("enum") or []
    for choice in choices:
        if not isinstance(choice, str):
            continue
        preset = choice.strip()
        if raw.lower() == preset.lower() or dimensions and dimensions == _dimensions(preset):
            if dimensions:
                return f"{dimensions[0]}x{dimensions[1]}"
            return "auto" if preset.lower() == "auto" else choice
    spec = definition.get("x-size-spec") or {}
    if raw.lower() == "auto":
        if spec.get("allow_auto") is True or str(definition.get("default", "")).lower() == "auto":
            return "auto"
        raise ValueError("模型参数 size：该模型未声明支持 auto")
    if spec.get("allow_custom") is not True:
        raise ValueError("模型参数 size：该模型未声明支持自定义尺寸")
    if dimensions is None:
        raise ValueError("模型参数 size：请输入正整数宽高，例如 1024x1024")
    width, height = dimensions
    measurements = {"width": width, "height": height, "pixels": width * height, "ratio": width / height}
    for name, measurement in measurements.items():
        for boundary, valid in (("min", lambda bound: measurement >= bound),
                                ("max", lambda bound: measurement <= bound)):
            bound = spec.get(f"{boundary}_{name}")
            if isinstance(bound, (int, float)) and not isinstance(bound, bool) and math.isfinite(bound) and not valid(bound):
                raise ValueError(f"模型参数 size：尺寸不符合 {boundary}_{name}={bound} 的限制")
    multiple = spec.get("multiple_of")
    if isinstance(multiple, (int, float)) and not isinstance(multiple, bool) and math.isfinite(multiple) and multiple > 0:
        if width % multiple or height % multiple:
            raise ValueError(f"模型参数 size：宽高必须是 {multiple} 的倍数")
    return f"{width}x{height}"
