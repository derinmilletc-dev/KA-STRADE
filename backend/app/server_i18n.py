"""Sunucu tarafı Türkçe → İngilizce çeviri (push bildirimi, e-posta ve zil bildirimlerinin gövdesi için).

Sitedeki tarayıcı çevirmeniyle AYNI sözlüğü (kök dizindeki ``i18n-en.json``) ve aynı kuralları kullanır; böylece
bir cümle sitede nasıl çevriliyorsa bildirimde de öyle çevrilir. Sözlük bulunamaz/okunamazsa ya da cümle sözlükte
yoksa metin olduğu gibi (Türkçe) döner: bildirim asla kaybolmaz, yalnızca çevrilmemiş kalır.
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

_DICT_PATH = Path(__file__).resolve().parents[2] / "i18n-en.json"
_MAX_DEPTH = 2


def _norm(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _apo(value: str) -> str:
    return value.replace("‘", "'").replace("’", "'")


def _tr_lower(value: str) -> str:
    return value.replace("İ", "i").replace("I", "ı").lower()


def _tr_upper(value: str) -> str:
    return value.replace("i", "İ").replace("ı", "I").upper()


@lru_cache(maxsize=1)
def _dictionary() -> tuple[dict[str, str], dict[str, str], list[tuple[re.Pattern[str], str]]]:
    try:
        raw: Any = json.loads(_DICT_PATH.read_text(encoding="utf-8"))
        exact_raw = raw["e"]
        pairs = raw.get("p", [])
    except (OSError, ValueError, KeyError, TypeError):
        return {}, {}, []
    exact: dict[str, str] = {}
    for key, value in exact_raw.items():
        if isinstance(key, str) and isinstance(value, str):
            normalized = _apo(key)
            if normalized not in exact or normalized == key:
                exact[normalized] = value
    lower: dict[str, str] = {}
    for key, value in exact.items():
        lower.setdefault(_tr_lower(key), value)
    patterns: list[tuple[re.Pattern[str], str]] = []
    for pair in pairs:
        if not (isinstance(pair, list) and len(pair) == 2 and all(isinstance(item, str) for item in pair)):
            continue
        source, target = pair
        body = re.escape(_apo(source)).replace(r"\{\#\}", r"(\d[\d.,]*)").replace(r"\{\}", "(.+?)").replace("{}", "(.+?)")
        try:
            patterns.append((re.compile("^" + body + "$"), target))
        except re.error:
            continue
    return exact, lower, patterns


def _core(key: str, depth: int) -> str | None:
    exact, lower, patterns = _dictionary()
    hit = exact.get(key)
    if hit is not None:
        return hit
    low = lower.get(_tr_lower(key))
    if low is not None:
        upper = key == _tr_upper(key) and re.search(r"[a-zçğıöşü]", key, re.I) and len(key) > 2
        return low.upper() if upper else low
    if depth > _MAX_DEPTH:
        return None
    for pattern, target in patterns:
        match = pattern.match(key)
        if not match:
            continue
        groups = iter(match.groups())

        def fill(_: re.Match[str]) -> str:
            part = next(groups, "") or ""
            return _core(_norm(part), depth + 1) or part

        return re.sub(r"\{\}", fill, target)
    return None


def to_english(text: str) -> str:
    """Türkçe bir bildirim cümlesini İngilizceye çevirir; çeviri yoksa metni aynen döndürür."""
    if not isinstance(text, str) or not text.strip() or not re.search(r"[A-Za-zÇĞİÖŞÜçğıöşü]", text):
        return text
    key = _apo(_norm(text))
    # Cümle cümle de dener: "A. B." biçimli birleşik bildirimlerde her parça ayrı sözlük girdisi olabilir.
    out = _core(key, 0)
    if out is None:
        parts = re.split(r"(?<=[.!?])\s+", key)
        if len(parts) > 1:
            translated = [_core(part, 0) for part in parts]
            if all(item is not None for item in translated):
                out = " ".join(item for item in translated if item is not None)
    if out is None:
        return text
    lead = re.match(r"^\s*", text).group(0)  # type: ignore[union-attr]
    trail = re.search(r"\s*$", text).group(0)  # type: ignore[union-attr]
    return lead + out + trail


def localize(text: str, language: str | None) -> str:
    """Kullanıcının hesap diline göre metni döndürür. Yalnızca "en" çevrilir; diğer her şey Türkçe kalır."""
    return to_english(text) if language == "en" else text


async def account_language(pool: Any, user_id: str) -> str:
    """Hesabın kayıtlı dili ("tr" | "en"). Okunamazsa Türkçe: bildirim yine gider."""
    if pool is None or not user_id:
        return "tr"
    try:
        row = await pool.fetchrow("SELECT payload FROM commercial_account_settings WHERE user_id = $1", user_id)
        payload = row["payload"] if row else None
        if isinstance(payload, str):
            payload = json.loads(payload)
        value = payload.get("language") if isinstance(payload, dict) else None
    except Exception:  # bildirimi dil okunamadı diye düşürme
        return "tr"
    return "en" if value == "en" else "tr"
