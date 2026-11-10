"""Календар: тижні семестру, сітка місяця, події (дедлайн / екзамен / залік), нагадування.

Тут немає нічого від Telegram. Чиста логіка плюс CalendarService поверх Database і KV,
тож усе перевіряється тестами без бота. Формат даних сумісний зі старим ботом:
таблиця calendar_deadlines (дата DD.MM.YYYY), ключі SEM_START_<група>, EXAM_START_<група>,
AUTO_DEADLINE_<user_id>. Екзамен і залік зберігаються як HTML з тегом [EXAM] або [ZALIK].

Що виправлено порівняно зі старим ботом:
* текст людини екранується, тож символ < чи & більше не ламає повідомлення;
* посилання приймаються лише http(s) без лапок, тож у href не можна підсунути атрибут;
* дата перевіряється під час введення (раніше биту дату мовчки зберігали і вона зникала);
* змінити чи видалити подію може адмін або людина з тієї ж групи, а не будь-хто за id;
* заміна старої події тієї самої роботи не зачіпає події інших груп.
"""
from __future__ import annotations

import calendar as _cal
import datetime as dt
import html
import re
from collections import defaultdict
from dataclasses import dataclass
from enum import Enum
from zoneinfo import ZoneInfo

from .db import Database
from .kv import KV

UA_MONTHS = ["", "Січень", "Лютий", "Березень", "Квітень", "Травень", "Червень",
             "Липень", "Серпень", "Вересень", "Жовтень", "Листопад", "Грудень"]
WEEKDAYS = ["пн", "вт", "ср", "чт", "пт", "сб", "нд"]
DATE_FMT = "%d.%m.%Y"
LEGEND = "🔴 сьогодні/завтра · 🟠 до 3 днів · 🟡 до тижня · 🟢 далі"
OTHER = "Інше"
REMIND_DAYS = (1, 3, 5)
MAX_DESC, MAX_SUBJECT = 500, 60


class CalendarInputError(ValueError):
    """Людина ввела щось хибне. Текст помилки можна показувати їй як є."""


# ---------------- дати та діапазони ----------------
def parse_date(raw: str | None, default_year: int | None = None) -> dt.date | None:
    """'5.9', '05.09', '05.09.2026' -> date. None, якщо дати не існує (31.02)."""
    m = re.fullmatch(r"\s*(\d{1,2})\.(\d{1,2})(?:\.(\d{4}))?\s*", raw or "")
    if not m:
        return None
    year = int(m.group(3)) if m.group(3) else default_year
    if year is None:
        return None
    try:
        return dt.date(year, int(m.group(2)), int(m.group(1)))
    except ValueError:
        return None


@dataclass(frozen=True, slots=True)
class DateRange:
    start: dt.date
    end: dt.date

    def text(self) -> str:
        return f"{self.start.strftime(DATE_FMT)} - {self.end.strftime(DATE_FMT)}"


def parse_range(raw: str | None) -> DateRange | None:
    """'01.09.2026 - 13.12.2026' (також – і —). Одна дата це діапазон з одного дня."""
    parts = [p for p in re.split(r"\s*[-–—]\s*", (raw or "").strip()) if p]
    if not 1 <= len(parts) <= 2:
        return None
    first = parse_date(parts[0], None) if parts[0].count(".") == 2 else None
    last = parse_date(parts[-1], None) if parts[-1].count(".") == 2 else None
    if first is None or last is None or last < first:
        return None
    return DateRange(first, last)


class WeekKind(Enum):
    SEMESTER = "semester"
    SESSION = "session"
    HOLIDAYS = "holidays"


@dataclass(frozen=True, slots=True)
class WeekInfo:
    kind: WeekKind
    abs_week: int = 0
    study_week: int = 0          # 1 або 2: непарний чи парний тиждень

    def label(self) -> str:
        if self.kind is WeekKind.SESSION:
            return "🎓 Сесія (Екзамени / Заліки)"
        if self.kind is WeekKind.SEMESTER:
            return f"📚 {self.abs_week}-й тиждень ({self.study_week}-й навчальний)"
        return "🏖 Канікули"


