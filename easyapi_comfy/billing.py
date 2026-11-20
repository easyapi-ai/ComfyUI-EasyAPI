"""Read-only billing information for the saved EasyAPI connection."""
from __future__ import annotations

import asyncio
import math
import time

from .client import EasyAPIError, normalize_locale


_FACTS = {
    "model", "operation", "duration", "resolution", "size", "n", "generate_audio", "aspect_ratio",
    "has_video_input", "has_audio_input", "has_image_input", "input_image_count", "input_video_seconds",
    "input_tokens", "output_tokens", "text_characters",
}
_RATE_KEYS = ("prompt", "completion", "request", "second", "input_cache_read", "input_cache_write", "image", "audio", "audio_completion")


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


async def connection_balance(client):
    try:
        data = _data(await asyncio.wait_for(client.request("GET", "/v1/billing/balance"), 12))
    except EasyAPIError as error:
        if error.status not in {404, 405, 501}:
            raise
        data = _data(await asyncio.wait_for(client.request("GET", "/usage/token"), 12))
        total = _number(data.get("total_available"), signed=True)
        unlimited = data.get("unlimited_quota") is True
        if total is None and not unlimited:
            raise EasyAPIError("This gateway does not expose a token balance", status=502)
        result = {"token": {"unlimited": True} if unlimited else {"amount": total, "currency": "quota", "unlimited": False}}
        budget, used = _number(data.get("budget_quota")), _number(data.get("budget_used"))
        if budget and used is not None and data.get("budget_period") in (1, 2):
            result["budget"] = {"amount": max(0, budget - used), "currency": "quota", "period": data["budget_period"]}
        return result

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
    budget = data.get("budget")
    if (remaining := _amount(budget)) is not None:
        result["budget"] = {**remaining, "period": budget.get("period") if budget.get("period") in (1, 2, "daily", "monthly") else None}
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


async def model_estimate(client, value, locale=None, *, rates_only=False):
    facts = _metering_facts(value)
    locale = normalize_locale(locale)
    row = await asyncio.wait_for(client.model_billing_row(facts["model"], locale), 12)
    if row is None:
        return {"reason": "model_unavailable"}
    pricing = public_rates(row)
    result = {"pricing": pricing} if pricing else {}
    if rates_only:
        return result | {"reason": "missing_usage"}
    try:
        quote = _data(await asyncio.wait_for(client.request("POST", "/v1/billing/estimate", body=facts, locale=locale), 12))
    except EasyAPIError as error:
        if error.status not in {404, 405, 501}:
            raise
        return result | {"reason": "quote_unavailable"}

    if (estimate := _amount(quote)) is None:
        reason = quote.get("reason")
        return result | {"reason": reason if isinstance(reason, str) and len(reason) <= 80 else "quote_unavailable"}
    if (original := _number(quote.get("original_amount"))) is not None:
        estimate["original_amount"] = original
    if (discount := _number(quote.get("discount"))) is not None and discount <= 1:
        estimate["discount"] = discount
    return result | {"estimate": estimate}
