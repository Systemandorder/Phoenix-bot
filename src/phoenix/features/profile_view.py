"""Текст «хто ти» (імʼя, група, рівень). Спільний для /start і реєстрації."""
from __future__ import annotations

import html

from ..core.access import Profile, Tier

TIER_NAMES = {1: "Р1 (гість)", 2: "Р2 (студент)", 3: "Р3 (модератор)", 4: "Адмін"}


def profile_text(profile: Profile, tier: Tier, fallback_name: str = "") -> str:
    name = " ".join(p for p in (profile.first_name, profile.last_name) if p) or fallback_name
    return (f"👋 <b>{html.escape(name)}</b>\n"
            f"Група: {html.escape(profile.group_name or '—')}\n"
            f"Рівень доступу: {TIER_NAMES[int(tier)]}")