def _monday(day: dt.date) -> dt.date:
    return day - dt.timedelta(days=day.weekday())


def week_info(day: dt.date, sem: DateRange | None, exam: DateRange | None) -> WeekInfo:
    """Тижні рахуються від понеділка. Сесія сильніша за семестр. Межі розширено до цілих тижнів."""
    if exam and _monday(exam.start) <= day <= exam.end + dt.timedelta(days=6 - exam.end.weekday()):
        return WeekInfo(WeekKind.SESSION)
    if sem and _monday(sem.start) <= day <= sem.end + dt.timedelta(days=6 - sem.end.weekday()):
        n = (day - _monday(sem.start)).days // 7 + 1
        return WeekInfo(WeekKind.SEMESTER, n, 1 if n % 2 else 2)
    return WeekInfo(WeekKind.HOLIDAYS)


# ---------------- безпечний HTML ----------------
_ALLOWED = re.compile(r"^</?(?:b|i|code)>$|^<a href=(['\"])(https?://[^\s'\"<>]+)\1>$|^</a>$")


def esc(text: str | None) -> str:
    return html.escape(html.unescape(text or ""), quote=False)


def sanitize_html(raw: str | None) -> str:
    """Лишає лише <b> <i> <code> <a href=http(s)>, решту екранує. Повторний виклик нічого не міняє."""
    out = []
    for token in re.split(r"(<[^>]*>)", raw or ""):
        if token.startswith("<") and token.endswith(">") and _ALLOWED.match(token):
            out.append(token)
        else:
            out.append(esc(token))
    return "".join(out)


def strip_html(raw: str | None) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", raw or ""))


def safe_url(url: str | None) -> str | None:
    u = (url or "").strip()
    return u if re.fullmatch(r"https?://[^\s'\"<>]+", u) else None


# ---------------- події ----------------
class Kind(Enum):
    DEADLINE = "deadline"
    EXAM = "EXAM"
    ZALIK = "ZALIK"


def kind_of(desc: str) -> Kind:
    d = (desc or "").lstrip()
    return Kind.EXAM if d.startswith("[EXAM]") else Kind.ZALIK if d.startswith("[ZALIK]") else Kind.DEADLINE


@dataclass(frozen=True, slots=True)
class Event:
    id: int
    group: str
    date: dt.date
    subject: str
    desc: str                     # як у базі (для екзамену це HTML)

    @property
    def kind(self) -> Kind:
        return kind_of(self.desc)

    @property
    def num(self) -> int:
        m = re.search(r"\d+", strip_html(self.desc))
        return int(m.group()) if m else 999

    @property
    def icon(self) -> str:
        low = self.desc.lower()
        if self.kind is Kind.EXAM:
            return "🎓"
        if self.kind is Kind.ZALIK:
            return "📒"
        if "лаба" in low or "лаб." in low:
            return "🧪"
        if "дз" in low or "практика" in low or "практична" in low:
            return "📝"
        return "⏳"

    @property
    def title(self) -> str:
        """Короткий опис у рядок: без тегів, у один рядок. Уже екранований."""
        body = self.desc.replace("[EXAM]", "").replace("[ZALIK]", "").strip()
        if self.kind is not Kind.DEADLINE:
            label = "Екзамен" if self.kind is Kind.EXAM else "Залік"
            m = re.search(r"Час:(?:</i>)?\s*<code>([^<]+)</code>", self.desc)
            return esc(label + (f" · {m.group(1).strip()}" if m else ""))
        first = strip_html(body.split("\n", 1)[0]).strip().rstrip(" .;,")
        return esc(first) or "—"

    @property
    def work_key(self) -> str | None:
        """Ключ «та сама робота». None, якщо дедуплікувати не можна (консультація без номера)."""
        first = strip_html(self.desc.replace("[EXAM]", "").replace("[ZALIK]", "").split("\n", 1)[0]).lower()
        norm = re.sub(r"[^\w]+", " ", first).strip()
        if self.kind is not Kind.DEADLINE:
            return f"{self.kind.value}:{norm}"
        return norm if re.search(r"\d", norm) else None


