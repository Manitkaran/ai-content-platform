"""Prompt-template store (CR-022).

A template is a named, reusable Whisper ``initial_prompt`` preset — so a repeat
operator (single operator, D10) does not retype a long domain vocabulary each run.

Persisted in the same SQLite DB as jobs (CR-017) so templates survive a restart;
one row per template keyed by ``name``. No auth (D10). No LLM — a template is just a
stored prompt string (D4).
"""

from __future__ import annotations

import re
import sqlite3
import threading
from dataclasses import dataclass
from functools import lru_cache

from app.config import get_settings
from app.jobs.store import _db_path_from_url

_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class TemplateNotFound(KeyError):
    """No template with the given name (→ 404 / 422 at the API)."""


class InvalidTemplateName(ValueError):
    """Name is empty or has characters outside ``[A-Za-z0-9_-]{1,64}``."""


@dataclass(frozen=True)
class Template:
    """A named, reusable prompt preset."""

    name: str
    prompt: str
    description: str = ""


def validate_name(name: str) -> str:
    if not _NAME_RE.match(name or ""):
        raise InvalidTemplateName(
            f"template name {name!r} must match [A-Za-z0-9_-] (1-64 chars)"
        )
    return name


class TemplateStore:
    """SQLite-backed template store. Thread-safe via a single lock."""

    def __init__(self, db_path: str) -> None:
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS templates ("
            "name TEXT PRIMARY KEY, prompt TEXT NOT NULL, "
            "description TEXT NOT NULL DEFAULT '')"
        )
        self._conn.commit()

    def upsert(self, template: Template) -> None:
        validate_name(template.name)
        with self._lock:
            self._conn.execute(
                "INSERT INTO templates (name, prompt, description) VALUES (?, ?, ?) "
                "ON CONFLICT(name) DO UPDATE SET "
                "prompt=excluded.prompt, description=excluded.description",
                (template.name, template.prompt, template.description),
            )
            self._conn.commit()

    def get(self, name: str) -> Template:
        with self._lock:
            row = self._conn.execute(
                "SELECT name, prompt, description FROM templates WHERE name = ?",
                (name,),
            ).fetchone()
        if row is None:
            raise TemplateNotFound(name)
        return Template(name=row[0], prompt=row[1], description=row[2])

    def all(self) -> list[Template]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT name, prompt, description FROM templates ORDER BY name"
            ).fetchall()
        return [Template(name=r[0], prompt=r[1], description=r[2]) for r in rows]

    def delete(self, name: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM templates WHERE name = ?", (name,))
            self._conn.commit()


class _MemoryTemplateStore(TemplateStore):
    """In-memory fallback for a ``:memory:``/unparseable DB URL (parity with jobs)."""

    def __init__(self) -> None:  # noqa: D401
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(":memory:", check_same_thread=False)
        self._conn.execute(
            "CREATE TABLE templates (name TEXT PRIMARY KEY, prompt TEXT NOT NULL, "
            "description TEXT NOT NULL DEFAULT '')"
        )
        self._conn.commit()


@lru_cache
def get_template_store() -> TemplateStore:
    """Process-wide template store, on the same DB file as jobs (CR-017/CR-022)."""

    db_path = _db_path_from_url(get_settings().database_url)
    if db_path is None:
        return _MemoryTemplateStore()
    return TemplateStore(db_path)


def reset_template_store_cache() -> None:
    get_template_store.cache_clear()
