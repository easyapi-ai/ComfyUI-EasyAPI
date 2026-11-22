"""Read-only billing information for the saved EasyAPI connection."""
from __future__ import annotations

import asyncio
import math
import re
import time

from .client import EasyAPIError, normalize_locale
from .profiles import OFFICIAL_GATEWAY


_FACTS = {
    "model", "operation", "duration", "resolution", "size", "n", "generate_audio", "aspect_ratio",
    "has_video_input", "has_audio_input", "has_image_input", "input_image_count", "input_video_seconds",
    "input_tokens", "output_tokens", "text_characters",
}
_RATE_KEYS = ("prompt", "completion", "request", "second", "input_cache_read", "input_cache_write", "image", "audio", "audio_completion")
_IMAGE_OPERATIONS = {"image-generation", "image-edit"}
_ENDPOINT_UNAVAILABLE = {404, 405, 501}
_DECIMAL = r"\d+(?:\.\d+)?"
_PUBLIC_FIXED = re.compile(rf'fixed\((?P<price>{_DECIMAL})\)(?P<per_image>\*image_count)?')
_PUBLIC_FIXED_TIER = re.compile(rf'tier\("request",fixed\((?P<price>{_DECIMAL})\)(?P<inside>\*image_count)?\)(?P<outside>\*image_count)?')
_PUBLIC_IMAGE_TIER = re.compile(
    rf'tier\("image",fixed\((?P<input>{_DECIMAL})\)\*image_input_count\+fixed\((?P<output>{_DECIMAL})\)'
    rf'\*image_output_count\*\(image_output_tier==2\?(?P<high>{_DECIMAL}):1\)\)'
)
_IMAGE_2K_AREA_THRESHOLD = 2_250_000  # Gateway: relay/helper/billing_expr_image.go.


def _number(value, *, signed=False):
    if isinstance(value, bool) or not isinstance(value, (int, float, str)) or len(str(value)) > 48:
        return None
    try:
        number = float(value)
        return number if math.isfinite(number) and (signed or number >= 0) else None
    except ValueError:
        return None


def _data(response):
    value = response.data
    if isinstance(value, dict):
        value = value.get("data", value)
    if not isinstance(value, dict):
        raise EasyAPIError("Invalid billing response", status=502)
    return value


def _amount(value, *, signed=False):
    if not isinstance(value, dict):
        return None
    amount, currency = _number(value.get("amount"), signed=signed), value.get("currency")
    if amount is None or not isinstance(currency, str) or not 0 < len(currency) <= 16:
        return None
    return {"amount": amount, "currency": currency}


def _is_official(client):
    return getattr(client, "base_url", None) == OFFICIAL_GATEWAY


async def _quota_display(client):
    if _is_official(client):
        try:
            config = await asyncio.wait_for(client.billing_currency_config(), 5)
        except (EasyAPIError, TimeoutError):
            config = None
        if isinstance(config, dict) and (unit := _number(config.get("quota_per_unit"))):
            if config.get("quota_display_type") == "CNY" and (rate := _number(config.get("usd_exchange_rate"))):
                return "CNY", rate / unit
            if config.get("quota_display_type") == "USD":
                return "USD", 1 / unit
    return "quota", 1


async def _token_usage_balance(client, *, required):
    try:
        data = _data(await asyncio.wait_for(client.request("GET", "/usage/token"), 12))
    except (EasyAPIError, TimeoutError):
        if required:
            raise
        return {}
    remaining = _number(data.get("total_available"), signed=True)
    unlimited = data.get("unlimited_quota") is True
    if remaining is None and not unlimited:
        if required:
            raise EasyAPIError("This gateway does not expose a token balance", status=502)
        return {}
    currency, scale = await _quota_display(client)
    token = {"unlimited": unlimited, "currency": currency, "source": "token_usage"}
    if remaining is not None:
        token["amount"] = remaining * scale
    for field in ("total_granted", "total_used"):
        if (amount := _number(data.get(field), signed=field == "total_granted")) is not None:
            token[field] = amount * scale
    result = {"token": token}
    budget, used = _number(data.get("budget_quota")), _number(data.get("budget_used"))
    if budget and used is not None and data.get("budget_period") in (1, 2):
        result["budget"] = {"amount": max(0, budget - used) * scale, "currency": currency, "period": data["budget_period"]}
    return result