def display_subject(subject: str | None, desc: str) -> str:
    if subject and subject != "Загальне":
        return subject.strip()
    m = re.search(r"<b>(.+?)</b>", desc or "")
    if m and kind_of(desc) is not Kind.DEADLINE:        # у екзамену предмет у <b>…</b>
        return strip_html(m.group(1)).strip() or OTHER
    return OTHER


def _row_to_event(row) -> Event | None:
    day = parse_date(row["deadline_date"], None) if (row["deadline_date"] or "").count(".") == 2 else None
    if day is None:
        return None
    desc = row["description"] or ""
    return Event(row["id"], row["group_name"] or "", day, display_subject(row["subject"], desc), desc.strip())


# ---------------- введення людини ----------------
def parse_deadline_input(text: str, *, today: dt.date, fixed_day: dt.date | None = None) -> tuple[dt.date, str, str]:
    """'14.09 Бази даних - Здача звіту' -> (дата, предмет, опис). Якщо день уже відомий, текст це 'Предмет - Опис'.
    Предмет відділяється рядком ' - ' (з пробілами), тож 'ФБ-56' чи 'лаба-5' не розриваються."""
    text = " ".join((text or "").split())
    if fixed_day is not None:
        day, rest = fixed_day, text
    else:
        head, _, rest = text.partition(" ")
        day = parse_date(head, today.year)
        if day is None or not rest.strip():
            raise CalendarInputError("Напиши ДАТУ і ТЕКСТ, наприклад: 14.09 Бази даних - Здача звіту")
    m = re.search(r"\s[-–—]\s", rest)
    subject, desc = (rest[:m.start()].strip(), rest[m.end():].strip()) if m else ("Загальне", rest.strip())
    if not desc:
        raise CalendarInputError("Опис завдання порожній.")
    if len(desc) > MAX_DESC or len(subject) > MAX_SUBJECT:
        raise CalendarInputError(f"Задовго: опис до {MAX_DESC}, предмет до {MAX_SUBJECT} символів.")
    return day, subject or "Загальне", desc


EXAM_FIELDS = ("предмет", "час", "місце", "посилання", "айді", "пароль", "викладач")


def parse_event_template(text: str) -> dict[str, str]:
    data: dict[str, str] = {}
    for line in (text or "").split("\n"):
        key, sep, val = line.partition(":")
        if sep and key.strip().lower() in EXAM_FIELDS:
            data[key.strip().lower()] = val.strip()
    if not data.get("предмет") or data["предмет"] == "-":
        raise CalendarInputError("У шаблоні потрібен рядок «Предмет: …».")
    if re.fullmatch(r"\d{1,2}:\d{2}", data.get("час", "")) is None and data.get("час", "-") != "-":
        raise CalendarInputError("Час у форматі 10:00 (або «-»).")
    return data


def build_event_desc(kind: Kind, f: dict[str, str]) -> str:
    """Збирає HTML екзамену чи заліку. Усі поля людини екрановано, посилання перевірено."""
    def has(key: str) -> bool:
        return bool(f.get(key)) and f[key] != "-"
    out = f"[{kind.value}] <b>{esc(f['предмет'])}</b>\n⏰ <i>Час:</i> <code>{esc(f.get('час') or '00:00')}</code>\n"
    if has("викладач"):
        out += f"👨‍🏫 <i>Викладач:</i> {esc(f['викладач'])}\n"
    if has("місце"):
        out += f"🏛 <i>Місце:</i> {esc(f['місце'])}\n"
    url = safe_url(f.get("посилання"))
    if url:
        out += f"🔗 <a href='{url}'>Підключитися</a>\n"
    if has("айді"):
        out += f"🆔 <i>ID:</i> <code>{esc(f['айді'])}</code>\n"
    if has("пароль"):
        out += f"🔑 <i>Пароль:</i> <code>{esc(f['пароль'])}</code>"
    return out.rstrip()


