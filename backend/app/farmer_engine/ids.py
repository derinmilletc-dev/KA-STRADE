"""KAIS FARMER emir kimlikleri. Grid (`kg-`) ve DCA (`kd-`) önekleriyle ÇAKIŞMAZ; ≤ 36 karakter (Binance sınırı).

Biçim, Grid/DCA ile aynı sonekli deseni korur (`-<L|S><kademe 2 hane><rol><rev>`), böylece `live_adapter.order_role` ve
`is_algo_order` kimlikten rolü değişmeden okur."""
from __future__ import annotations

import re

from ..grid_engine.ports import LONG

PREFIX = "kf-"
MAX_CLIENT_ID_LENGTH = 36
FARMER_CLIENT_ID = re.compile(r"^kf-([A-Za-z0-9]{1,16})-[LS]\d{2}[ETSC]\d+$")


def farmer_client_order_id(grid_id: str, side: str, level: int, role: str, rev: int = 0) -> str:
    safe = re.sub(r"[^A-Za-z0-9]", "", grid_id)[:16]
    return f"{PREFIX}{safe}-{'L' if side == LONG else 'S'}{level:02d}{role}{rev}"[:MAX_CLIENT_ID_LENGTH]


def farmer_grid_prefix(grid_id: str) -> str:
    """Bu motorun yetim emir taraması için önek (`GridEngine.prefix` ile aynı mantık)."""
    return f"{PREFIX}{re.sub(r'[^A-Za-z0-9]', '', grid_id)[:16]}-"


def is_farmer_client_id(client_id: str) -> bool:
    return bool(FARMER_CLIENT_ID.match(str(client_id or "")))
