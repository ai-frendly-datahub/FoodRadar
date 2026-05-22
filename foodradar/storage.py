from __future__ import annotations

from collections.abc import Iterable, Mapping

from radar_core.exceptions import StorageError
from radar_core.storage import RadarStorage as CoreRadarStorage

from .models import Source


class RadarStorage(CoreRadarStorage):
    """FoodRadar storage wrapper with source metadata columns."""

    def _ensure_tables(self) -> None:
        super()._ensure_tables()
        _ = self.conn.execute("ALTER TABLE articles ADD COLUMN IF NOT EXISTS language TEXT")

    def sync_source_metadata(
        self,
        sources: Iterable[Source],
        *,
        category: str,
        source_languages: Mapping[str, str] | None = None,
    ) -> int:
        """Persist configured source metadata onto stored article rows."""
        languages: dict[str, str] = {}
        for source in sources:
            language = str(getattr(source, "language", "") or "").strip()
            if not language:
                continue
            languages[source.name] = language
        for source_name, language in (source_languages or {}).items():
            clean_source_name = str(source_name).strip()
            clean_language = str(language).strip()
            if clean_source_name and clean_language:
                languages.setdefault(clean_source_name, clean_language)

        updates: list[tuple[str, str, str]] = [
            (language, category, source_name)
            for source_name, language in sorted(languages.items())
        ]

        if not updates:
            return 0

        changed = 0
        try:
            for language, category_name, source_name in updates:
                row = self.conn.execute(
                    """
                    SELECT COUNT(*)
                    FROM articles
                    WHERE category = ?
                      AND source = ?
                      AND COALESCE(language, '') <> ?
                    """,
                    [category_name, source_name, language],
                ).fetchone()
                changed += int(row[0]) if row else 0
                _ = self.conn.execute(
                    """
                    UPDATE articles
                    SET language = ?
                    WHERE category = ?
                      AND source = ?
                      AND COALESCE(language, '') <> ?
                    """,
                    [language, category_name, source_name, language],
                )
            _ = self.conn.commit()
        except Exception as exc:
            try:
                _ = self.conn.rollback()
            except Exception:
                pass
            raise StorageError("Failed to sync source metadata") from exc
        return changed


__all__ = ["RadarStorage", "StorageError"]