def event_fields(desc: str) -> dict[str, str]:
    """Зворотний розбір для редагування: HTML екзамену -> поля шаблону."""
    def grab(pattern: str) -> str:
        m = re.search(pattern, desc or "")
        return html.unescape(m.group(1)).strip() if m else "-"
    return {"предмет": grab(r"\] <b>(.*?)</b>"), "час": grab(r"Час:</i> <code>(.*?)</code>"),
            "місце": grab(r"Місце:</i> (.*?)(?:\n|$)"), "посилання": grab(r"href=['\"](.*?)['\"]"),
            "айді": grab(r"ID:</i> <code>(.*?)</code>"), "пароль": grab(r"Пароль:</i> <code>(.*?)</code>"),
            "викладач": grab(r"Викладач:</i> (.*?)(?:\n|$)")}


# ---------------- сітка місяця ----------------
@dataclass(frozen=True, slots=True)
class Cell:
    day: dt.date
    label: str


@dataclass(frozen=True, slots=True)
class WeekRow:
    info: WeekInfo
    cells: tuple[Cell, ...]


def month_grid(year: int, month: int, events: list[Event], today: dt.date,
               sem: DateRange | None, exam: DateRange | None) -> list[WeekRow]:
    kinds: dict[dt.date, set[Kind]] = defaultdict(set)
    for ev in events:
        kinds[ev.date].add(ev.kind)
    rows = []
    for week in _cal.Calendar().monthdatescalendar(year, month):
        cells = []
        for d in week:
            k = kinds.get(d, set())
            label = str(d.day) + ("❗️" if Kind.DEADLINE in k else "") + ("📒" if Kind.ZALIK in k else "") \
                + ("🎓" if Kind.EXAM in k else "")
            if d.month != month:
                label = f"·{label}·"
            if d == today:
                label = f"[{label}]"
            cells.append(Cell(d, label))
        rows.append(WeekRow(week_info(week[0], sem, exam), tuple(cells)))
    return rows


def shift_month(year: int, month: int, step: int) -> tuple[int, int]:
    index = year * 12 + (month - 1) + step
    return index // 12, index % 12 + 1


# ---------------- текст списку подій ----------------
def _plural(n: int, one: str, few: str, many: str) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return one
    return few if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14 else many


def when_label(days: int) -> str:
    if days <= 0:
        return "сьогодні"
    return {1: "завтра", 2: "післязавтра"}.get(days) or f"через {days} {_plural(days, 'день', 'дні', 'днів')}"


def dot(days: int) -> str:
    return "🔴" if days <= 1 else "🟠" if days <= 3 else "🟡" if days <= 7 else "🟢"


def upcoming(events: list[Event], today: dt.date, *, by_date: bool = False) -> dict[str, list[Event]]:
    """Лише майбутні. Якщо та сама робота записана двічі, лишається новіший запис (більший id)."""
    newest: dict[tuple[str, str], int] = {}
    for ev in events:
        if ev.work_key is not None:
            key = (ev.subject.lower(), ev.work_key)
            newest[key] = max(newest.get(key, 0), ev.id)
    grouped: dict[str, list[Event]] = defaultdict(list)
    for ev in events:
        if ev.work_key is not None and newest[(ev.subject.lower(), ev.work_key)] != ev.id:
            continue
        if ev.date >= today:
            grouped[ev.subject].append(ev)
    for evs in grouped.values():
        evs.sort(key=(lambda e: (e.date, e.num)) if by_date else (lambda e: (e.num, e.date)))
    return grouped