async def connection_balance(client):
    try:
        data = _data(await asyncio.wait_for(client.request("GET", "/v1/billing/balance"), 12))
    except EasyAPIError as error:
        if error.status not in _ENDPOINT_UNAVAILABLE:
            raise
        return await _token_usage_balance(client, required=True)

    result = {}
    wallet = _amount(data.get("wallet"), signed=True)
    if wallet is not None:
        result["wallet"] = wallet
    token = data.get("token")
    if isinstance(token, dict):
        if token.get("unlimited") is True:
            result["token"] = {"unlimited": True}
        elif (remaining := _amount(token, signed=True)) is not None:
            result["token"] = {**remaining, "unlimited": False}
        currency = token.get("currency")
        if "token" in result and isinstance(currency, str) and 0 < len(currency) <= 16:
            for field in ("total_granted", "total_used"):
                if (amount := _number(token.get(field), signed=field == "total_granted")) is not None:
                    result["token"].update({field: amount, "currency": currency})
    budget = data.get("budget")
    if (remaining := _amount(budget)) is not None:
        result["budget"] = {**remaining, "period": budget.get("period") if budget.get("period") in (1, 2, "daily", "monthly") else None}
    if _is_official(client) and not {"total_granted", "total_used"} <= result.get("token", {}).keys():
        # The wallet remains from its own route. Legacy token metrics are a
        # separate snapshot and never stand in for account funds.
        for key, usage in (await _token_usage_balance(client, required=False)).items():
            if key == "token" or key not in result:
                result[key] = usage
    if not ({"wallet", "token"} & result.keys()):
        raise EasyAPIError("This gateway does not expose a balance", status=502)
    return result


def public_rates(row):
    rates = row.get("pricing")
    billing = row.get("billing")
    if not isinstance(rates, dict) or not isinstance(billing, dict):
        return None
    end = _number(rates.get("limited_discount_end"))
    if end and end <= time.time():
        return None  # Prices cached during a concluded offer cannot support a fresh estimate.
    values = {key: value for key in _RATE_KEYS if (value := _number(rates.get(key))) and value > 0}
    mode = billing.get("mode")
    if mode not in ("request", "second", "token"):
        mode = None
    if not values and mode is None:
        return None
    discount = _number(rates.get("discount"))
    limited = _number(rates.get("limited_discount_rate"))
    result = {"mode": mode, "rates": values, "currency": "USD", "discount": discount if discount and discount <= 1 else 1}
    if limited and limited < 1:
        name = rates.get("limited_discount_name")
        result["offer"] = {"discount": limited, "name": name[:120] if isinstance(name, str) else "", "ends_at": end}
    return result


def _metering_facts(value):
    if not isinstance(value, dict):
        raise ValueError("Billing estimate must be an object")
    facts = {}
    for key in _FACTS & value.keys():
        item = value[key]
        if key in {"model", "operation", "resolution", "size", "aspect_ratio"}:
            if isinstance(item, str) and 0 < len(item) <= (400 if key == "model" else 64):
                facts[key] = item
        elif key.startswith("has_") or key == "generate_audio":
            if isinstance(item, bool):
                facts[key] = item
        elif not isinstance(item, (bool, str)) and (_number(item) is not None or key == "duration" and item == -1):
            facts[key] = item
    if not facts.get("model") or not facts.get("operation"):
        raise ValueError("Model and operation are required for a billing estimate")
    return facts


def _count(value, *, minimum=0):
    number = _number(value)
    return int(number) if number is not None and number.is_integer() and minimum <= number <= 128 else None


def _estimate(amount, original, currency, discount, basis, version=1):
    if not math.isfinite(amount) or not math.isfinite(original):
        return None
    result = {"amount": amount, "currency": currency, "basis": basis, "expression_version": version}
    if discount < 1:
        result.update(discount=discount, original_amount=original)
    return result


def _authenticated_image_quote(client, row, pricing, facts):
    category = row.get("category")
    if not isinstance(category, str) or category.casefold() not in {"image", "图片", "圖像", "圖片"}:
        return None
    if not pricing or pricing["mode"] != "request" or not (rate := pricing["rates"].get("request")):
        return None
    if (count := _count(facts.get("n", 1), minimum=1)) is None:
        return None
    if count > 1 and not _is_official(client):
        return None  # Custom gateways can treat the published rate as per request.
    discount = pricing["discount"]
    amount = rate * count
    return _estimate(amount, amount / discount, "USD", discount, "authenticated_catalog")


def _published_terms(expression):
    if not isinstance(expression, str) or len(expression) > 1024:
        return None
    expression = re.sub(r"\s+", "", expression)
    version = 2 if expression.startswith("v2:") else 1
    if expression.startswith(("v1:", "v2:")):
        expression = expression[3:]
    if (match := _PUBLIC_FIXED.fullmatch(expression)):
        key = "image_output" if match["per_image"] else "request"
        return version, {key: float(match["price"])}
    if (match := _PUBLIC_FIXED_TIER.fullmatch(expression)) and not (match["inside"] and match["outside"]):
        key = "image_output" if match["inside"] or match["outside"] else "request"
        return version, {key: float(match["price"])}
    if (match := _PUBLIC_IMAGE_TIER.fullmatch(expression)):
        output = float(match["output"])
        return version, {"image_input": float(match["input"]), "image_output": output,
                         "image_output_2k": output * float(match["high"])}
    return None


def _published_total(rates, facts):
    if "request" in rates:
        return rates["request"]
    count = _count(facts.get("n", 1), minimum=1)
    if count is None:
        return None
    if "image_output_2k" not in rates:
        return rates["image_output"] * count
    inputs = _count(facts.get("input_image_count"))
    has_image = facts.get("has_image_input")
    if inputs is not None and has_image is not None and bool(inputs) != has_image:
        return None
    if inputs is None and has_image is False:
        inputs = 0
    size = str(facts.get("size", "")).lower().replace(" ", "")
    dimensions = re.fullmatch(r"([1-9]\d{0,5})[x×*]([1-9]\d{0,5})", size)
    if inputs is None or dimensions is None:
        return None
    area = int(dimensions[1]) * int(dimensions[2])
    output = rates["image_output_2k"] if area > _IMAGE_2K_AREA_THRESHOLD else rates["image_output"]
    return rates["image_input"] * inputs + output * count


