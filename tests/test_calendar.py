"""Тести календаря (без Telegram і мережі): тижні, введення, безпека, права, заміна, нагадування."""
from __future__ import annotations

import datetime as dt
import tempfile
import unittest
from pathlib import Path

from phoenix.core import calendar as cal
from phoenix.core.calendar import CalendarInputError, Kind, WeekKind
from phoenix.core.db import Database
from phoenix.core.kv import KV
from phoenix.core.migrations import migrate

D = dt.date
SEM = cal.parse_range("01.09.2026 - 13.12.2026")
EXAM = cal.parse_range("14.12.2026 - 25.12.2026")


class DatesTests(unittest.TestCase):
    def test_parse_date(self):
        self.assertEqual(cal.parse_date("5.9", 2026), D(2026, 9, 5))
        self.assertEqual(cal.parse_date("05.09.2026"), D(2026, 9, 5))
        self.assertIsNone(cal.parse_date("31.02", 2026))
        self.assertIsNone(cal.parse_date("абв", 2026))
        self.assertIsNone(cal.parse_date("5.9"))               # без року і без типового

    def test_parse_range(self):
        self.assertEqual(SEM, cal.DateRange(D(2026, 9, 1), D(2026, 12, 13)))
        self.assertEqual(cal.parse_range("01.09.2026 – 13.12.2026"), SEM)
        self.assertEqual(cal.parse_range("01.09.2026"), cal.DateRange(D(2026, 9, 1), D(2026, 9, 1)))
        self.assertIsNone(cal.parse_range("13.12.2026 - 01.09.2026"))
        self.assertIsNone(cal.parse_range("1.9 - 2.9"))
        self.assertIsNone(cal.parse_range(""))

    def test_weeks_align_to_monday_and_alternate(self):
        w = cal.week_info(D(2026, 9, 1), SEM, EXAM)             # вівторок першого тижня
        self.assertEqual((w.kind, w.abs_week, w.study_week), (WeekKind.SEMESTER, 1, 1))
        self.assertEqual(cal.week_info(D(2026, 9, 7), SEM, EXAM).study_week, 2)
        self.assertEqual(cal.week_info(D(2026, 9, 14), SEM, EXAM).abs_week, 3)

    def test_session_beats_semester_and_holidays(self):
        self.assertEqual(cal.week_info(D(2026, 12, 14), SEM, EXAM).kind, WeekKind.SESSION)
        self.assertEqual(cal.week_info(D(2026, 12, 13), SEM, None).kind, WeekKind.SEMESTER)
        self.assertEqual(cal.week_info(D(2027, 2, 1), SEM, EXAM).kind, WeekKind.HOLIDAYS)
        self.assertEqual(cal.week_info(D(2026, 9, 1), None, None).kind, WeekKind.HOLIDAYS)

    def test_shift_month(self):
        self.assertEqual(cal.shift_month(2026, 12, 1), (2027, 1))
        self.assertEqual(cal.shift_month(2026, 1, -1), (2025, 12))


class InputTests(unittest.TestCase):
    TODAY = D(2026, 11, 10)

    def test_deadline_with_subject(self):
        self.assertEqual(cal.parse_deadline_input("5.12 Бази даних - Здача звіту", today=self.TODAY),
                         (D(2026, 12, 5), "Бази даних", "Здача звіту"))

    def test_hyphenated_words_are_not_split(self):
        _, subj, desc = cal.parse_deadline_input("5.12 лаба-5 до ФБ-56", today=self.TODAY)
        self.assertEqual((subj, desc), ("Загальне", "лаба-5 до ФБ-56"))

    def test_fixed_day(self):
        self.assertEqual(cal.parse_deadline_input("АШПЗ - лаба 6", today=self.TODAY, fixed_day=D(2026, 11, 20)),
                         (D(2026, 11, 20), "АШПЗ", "лаба 6"))

    def test_bad_input_gives_clear_error(self):
        for bad in ("", "32.01 щось", "5.12", "abc def"):
            with self.assertRaises(CalendarInputError, msg=bad):
                cal.parse_deadline_input(bad, today=self.TODAY)
        with self.assertRaises(CalendarInputError):
            cal.parse_deadline_input("5.12 " + "x" * 600, today=self.TODAY)