def _render(grouped: dict[str, list[Event]], per_subject: int | None, today: dt.date) -> str:
    out: list[str] = []
    for subj in sorted(grouped, key=lambda s: (s == OTHER, s.lower())):
        evs = grouped[subj]
        shown = evs if per_subject is None else evs[:per_subject]
        hidden = len(evs) - len(shown)
        out.append(f"📚 <b>{esc(subj).upper()}</b>")
        for i, ev in enumerate(shown):
            days = (ev.date - today).days
            tail = "└" if i == len(shown) - 1 and not hidden else "├"
            date_txt = ev.date.strftime("%d.%m") + (ev.date.strftime(".%y") if ev.date.year != today.year else "")
            out.append(f"{tail} {dot(days)} <code>{date_txt} {WEEKDAYS[ev.date.weekday()]}</code> · "
                       f"{when_label(days)} · {ev.icon} <i>{ev.title}</i>")
        if hidden:
            out.append(f"└ <i>… ще {hidden} {_plural(hidden, 'подія', 'події', 'подій')}</i>")
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def events_block(events: list[Event], today: dt.date, per_subject: int | None = 5, limit: int = 3300) -> str:
    """Список подій без заголовка. Скорочується, щоб влізти в ліміт Telegram, і не ріже тег навпіл."""
    grouped = upcoming(events, today)
    if not grouped:
        return ""
    n = per_subject
    text = _render(grouped, n, today)
    while n and n > 1 and len(text) > limit:
        n -= 1
        text = _render(grouped, n, today)
    if len(text) > limit:
        text = text[:limit].rsplit("\n", 1)[0] + "\n<i>… список скорочено</i>"
    return text


# ---------------- сервіс ----------------
@dataclass(frozen=True, slots=True)
class Snapshot:
    """Усе, що потрібно намалювати календар групи. Береться з бази один раз."""
    group: str
    today: dt.date
    events: list[Event]
    sem: DateRange | None
    exam: DateRange | None