async def _published_quote(client, facts, locale, *, rates_only):
    if not _is_official(client):
        return None
    try:
        catalog = await asyncio.wait_for(client.public_billing_catalog(locale), 8)
    except (EasyAPIError, TimeoutError):
        return None
    if not isinstance(catalog, dict):
        return None
    rows, ratios, group = catalog.get("data"), catalog.get("group_ratio"), catalog.get("pricing_group")
    if not isinstance(rows, list):
        return None
    row = next((item for item in rows if isinstance(item, dict) and item.get("model_name") == facts["model"]), None)
    if row is None or row.get("billing_mode") != "tiered_expr":
        return None
    # A dynamic model must not fall back to a generic authenticated catalogue
    # unit price that does not carry its expression or currency version.
    expression = row.get("billing_expr")
    if not isinstance(expression, str) or not expression.strip():
        return {"reason": "quote_unavailable"}
    version = 2 if expression.lstrip().startswith("v2:") else 1
    currency = "CNY" if version == 2 else "USD"
    basis = "public_catalog"
    pricing = {"currency": currency, "rates": {}, "mode": "dynamic", "basis": basis, "expression_version": version}
    unavailable = {"pricing": pricing, "reason": "missing_usage" if rates_only else "quote_unavailable"}
    ratio = _number(ratios.get(group)) if isinstance(ratios, dict) and isinstance(group, str) else None
    endpoints = row.get("supported_endpoint_types")
    if ratio is None or not isinstance(endpoints, list) or facts["operation"] not in endpoints:
        return unavailable
    discount = _number(row.get("model_discount", 1))
    end = _number(row.get("limited_discount_end"))
    if discount is None or discount > 1 or (end and end <= time.time()):
        return unavailable
    pricing["discount"] = discount
    terms = _published_terms(expression) if facts["operation"] in _IMAGE_OPERATIONS else None
    if terms is None:
        return unavailable
    version, rates = terms
    if any(not math.isfinite(value * ratio) for value in rates.values()):
        return unavailable
    pricing = {"currency": currency, "rates": {key: value * ratio * discount for key, value in rates.items()},
               "mode": "request", "discount": discount, "basis": basis, "expression_version": version, "group": group}
    original = None if rates_only else _published_total(rates, facts)
    if original is None:
        return {"pricing": pricing, "reason": "missing_usage"}
    estimate = _estimate(original * ratio * discount, original * ratio, currency, discount, basis, version)
    return {"pricing": pricing, "estimate": estimate} if estimate else {"pricing": pricing, "reason": "quote_unavailable"}


async def model_estimate(client, value, locale=None, *, rates_only=False):
    facts = _metering_facts(value)
    locale = normalize_locale(locale)
    row = await asyncio.wait_for(client.model_billing_row(facts["model"], locale), 12)
    if row is None:
        return {"reason": "model_unavailable"}
    pricing = public_rates(row)
    result = {"pricing": pricing} if pricing else {}
    if rates_only:
        if published := await _published_quote(client, facts, locale, rates_only=True):
            return published
        return result | {"reason": "missing_usage"}
    try:
        quote = _data(await asyncio.wait_for(client.request("POST", "/v1/billing/estimate", body=facts, locale=locale), 12))
    except EasyAPIError as error:
        if error.status not in _ENDPOINT_UNAVAILABLE:
            raise
        if published := await _published_quote(client, facts, locale, rates_only=False):
            return published
        if facts["operation"] in _IMAGE_OPERATIONS:
            if estimate := _authenticated_image_quote(client, row, pricing, facts):
                image_pricing = {**pricing, "rates": {"image_output": pricing["rates"]["request"]}} if _is_official(client) else pricing
                return {"pricing": image_pricing, "estimate": estimate}
        return result | {"reason": "quote_unavailable"}

    version = quote.get("expression_version")
    if version == 2:
        result = {}
        published = await _published_quote(client, facts, locale, rates_only=True)
        reference = (published or {}).get("pricing")
        if reference and reference.get("expression_version") == 2:
            result["pricing"] = reference
        else:
            result["pricing"] = {"currency": "CNY", "rates": {}, "mode": "dynamic", "expression_version": 2}
    if (estimate := _amount(quote)) is None:
        reason = quote.get("reason")
        return result | {"reason": reason if isinstance(reason, str) and len(reason) <= 80 else "quote_unavailable"}
    if (original := _number(quote.get("original_amount"))) is not None:
        estimate["original_amount"] = original
    if (discount := _number(quote.get("discount"))) is not None and discount <= 1:
        estimate["discount"] = discount
    if version in (1, 2) and not isinstance(version, bool):
        estimate["expression_version"] = version
    return result | {"estimate": estimate}