class SafetyTests(unittest.TestCase):
    def test_html_is_escaped(self):
        self.assertEqual(cal.esc("a < b & c"), "a &lt; b &amp; c")
        self.assertEqual(cal.esc(cal.esc("a < b")), "a &lt; b")            # без подвійного екранування

    def test_sanitize_keeps_allowed_and_kills_rest(self):
        raw = "<b>ok</b> <script>x</script> <a href='https://t.me/x'>l</a> <a href='javascript:1'>bad</a> 5 < 6"
        out = cal.sanitize_html(raw)
        self.assertIn("<b>ok</b>", out)
        self.assertIn("<a href='https://t.me/x'>l</a>", out)
        self.assertNotIn("<script>", out)
        self.assertNotIn("javascript:1'>", out)
        self.assertEqual(cal.sanitize_html(out), out)                       # повторно нічого не міняє

    def test_exam_template_escapes_and_validates_link(self):
        fields = cal.parse_event_template("Предмет: Мат <b>\nЧас: 10:00\nПосилання: https://x.com/a'onclick='1\n"
                                          "Місце: 301 & 302\nПароль: -")
        desc = cal.build_event_desc(Kind.EXAM, fields)
        self.assertIn("Мат &lt;b&gt;", desc)
        self.assertIn("301 &amp; 302", desc)
        self.assertNotIn("href", desc)                                      # лапка в посиланні, посилання відкинуто
        ok = cal.build_event_desc(Kind.ZALIK, {"предмет": "БД", "час": "9:00", "посилання": "https://zoom.us/j/1"})
        self.assertIn("<a href='https://zoom.us/j/1'>", ok)

    def test_template_errors(self):
        with self.assertRaises(CalendarInputError):
            cal.parse_event_template("Час: 10:00")
        with self.assertRaises(CalendarInputError):
            cal.parse_event_template("Предмет: БД\nЧас: десята")

    def test_edit_roundtrip(self):
        src = {"предмет": "Мат & фіз", "час": "10:00", "місце": "301", "посилання": "https://a.b/c",
               "айді": "123", "пароль": "p", "викладач": "Іванов І."}
        back = cal.event_fields(cal.build_event_desc(Kind.EXAM, src))
        self.assertEqual(back, src)


def make_event(i, desc, subject="Загальне", day=D(2026, 11, 20), group="G"):
    return cal.Event(i, group, day, cal.display_subject(subject, desc), desc)


class EventTests(unittest.TestCase):
    def test_kind_icon_num(self):
        self.assertEqual(make_event(1, "[EXAM] <b>БД</b>").kind, Kind.EXAM)
        self.assertEqual(make_event(1, "лаба 5").icon, "🧪")
        self.assertEqual(make_event(1, "лаба 12").num, 12)
        self.assertEqual(make_event(1, "консультація").num, 999)

    def test_subject_from_exam_html(self):
        self.assertEqual(make_event(1, "[EXAM] <b>Вища мат</b>\n").subject, "Вища мат")
        self.assertEqual(make_event(1, "лаба 1").subject, cal.OTHER)

    def test_title_is_safe(self):
        self.assertEqual(make_event(1, "a < b & c").title, "a &lt; b &amp; c")

    def test_newest_duplicate_wins_and_past_is_hidden(self):
        today = D(2026, 11, 10)
        evs = [make_event(1, "лаба 5", "АШПЗ", D(2026, 11, 15)), make_event(2, "Лаба 5", "АШПЗ", D(2026, 11, 25)),
               make_event(3, "консультація", "АШПЗ", D(2026, 11, 12)), make_event(4, "консультація", "АШПЗ", D(2026, 11, 13)),
               make_event(5, "лаба 1", "АШПЗ", D(2026, 11, 1))]
        got = cal.upcoming(evs, today)["АШПЗ"]
        self.assertEqual(sorted(e.id for e in got), [2, 3, 4])               # без номера не склеюємо, минуле прибрано

    def test_sort_by_number_then_date(self):
        evs = [make_event(1, "лаба 10", "А"), make_event(2, "лаба 2", "А")]
        self.assertEqual([e.id for e in cal.upcoming(evs, D(2026, 11, 1))["А"]], [2, 1])

    def test_block_fits_telegram_limit(self):
        evs = [make_event(i, f"лаба {i}", f"Предмет {i % 9}", D(2026, 11, 20) + dt.timedelta(days=i)) for i in range(1, 400)]
        text = cal.events_block(evs, D(2026, 11, 10))
        self.assertLessEqual(len(text), 3300 + 40)
        self.assertEqual(text.count("<b>") , text.count("</b>"))

    def test_when_label_plural(self):
        self.assertEqual(cal.when_label(0), "сьогодні")
        self.assertEqual(cal.when_label(2), "післязавтра")
        self.assertEqual(cal.when_label(5), "через 5 днів")
        self.assertEqual(cal.when_label(21), "через 21 день")
        self.assertEqual(cal.when_label(22), "через 22 дні")
        self.assertEqual(cal.when_label(12), "через 12 днів")


class GridTests(unittest.TestCase):
    def test_marks_today_and_outside_days(self):
        evs = [make_event(1, "лаба 1", day=D(2026, 11, 20)), make_event(2, "[EXAM] <b>БД</b>", day=D(2026, 11, 20)),
               make_event(3, "[ZALIK] <b>X</b>", day=D(2026, 11, 21))]
        rows = cal.month_grid(2026, 11, evs, D(2026, 11, 10), SEM, EXAM)
        cells = {c.day: c.label for r in rows for c in r.cells}
        self.assertEqual(cells[D(2026, 11, 20)], "20❗️🎓")
        self.assertEqual(cells[D(2026, 11, 21)], "21📒")
        self.assertEqual(cells[D(2026, 11, 10)], "[10]")
        self.assertEqual(cells[D(2026, 10, 31)], "·31·")
        self.assertTrue(all(len(r.cells) == 7 for r in rows))
        self.assertEqual(rows[0].info.kind, WeekKind.SEMESTER)


class ServiceCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db = Database(Path(self._tmp.name) / "t.db")
        self.addCleanup(self.db.close)
        migrate(self.db)
        self.kv = KV(self.db)
        self.svc = cal.CalendarService(self.db, self.kv)


class ServiceTests(ServiceCase):
    def test_ranges_use_legacy_keys(self):
        self.svc.set_range("ФБ-56", "01.09.2026 - 13.12.2026", exam=False)
        self.assertEqual(self.kv.get("SEM_START_ФБ-56"), "01.09.2026 - 13.12.2026")
        self.assertEqual(self.svc.get_range("ФБ-56", exam=False), SEM)
        self.svc.set_range("ФБ-56", "0", exam=False)
        self.assertIsNone(self.svc.get_range("ФБ-56", exam=False))
        with self.assertRaises(CalendarInputError):
            self.svc.set_range("ФБ-56", "вчора", exam=True)

    def test_reads_legacy_rows_and_skips_broken_dates(self):
        self.db.execute("INSERT INTO calendar_deadlines (user_id, group_name, deadline_date, description, subject) "
                        "VALUES (1,'G','20.11.2026','лаба 3','БД'), (1,'ALL','21.11.2026','збори','Загальне'), "
                        "(1,'G','сміття','x','БД'), (1,'OTHER','22.11.2026','чуже','БД')")
        evs = self.svc.events("G")
        self.assertEqual(sorted(e.desc for e in evs), ["збори", "лаба 3"])

    def test_add_replaces_old_same_work_in_same_group_only(self):
        a, _ = self.svc.add(1, "G", D(2026, 11, 15), "АШПЗ", "лаба 5")
        other, _ = self.svc.add(2, "H", D(2026, 11, 15), "АШПЗ", "лаба 5")
        new, replaced = self.svc.add(1, "G", D(2026, 11, 25), "АШПЗ", "Лаба 5")
        self.assertEqual(replaced, 1)
        self.assertIsNone(self.svc.get(a))
        self.assertIsNotNone(self.svc.get(other))
        self.assertEqual(self.svc.get(new).date, D(2026, 11, 25))
        _, replaced = self.svc.add(1, "G", D(2026, 11, 26), "АШПЗ", "консультація")
        _, replaced2 = self.svc.add(1, "G", D(2026, 11, 27), "АШПЗ", "консультація")
        self.assertEqual((replaced, replaced2), (0, 0))

    def test_permissions(self):
        mine, _ = self.svc.add(1, "G", D(2026, 11, 15), "А", "лаба 1")
        shared, _ = self.svc.add(1, "ALL", D(2026, 11, 16), "А", "збори")
        self.assertFalse(self.svc.delete(mine, "H", False))               # чужа група
        self.assertFalse(self.svc.update(mine, "H", False, desc="зламано"))
        self.assertFalse(self.svc.delete(shared, "G", False))             # спільну чіпає лише адмін
        self.assertTrue(self.svc.update(mine, "G", False, desc="лаба 1 ok", subject="Б"))
        self.assertEqual(self.svc.get(mine).subject, "Б")
        self.assertTrue(self.svc.delete(shared, "G", True))
        self.assertTrue(self.svc.delete(mine, "G", False))
        self.assertFalse(self.svc.delete(999, "G", True))

    def test_notify_toggle(self):
        self.assertFalse(self.svc.notify_enabled(7))
        self.svc.set_notify(7, True)
        self.assertTrue(self.svc.notify_enabled(7))
        self.svc.set_notify(7, False)
        self.assertFalse(self.svc.notify_enabled(7))

    def test_reminders(self):
        today = D(2026, 11, 10)
        self.db.execute("INSERT INTO users (user_id, first_name, last_name, group_name) VALUES (7,'А','Б','G'), (8,'В','Г','G')")
        self.svc.set_notify(7, True)
        self.kv.set("AUTO_DEADLINE_abc", "1")                              # биті ключі не валять розсилку
        for days, desc in ((1, "лаба 1"), (3, "лаба 2"), (5, "[EXAM] <b>БД</b>"), (4, "лаба 3"), (0, "лаба 0")):
            self.svc.add(1, "G", today + dt.timedelta(days=days), "А", desc)
        got = self.svc.reminders(today)
        self.assertEqual(list(got), [7])                                   # 8 не вмикав нагадування
        self.assertEqual({d: len(v) for d, v in got[7].items()}, {1: 1, 3: 1, 5: 1})
        text = cal.reminder_text(got[7])
        self.assertIn("НА ЗАВТРА", text)
        self.assertIn("🎓 ЕКЗАМЕН:", text)
        self.assertEqual(self.svc.reminders(today + dt.timedelta(days=30)), {})

    def test_snapshot(self):
        self.svc.set_range("G", "01.09.2026 - 13.12.2026", exam=False)
        self.svc.add(1, "G", D(2026, 11, 20), "А", "лаба 1")
        snap = self.svc.snapshot("G")
        self.assertEqual((snap.group, len(snap.events), snap.sem), ("G", 1, SEM))
        self.assertIsNone(snap.exam)


if __name__ == "__main__":
    unittest.main()