class CalendarService:
    def __init__(self, db: Database, kv: KV, timezone: str = "Europe/Kyiv") -> None:
        self._db, self._kv, self._tz = db, kv, ZoneInfo(timezone)

    def today(self) -> dt.date:
        return dt.datetime.now(self._tz).date()

    # --- діапазони (ті самі ключі, що в старому боті) ---
    @staticmethod
    def _range_key(group: str, exam: bool) -> str:
        return f"{'EXAM' if exam else 'SEM'}_START_{group}"

    def get_range(self, group: str, *, exam: bool) -> DateRange | None:
        return parse_range(self._kv.get(self._range_key(group, exam)))

    def set_range(self, group: str, raw: str, *, exam: bool) -> DateRange | None:
        """'0' скидає діапазон (повертає None). Хибний формат дає CalendarInputError."""
        if raw.strip() == "0":
            self._kv.delete(self._range_key(group, exam))
            return None
        rng = parse_range(raw)
        if rng is None:
            raise CalendarInputError("Формат: 01.09.2026 - 13.12.2026 (кінець не раніше початку). 0 скидає діапазон.")
        self._kv.set(self._range_key(group, exam), rng.text())
        return rng

    # --- події ---
    def events(self, group: str) -> list[Event]:
        rows = self._db.query_all(
            "SELECT id, group_name, deadline_date, description, subject FROM calendar_deadlines "
            "WHERE group_name = ? OR group_name = 'ALL'", (group,))
        return [ev for ev in map(_row_to_event, rows) if ev is not None]

    def get(self, event_id: int) -> Event | None:
        row = self._db.query_one("SELECT id, group_name, deadline_date, description, subject "
                                 "FROM calendar_deadlines WHERE id = ?", (event_id,))
        return _row_to_event(row) if row else None

    def snapshot(self, group: str) -> Snapshot:
        return Snapshot(group, self.today(), self.events(group),
                        self.get_range(group, exam=False), self.get_range(group, exam=True))

    @staticmethod
    def can_edit(event: Event, group: str | None, is_admin: bool) -> bool:
        return is_admin or (event.group != "ALL" and event.group == group)

    def add(self, user_id: int, group: str, day: dt.date, subject: str, desc: str) -> tuple[int, int]:
        """Додає подію й видаляє СТАРІ записи тієї самої роботи в цій самій групі. -> (id, скільки замінено)."""
        with self._db.tx() as conn:
            cur = conn.execute("INSERT INTO calendar_deadlines (user_id, group_name, deadline_date, description, subject) "
                               "VALUES (?, ?, ?, ?, ?)", (user_id, group, day.strftime(DATE_FMT), desc, subject))
            new_id = int(cur.lastrowid)
            fresh = Event(new_id, group, day, display_subject(subject, desc), desc)
            replaced = 0
            if fresh.work_key is not None:
                for row in conn.execute("SELECT id, group_name, deadline_date, description, subject FROM "
                                        "calendar_deadlines WHERE group_name = ? AND id != ?", (group, new_id)):
                    old = _row_to_event(row)
                    if old and old.work_key == fresh.work_key and old.subject.lower() == fresh.subject.lower():
                        conn.execute("DELETE FROM calendar_deadlines WHERE id = ?", (old.id,))
                        replaced += 1
            return new_id, replaced

    def update(self, event_id: int, group: str | None, is_admin: bool, *, desc: str, subject: str | None = None) -> bool:
        ev = self.get(event_id)
        if ev is None or not self.can_edit(ev, group, is_admin):
            return False
        if subject is None:
            self._db.execute("UPDATE calendar_deadlines SET description = ? WHERE id = ?", (desc, event_id))
        else:
            self._db.execute("UPDATE calendar_deadlines SET description = ?, subject = ? WHERE id = ?",
                             (desc, subject, event_id))
        return True

    def delete(self, event_id: int, group: str | None, is_admin: bool) -> bool:
        ev = self.get(event_id)
        if ev is None or not self.can_edit(ev, group, is_admin):
            return False
        self._db.execute("DELETE FROM calendar_deadlines WHERE id = ?", (event_id,))
        return True

    # --- нагадування ---
    def notify_enabled(self, user_id: int) -> bool:
        return self._kv.get(f"AUTO_DEADLINE_{user_id}") == "1"

    def set_notify(self, user_id: int, on: bool) -> None:
        self._kv.set(f"AUTO_DEADLINE_{user_id}", "1" if on else "0")

    def reminders(self, today: dt.date | None = None) -> dict[int, dict[int, list[Event]]]:
        """{user_id: {1|3|5: [події]}} лише для тих, у кого нагадування ввімкнено і є що нагадати."""
        today = today or self.today()
        users: list[int] = []
        for key, value in self._kv.with_prefix("AUTO_DEADLINE_"):
            suffix = key.removeprefix("AUTO_DEADLINE_")
            if value == "1" and suffix.isdigit():
                users.append(int(suffix))
        by_group: dict[str, list[Event]] = {}
        result: dict[int, dict[int, list[Event]]] = {}
        for uid in users:
            row = self._db.query_one("SELECT group_name FROM users WHERE user_id = ?", (uid,))
            group = (row["group_name"] if row else None) or "ALL"
            if group not in by_group:
                by_group[group] = self.events(group)
            hits: dict[int, list[Event]] = {d: [] for d in REMIND_DAYS}
            for ev in by_group[group]:
                left = (ev.date - today).days
                if left in hits:
                    hits[left].append(ev)
            if any(hits.values()):
                result[uid] = {d: sorted(v, key=lambda e: e.subject.lower()) for d, v in hits.items() if v}
        return result


def reminder_text(hits: dict[int, list[Event]]) -> str:
    titles = {1: "🚨 <b>НА ЗАВТРА:</b>", 3: "⏳ <b>Через 3 дні:</b>", 5: "👀 <b>Через 5 днів:</b>"}
    out = ["🔔 <b>НАБЛИЖАЮТЬСЯ ДЕДЛАЙНИ!</b>\n"]
    for days in REMIND_DAYS:
        if days not in hits:
            continue
        out.append(titles[days])
        for ev in hits[days]:
            kind = {Kind.EXAM: "🎓 ЕКЗАМЕН: ", Kind.ZALIK: "📒 ЗАЛІК: "}.get(ev.kind, "")
            out.append(f" ├ 📚 <b>{esc(ev.subject)}:</b> <i>{kind}{ev.title}</i>"
                       + (f" ({ev.date.strftime(DATE_FMT)})" if days > 1 else ""))
        out.append("")
    return "\n".join(out).strip()
