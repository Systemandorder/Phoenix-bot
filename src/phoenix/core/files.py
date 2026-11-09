"""Файли й папки: безпечний вихід у файлову систему + права. Без Telegram.

Старий бот зберігає файли в папці tasks/ поруч із Botsort.py, а права прив'язує до md5 від АБСОЛЮТНОГО
шляху на сервері (наприклад /home/ubuntu/my_project/tasks/Папка/файл.pdf). Щоб на твоєму ПК права
з копії бази діяли так само, ідентифікатор рахується від «серверного» шляху (legacy_root), а не від
локального. Таблиця server_paths (uid -> шлях) використовується як у старому боті.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .access import AccessService, path_uid
from .db import Database

log = logging.getLogger(__name__)

# Папки, які не показуються в корені (мають власні розділи меню). Як у старому get_file_keyboard.
ROOT_HIDDEN = frozenset({"Музика", "Анімації", "Архів знань"})


@dataclass(frozen=True, slots=True)
class Entry:
    uid: str
    name: str
    is_dir: bool
    path: Path


class FileService:
    def __init__(self, db: Database, access: AccessService, root: Path,
                 legacy_root: str | None = None) -> None:
        self._db = db
        self._access = access
        self.root = Path(root).resolve()
        self._legacy_root = (legacy_root or self.root.as_posix()).rstrip("/")

    # ---------- шляхи ----------
    def _inside(self, path: Path) -> Path | None:
        """Реальний шлях, якщо він у межах кореня (захист від .. і символічних посилань), інакше None."""
        try:
            real = Path(path).resolve()
            real.relative_to(self.root)
        except (ValueError, OSError):
            return None
        return real

    def legacy_path(self, path: Path) -> str | None:
        """Шлях у вигляді, як його бачив старий бот на сервері. Від нього рахуються права й uid."""
        real = self._inside(path)
        if real is None:
            return None
        rel = real.relative_to(self.root)
        return self._legacy_root if not rel.parts else f"{self._legacy_root}/{PurePosixPath(*rel.parts)}"

    def uid_of(self, path: Path) -> str | None:
        lp = self.legacy_path(path)
        return None if lp is None else path_uid(lp)

    def remember(self, path: Path) -> str | None:
        """Запам'ятати uid -> шлях (як remember_path у старому боті). Повертає uid."""
        lp = self.legacy_path(path)
        if lp is None:
            return None
        uid = path_uid(lp)
        self._db.execute("INSERT OR REPLACE INTO server_paths (uid, full_path, click_count) VALUES "
                         "(?, ?, COALESCE((SELECT click_count FROM server_paths WHERE uid = ?), 0))",
                         (uid, lp, uid))
        return uid

    def resolve(self, uid: str) -> Path | None:
        """uid з кнопки -> локальний шлях. None, якщо невідомий, поза коренем або вже не існує."""
        row = self._db.query_one("SELECT full_path FROM server_paths WHERE uid = ?", (uid,))
        if row is None or not row["full_path"]:
            return None
        full = row["full_path"].replace("\\", "/")
        if full == self._legacy_root:
            local = self.root
        elif full.startswith(self._legacy_root + "/"):
            local = self.root.joinpath(*PurePosixPath(full[len(self._legacy_root) + 1:]).parts)
        else:
            return None                      # шлях не з нашого дерева файлів
        real = self._inside(local)
        return real if real is not None and real.exists() else None

    # ---------- вміст папки ----------
    def can_open(self, path: Path, group: str | None, user_id: int) -> bool:
        lp = self.legacy_path(path)
        return lp is not None and self._access.can_access_file(lp, group, user_id)

    def list_dir(self, folder: Path, group: str | None, user_id: int) -> list[Entry]:
        """Що людина бачить у папці: без прихованих, без заборонених їй, відсортовано за назвою."""
        real = self._inside(folder)
        if real is None or not real.is_dir():
            return []
        is_root = real == self.root
        out: list[Entry] = []
        for name in sorted(p.name for p in real.iterdir()):
            if name.startswith(".") or (is_root and name in ROOT_HIDDEN):
                continue
            child = real / name
            if not self.can_open(child, group, user_id):
                continue
            uid = self.remember(child)
            if uid is not None:
                out.append(Entry(uid, name, child.is_dir(), child))
        return out

    def parent(self, folder: Path) -> Path | None:
        """Папка вище; None, якщо це корінь або шлях поза коренем."""
        real = self._inside(folder)
        if real is None or real == self.root:
            return None
        return real.parent

    def register_click(self, user_id: int, uid: str) -> None:
        with self._db.tx() as c:
            c.execute("INSERT INTO user_clicks (user_id, file_uid, clicks) VALUES (?, ?, 1) "
                      "ON CONFLICT(user_id, file_uid) DO UPDATE SET clicks = clicks + 1", (user_id, uid))
            c.execute("UPDATE server_paths SET click_count = COALESCE(click_count, 0) + 1 WHERE uid = ?", (uid,))