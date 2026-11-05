"""E-posta adresi denetimi: biçim, gerçek uzantı (IANA), bilinen sağlayıcı yazım hataları, geçici adresler.

Site (email-check.ts) ile aynı kuralları aynı dosyadan (email_rules.json) okur; ikisi de aynı test örnekleriyle sınanır.
Sonuç durumları:
  ok          geçerli
  empty       boş
  invalid     biçim hatalı (ör. boşluk, @ yok, Türkçe karakter); düzeltilmiş öneri olabilir
  tld         uzantı gerçek değil (ör. .con, .cmo, .ogr)
  typo        bilinen sağlayıcıda kesin yazım hatası (ör. gmail.co, hotmail.con.tr)
  maybe       büyük olasılıkla yazım hatası (ör. gmial.com); kullanıcı "adresim doğru" diyebilir
  disposable  geçici / tek kullanımlık adres
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

RULES_PATH = Path(__file__).with_name("email_rules.json")
LOCAL_RE = re.compile(r"^[a-z0-9!#$%&'*+/=?^_`{|}~-]+(\.[a-z0-9!#$%&'*+/=?^_`{|}~-]+)*$")
LABEL_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
TURKISH = str.maketrans({"ı": "i", "İ": "i", "ş": "s", "Ş": "s", "ğ": "g", "Ğ": "g", "ü": "u", "Ü": "u", "ö": "o", "Ö": "o", "ç": "c", "Ç": "c"})
COMMON_TLDS = ("com", "net", "org", "tr", "io", "co", "de", "uk", "info", "me", "ai", "app", "dev", "edu", "gov")
AMBIGUOUS = {"co", "cm", "om", "cmo", "co.tr", "cm.tr"}  # gerçek ama sağlayıcı adreslerinde neredeyse hep yazım hatası
HARD = {"invalid", "tld", "typo", "disposable", "empty"}


@lru_cache(maxsize=1)
def rules() -> dict:
    data = json.loads(RULES_PATH.read_text(encoding="utf-8"))
    return {"providers": data["providers"], "regional": set(data["regional"]), "fuzzy": data["fuzzy"], "disposable": set(data["disposable"]), "tlds": set(data["tlds"])}


def distance(a: str, b: str) -> int:
    """Damerau-Levenshtein (bitişik harf yer değiştirme 1 sayılır)."""
    d = [[0] * (len(b) + 1) for _ in range(len(a) + 1)]
    for i in range(len(a) + 1):
        d[i][0] = i
    for j in range(len(b) + 1):
        d[0][j] = j
    for i in range(1, len(a) + 1):
        for j in range(1, len(b) + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            d[i][j] = min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + cost)
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                d[i][j] = min(d[i][j], d[i - 2][j - 2] + 1)
    return d[len(a)][len(b)]


def _nearest(word: str, options) -> str:
    return min(options, key=lambda option: (distance(word, option), list(options).index(option)))


def _syntax_ok(email: str) -> bool:
    if len(email) > 254 or email.count("@") != 1:
        return False
    local, domain = email.split("@")
    labels = domain.split(".")
    if not local or len(local) > 64 or not LOCAL_RE.match(local) or len(labels) < 2:
        return False
    if not all(LABEL_RE.match(label) for label in labels):
        return False
    tld = labels[-1]
    return tld.isalpha() or tld.startswith("xn--")


def _clean(raw: str) -> str:
    """Sık yapılan biçim hatalarını düzeltir: Türkçe harf, boşluk, virgül, çift nokta, @@."""
    value = raw.translate(TURKISH).lower()
    value = re.sub(r"\s+", "", value).replace(",", ".").replace(";", ".")
    value = re.sub(r"@+", "@", value)
    value = re.sub(r"\.{2,}", ".", value).strip(".")
    value = value.replace(".@", "@").replace("@.", "@")
    return value


def _check_valid(email: str) -> dict:
    r = rules()
    local, domain = email.split("@")
    labels = domain.split(".")
    if domain in r["disposable"] or any(domain.endswith("." + item) for item in r["disposable"]):
        return {"status": "disposable", "email": email, "suggestion": None}
    first, suffix = labels[0], ".".join(labels[1:])
    # Sağlayıcı adının arkası yalnız kısa uzantı parçalarından oluşuyorsa (com, co.uk, con.tr …) sağlayıcı adresi sayılır;
    # "mail.company.com" gibi alt alan adları sağlayıcı sayılmaz.
    tld_like = len(labels) <= 3 and all(label.isalpha() and len(label) <= 4 for label in labels[1:])
    allowed = r["providers"].get(first)
    regional_ok = first in r["regional"] and all(label in r["tlds"] for label in labels[1:]) and suffix not in AMBIGUOUS
    if allowed and tld_like and suffix not in allowed and not regional_ok:
        return {"status": "typo", "email": email, "suggestion": f"{local}@{first}.{_nearest(suffix, allowed)}"}
    if labels[-1] not in r["tlds"]:
        fixed = ".".join([*labels[:-1], _nearest(labels[-1], COMMON_TLDS)])
        if allowed is None and len(labels) == 2:
            fuzzy = _fuzzy(first, suffix=None)
            if fuzzy:
                fixed = fuzzy
        return {"status": "tld", "email": email, "suggestion": f"{local}@{fixed}"}
    if allowed is None and tld_like:
        fuzzy = _fuzzy(first, suffix)
        if fuzzy:
            return {"status": "maybe", "email": email, "suggestion": f"{local}@{fuzzy}"}
    return {"status": "ok", "email": email, "suggestion": None}


def _fuzzy(first: str, suffix: str | None) -> str | None:
    r = rules()
    if len(first) < 4 or first in r["providers"]:
        return None
    best = None
    for name in r["fuzzy"]:
        gap = distance(first, name)
        if gap <= (1 if len(name) <= 6 else 2) and (best is None or gap < best[0]):
            best = (gap, name)
    if not best:
        return None
    allowed = r["providers"][best[1]]
    return f"{best[1]}.{suffix if suffix in allowed else allowed[0]}"


def check_email(raw: str | None) -> dict:
    value = (raw or "").strip()
    if not value:
        return {"status": "empty", "email": "", "suggestion": None}
    email = value.lower()
    if _syntax_ok(email):
        return _check_valid(email)
    candidate = _clean(value)
    r = rules()
    if "@" not in candidate:
        # "aligmail.com" → "ali@gmail.com"
        for name, allowed in r["providers"].items():
            for suffix in allowed:
                tail = f"{name}.{suffix}"
                if candidate.endswith(tail) and len(candidate) > len(tail):
                    candidate = f"{candidate[:-len(tail)]}@{tail}"
                    break
            if "@" in candidate:
                break
    elif "." not in candidate.split("@", 1)[1]:
        # "ali@gmail" → "ali@gmail.com"
        local, domain = candidate.split("@", 1)
        if domain in r["providers"]:
            candidate = f"{local}@{domain}.{r['providers'][domain][0]}"
    suggestion = None
    if candidate != email and _syntax_ok(candidate):
        fixed = _check_valid(candidate)
        suggestion = fixed["suggestion"] if fixed["status"] in {"typo", "tld", "maybe"} else candidate if fixed["status"] == "ok" else None
    return {"status": "invalid", "email": email, "suggestion": suggestion}


MESSAGES_TR = {
    "empty": "E-posta adresini yaz.",
    "invalid": "Bu e-posta adresi geçerli değil.",
    "tld": "E-posta adresinin sonu hatalı görünüyor.",
    "typo": "E-posta adresinde yazım hatası var.",
    "maybe": "E-posta adresinde yazım hatası olabilir.",
    "disposable": "Geçici (tek kullanımlık) e-posta adresleri kabul edilmiyor. Kalıcı bir adres gir.",
}


def rejection_message(result: dict) -> str:
    text = MESSAGES_TR.get(result["status"], MESSAGES_TR["invalid"])
    if result.get("suggestion"):
        text += f" Bunu mu demek istedin: {result['suggestion']}"
    return text
