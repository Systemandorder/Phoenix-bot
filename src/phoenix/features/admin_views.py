"""Екрани адмін-панелі: ЧИСТІ функції (дані на вході, текст і кнопки на виході). Без Telegram і бази,
тому їх легко перевіряти тестами. Кнопки це пари (підпис, callback_data)."""
from __future__ import annotations

import html
from datetime import datetime

from ..core.admin import AuditRow, GroupInfo, UserRow

Button = tuple[str, str]
Rows = list[list[Button]]
Screen = tuple[str, Rows]

PER_PAGE = 8
LOG_PER_PAGE = 12
TIER_ICON = {4: "👑", 3: "🎓", 2: "✅", 1: "👤"}
TIER_TITLE = {4: "Адмін", 3: "Куратор (Р3)", 2: "Студент (Р2)", 1: "Гість (Р1)"}


def esc(value: object) -> str:
    return html.escape(str(value if value is not None else ""))


def user_button(u: UserRow, tier: int) -> Button:
    return (f"{TIER_ICON[tier]} {u.full_name} · {u.group}", f"ADM:U:{u.user_id}")


def pages(total: int, per: int) -> int:
    return max(1, -(-total // per))


def nav_row(page: int, total: int, per: int, cb) -> list[Button]:
    """Кнопки ◀️ N/M ▶️. cb(page) -> callback_data."""
    last = pages(total, per) - 1
    row: list[Button] = []
    if page > 0:
        row.append(("◀️", cb(page - 1)))
    row.append((f"{page + 1}/{last + 1}", "ADM:NOOP"))
    if page < last:
        row.append(("▶️", cb(page + 1)))
    return row


def home_view(users: int, groups: int, curators: int) -> Screen:
    text = ("🛡 <b>Штаб адміністратора</b>\n\n"
            f"👥 Користувачів: <b>{users}</b>\n"
            f"🏷 Груп: <b>{groups}</b>\n"
            f"🎓 Кураторів: <b>{curators}</b>\n\n"
            "Оберіть розділ:\n"
            "<i>Кнопки меню і доступ до файлів по групах та людях: наступним кроком.</i>")
    rows = [[("👥 Усі користувачі", "ADM:USR:0"), ("🎓 Куратори по групах", "ADM:CUR")],
            [("🏷 Групи та підписки", "ADM:GRPS"), ("🔎 Знайти людину", "ADM:FIND")],
            [("🧾 Журнал дій", "ADM:LOG:0"), ("✖️ Закрити", "ADM:CLOSE")]]
    return text, rows


def list_view(title: str, people: list[tuple[UserRow, int]], total: int, page: int, cb, back: str,
              extra: Rows | None = None) -> Screen:
    """Сторінка зі списком людей. people це (людина, рівень)."""
    legend = "👑 адмін · 🎓 куратор · ✅ студент · 👤 гість"
    text = f"{title}\nВсього: <b>{total}</b> · сторінка {page + 1}/{pages(total, PER_PAGE)}\n<i>{legend}</i>"
    if not people:
        text += "\n\n<i>Тут поки нікого.</i>"
    rows: Rows = [[user_button(u, t)] for u, t in people]
    if total > PER_PAGE:
        rows.append(nav_row(page, total, PER_PAGE, cb))
    rows.extend(extra or [])
    rows.append([("⬅️ Штаб", "ADM:HOME")] if back == "ADM:HOME" else [("⬅️ Назад", back), ("🏠 Штаб", "ADM:HOME")])
    return text, rows


def groups_view(groups: list[GroupInfo]) -> Screen:
    text = "🏷 <b>Групи та підписки</b>\n<i>Оберіть групу: список людей і видача рівня 2 усій групі.</i>"
    if not groups:
        text += "\n\n<i>Груп ще немає.</i>"
    rows: Rows = [[(f"{g.name} · {g.users} ос. · 🎓{g.curators} · 💳{g.paid}", f"ADM:GRP:{g.name}:0")]
                  for g in groups]
    rows.append([("⬅️ Штаб", "ADM:HOME")])
    return text, rows


def group_extra(group: str) -> Rows:
    if group == "—":
        return []
    return [[(f"🎁 Р2 усій групі: {m} міс", f"ADM:GG:{group}:{m}") for m in (1, 3, 6)]]


def curators_view(by_group: dict[str, list[UserRow]]) -> Screen:
    text = "🎓 <b>Куратори по групах</b>\n"
    rows: Rows = []
    if not by_group:
        text += ("\n<i>Кураторів поки немає.</i>\n"
                 "Щоб призначити: відкрийте людину («Усі користувачі» або «Знайти людину») "
                 "і натисніть «Зробити куратором».")
    for group, people in by_group.items():
        text += f"\n📁 <b>{esc(group)}</b>\n"
        for u in people:
            contact = f"@{esc(u.username)}" if u.username else f"ID {u.user_id}"
            text += f" • {esc(u.full_name)} ({contact})\n"
            if len(rows) < 20:
                rows.append([(f"🎓 {u.full_name} · {u.group}", f"ADM:U:{u.user_id}")])
    rows.append([("⬅️ Штаб", "ADM:HOME")])
    return text, rows


def subscription_text(u: UserRow, now: datetime) -> str:
    if not u.has_paid:
        return "немає"
    if not u.subscription_end:
        return "активна (без дати завершення)"
    try:
        end = datetime.strptime(str(u.subscription_end)[:19], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return f"активна ({esc(u.subscription_end)})"
    days = (end - now).days
    if end < now:
        return f"завершилась {end:%d.%m.%Y}"
    return f"до {end:%d.%m.%Y} (ще {days} дн.)"


def card_view(u: UserRow, tier: int, reason: str, now: datetime, is_admin_target: bool) -> Screen:
    contact = f"@{esc(u.username)}" if u.username else "без тегу"
    text = (f"👤 <b>{esc(u.full_name)}</b>\n"
            f"ID: <code>{u.user_id}</code> · {contact}\n"
            f"Група: <b>{esc(u.group)}</b>\n"
            f"Рівень: {TIER_ICON[tier]} <b>{TIER_TITLE[tier]}</b>\n"
            f"<i>Чому: {esc(reason)}</i>\n"
            f"Підписка: {subscription_text(u, now)}\n"
            f"Безкоштовні кліки: {u.free_clicks}")
    back = [("⬅️ Назад", "ADM:BACK"), ("🏠 Штаб", "ADM:HOME")]
    if is_admin_target:
        return text, [back]
    uid = u.user_id
    rows: Rows = [
        [("⬇️ Зняти куратора", f"ADM:ASK:demote:{uid}:0")] if u.is_vip == 1
        else [("🎓 Зробити куратором", f"ADM:ASK:promote:{uid}:0")],
        [(f"🎁 Р2: {m} міс", f"ADM:ASK:grant:{uid}:{m}") for m in (1, 3, 12)],
        [("⏳ Завершити підписку", f"ADM:ASK:expire:{uid}:0")],
        [("🔁 Змінити групу", f"ADM:EDITG:{uid}"), ("🧮 Кліки", f"ADM:EDITC:{uid}")],
        back,
    ]
    return text, rows


def confirm_text(verb: str, u: UserRow, months: int) -> str:
    n = f"<b>{esc(u.full_name)}</b>"
    return {
        "promote": f"Зробити {n} куратором (рівень 3)?\nПідписка йому не потрібна й не спливає.",
        "demote": f"Зняти куратора з {n}?\nРівень залежатиме від групи та підписки.",
        "grant": (f"Видати {n} рівень 2 на <b>{months} міс.</b>?\nБезкоштовних кліків стане 20. "
                  "Якщо це куратор, він стане звичайним студентом."),
        "expire": f"Завершити підписку {n}?\nСтатус оплати скинеться, кліки стануть 0.",
    }[verb]


def confirm_view(verb: str, u: UserRow, months: int) -> Screen:
    uid = u.user_id
    return confirm_text(verb, u, months), [[("✅ Так", f"ADM:DO:{verb}:{uid}:{months}"),
                                            ("❌ Скасувати", f"ADM:U:{uid}")]]


def group_grant_confirm(group: str, months: int) -> Screen:
    text = (f"Видати рівень 2 усім студентам групи <b>{esc(group)}</b> на <b>{months} міс.</b>?\n"
            "Куратори не змінюються.")
    return text, [[("✅ Так", f"ADM:GGDO:{group}:{months}"), ("❌ Скасувати", f"ADM:GRP:{group}:0")]]


def _when(ts: str | None) -> str:
    try:
        return f"{datetime.strptime(str(ts)[:19], '%Y-%m-%d %H:%M:%S'):%d.%m %H:%M}"
    except ValueError:
        return esc(ts or "?")


def log_view(entries: list[AuditRow], total: int, page: int) -> Screen:
    text = f"🧾 <b>Журнал дій</b> (записів: {total})\n"
    if not entries:
        text += "\n<i>Поки порожньо.</i>"
    for e in entries:
        text += f"\n<code>{_when(e.timestamp)}</code> · {e.user_id} · <b>{esc(e.action)}</b>\n   {esc(e.target)}"
    rows: Rows = []
    if total > LOG_PER_PAGE:
        rows.append(nav_row(page, total, LOG_PER_PAGE, lambda p: f"ADM:LOG:{p}"))
    rows.append([("⬅️ Штаб", "ADM:HOME")])
    return text, rows