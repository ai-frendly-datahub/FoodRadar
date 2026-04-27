from __future__ import annotations

import json
import re
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .models import Article, CategoryConfig, Source


COLLECTED_SOURCE_TYPES = {"rss", "reddit"}
ALIAS_ENTITY_TYPES = {"Brand": "brand", "Manufacturer": "manufacturer"}
ALIAS_TRACE_PATTERN = re.compile(r"^(?P<variant>.+?)\s*->\s*(?P<canonical>.+)$")
TRACKED_EVENT_MODEL_ORDER = [
    "recall_status_change",
    "enforcement_action",
    "complaint_signal",
]
TRACKED_EVENT_MODELS = set(TRACKED_EVENT_MODEL_ORDER)
COMPACT_DATE_PATTERN = re.compile(r"(?<!\d)(20\d{2})(\d{2})(\d{2})(?!\d)")
DATE_ONLY_TEXT_PATTERN = re.compile(
    r"^\s*(?:20\d{2}\d{2}\d{2}|20\d{2}[.\-/년\s]+\d{1,2}[.\-/월\s]+\d{1,2})\s*\.?\s*$"
)
DATE_PATTERN = re.compile(
    r"(?P<year>20\d{2})\s*(?:[.\-/]|년)\s*"
    r"(?P<month>\d{1,2})\s*(?:[.\-/]|월)\s*"
    r"(?P<day>\d{1,2})"
)


def build_quality_report(
    *,
    category: CategoryConfig,
    articles: Iterable[Article],
    errors: Iterable[str] | None = None,
    quality_config: Mapping[str, object] | None = None,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    """Build source freshness/stale and alias traceability report."""
    generated_at = _as_utc(generated_at or datetime.now(UTC))
    articles_list = list(articles)
    errors_list = [str(error) for error in (errors or [])]
    quality = _dict(quality_config or {}, "data_quality")
    freshness_sla = _dict(quality, "freshness_sla")
    tracked_event_models = _tracked_event_models(quality)
    source_rows = [
        _build_source_row(
            source=source,
            articles=articles_list,
            errors=errors_list,
            freshness_sla=freshness_sla,
            tracked_event_models=tracked_event_models,
            generated_at=generated_at,
        )
        for source in category.sources
    ]
    events = _build_event_rows(
        sources=category.sources,
        articles=articles_list,
        tracked_event_models=tracked_event_models,
        freshness_sla=freshness_sla,
        generated_at=generated_at,
    )
    alias_candidates = _build_alias_candidates(articles_list)

    status_counts = Counter(str(row["status"]) for row in source_rows)
    event_counts = Counter(str(row["event_model"]) for row in events)
    event_status_counts = Counter(str(row["event_status"]) for row in events)
    food_event_keys = {
        str(row["food_event_key"])
        for row in events
        if str(row.get("food_event_key") or "")
    }
    summary = {
        "total_sources": len(source_rows),
        "tracked_sources": sum(1 for row in source_rows if row["tracked"]),
        "fresh_sources": status_counts.get("fresh", 0),
        "stale_sources": status_counts.get("stale", 0),
        "missing_sources": status_counts.get("missing", 0),
        "unknown_event_date_sources": status_counts.get("unknown_event_date", 0),
        "not_tracked_sources": status_counts.get("not_tracked", 0),
        "catalog_only_sources": status_counts.get("catalog_only", 0),
        "skipped_disabled_sources": status_counts.get("skipped_disabled", 0),
        "collection_error_count": len(errors_list),
        "alias_candidate_count": len(alias_candidates),
        "fresh_food_events": event_status_counts.get("fresh", 0),
        "stale_food_events": event_status_counts.get("stale", 0),
        "undated_food_events": event_status_counts.get("unknown_event_date", 0),
        "unique_food_event_key_count": len(food_event_keys),
        "recall_events_with_notice_date_count": sum(
            1
            for row in events
            if row.get("event_model") == "recall_status_change" and row.get("notice_date")
        ),
        "recall_events_with_status_count": sum(
            1
            for row in events
            if row.get("event_model") == "recall_status_change" and row.get("recall_status")
        ),
        "event_alias_trace_count": sum(1 for row in events if row.get("alias_traces")),
        "complaint_auxiliary_only_events": sum(
            1
            for row in events
            if row.get("event_model") == "complaint_signal"
            and row.get("verification_status") == "auxiliary_only"
        ),
        "official_evidence_events": sum(
            1
            for row in events
            if str(row.get("trust_tier") or "").startswith("T1_")
        ),
    }
    for event_model in TRACKED_EVENT_MODEL_ORDER:
        summary[f"{event_model}_events"] = event_counts.get(event_model, 0)

    return {
        "category": category.category_name,
        "generated_at": generated_at.isoformat(),
        "summary": summary,
        "sources": source_rows,
        "events": events,
        "alias_candidates": alias_candidates,
        "errors": errors_list,
    }


def write_quality_report(
    report: Mapping[str, object],
    *,
    output_dir: Path,
    category_name: str,
) -> dict[str, Path]:
    """Write stable and dated quality report JSON files."""
    output_dir.mkdir(parents=True, exist_ok=True)
    generated_at = _parse_iso_datetime(str(report.get("generated_at") or "")) or datetime.now(UTC)
    date_stamp = _as_utc(generated_at).strftime("%Y%m%d")
    latest_path = output_dir / f"{category_name}_quality.json"
    dated_path = output_dir / f"{category_name}_{date_stamp}_quality.json"
    encoded = json.dumps(report, ensure_ascii=False, indent=2, default=str)
    latest_path.write_text(encoded + "\n", encoding="utf-8")
    dated_path.write_text(encoded + "\n", encoding="utf-8")
    return {"latest": latest_path, "dated": dated_path}


def _build_source_row(
    *,
    source: Source,
    articles: list[Article],
    errors: list[str],
    freshness_sla: Mapping[str, object],
    tracked_event_models: set[str],
    generated_at: datetime,
) -> dict[str, Any]:
    source_articles = [article for article in articles if article.source == source.name]
    event_model = _source_event_model(source)
    tracked = _is_tracked_source(source, event_model, tracked_event_models)
    sla_days = _source_sla_days(source, event_model, freshness_sla)
    source_errors = _source_errors(source.name, errors)
    latest_article = _latest_article(source_articles, source)
    latest_event_at = _event_datetime(latest_article, source) if latest_article else None
    age_days = _age_days(generated_at, latest_event_at) if latest_event_at else None
    status = _source_status(
        source=source,
        tracked=tracked,
        article_count=len(source_articles),
        sla_days=sla_days,
        latest_event_at=latest_event_at,
        age_days=age_days,
    )
    latest_event_fields = (
        _food_event_fields(latest_article, source, event_model)
        if latest_article and tracked
        else {}
    )
    return {
        "source": source.name,
        "source_type": source.type,
        "enabled": source.enabled,
        "tracked": tracked,
        "event_model": event_model,
        "freshness_sla_days": sla_days,
        "status": status,
        "article_count": len(source_articles),
        "latest_event_at": latest_event_at.isoformat() if latest_event_at else None,
        "latest_collected_at": _article_collected_at(latest_article),
        "age_days": round(age_days, 2) if age_days is not None else None,
        "latest_title": latest_article.title if latest_article else "",
        "latest_url": latest_article.link if latest_article else "",
        "latest_food_event_key": _food_event_key(
            article=latest_article,
            source=source,
            event_model=event_model,
            fields=latest_event_fields,
        )
        if latest_article and tracked
        else "",
        "latest_notice_date": latest_event_fields.get("notice_date", ""),
        "latest_recall_status": latest_event_fields.get("recall_status", ""),
        "latest_release_date": latest_event_fields.get("release_date", ""),
        "latest_sanction_start_date": latest_event_fields.get("sanction_start_date", ""),
        "latest_sanction_end_date": latest_event_fields.get("sanction_end_date", ""),
        "latest_sanction_type": latest_event_fields.get("sanction_type", ""),
        "latest_alias_traces": latest_event_fields.get("alias_traces", []),
        "verification_role": str(source.config.get("verification_role") or "").strip(),
        "merge_policy": str(source.config.get("merge_policy") or "").strip(),
        "skip_reason": str(source.config.get("skip_reason") or "").strip(),
        "reenable_gate": str(source.config.get("reenable_gate") or "").strip(),
        "errors": source_errors,
    }


def _build_event_rows(
    *,
    sources: list[Source],
    articles: list[Article],
    tracked_event_models: set[str],
    freshness_sla: Mapping[str, object],
    generated_at: datetime,
) -> list[dict[str, Any]]:
    sources_by_name = {source.name: source for source in sources}
    rows: list[dict[str, Any]] = []
    for article in articles:
        source = sources_by_name.get(article.source)
        if source is None:
            continue
        event_model = _source_event_model(source)
        if not _is_tracked_source(source, event_model, tracked_event_models):
            continue

        event_at = _event_datetime(article, source)
        sla_days = _source_sla_days(source, event_model, freshness_sla)
        age_days = _age_days(generated_at, event_at) if event_at else None
        fields = _food_event_fields(article, source, event_model)
        rows.append(
            {
                "source": source.name,
                "source_type": source.type,
                "trust_tier": source.trust_tier,
                "event_model": event_model,
                "title": article.title,
                "url": article.link,
                "event_at": event_at.isoformat() if event_at else None,
                "event_age_days": round(age_days, 2) if age_days is not None else None,
                "event_freshness_sla_days": sla_days,
                "event_status": _event_status(
                    event_at=event_at,
                    age_days=age_days,
                    sla_days=sla_days,
                ),
                "food_event_key": _food_event_key(
                    article=article,
                    source=source,
                    event_model=event_model,
                    fields=fields,
                ),
                "evidence_url": article.link,
                "evidence_url_present": bool(article.link),
                **fields,
            }
        )
    return rows


def _food_event_fields(
    article: Article,
    source: Source,
    event_model: str,
) -> dict[str, Any]:
    brand_canonical = _list(article.matched_entities.get("BrandCanonical"))
    manufacturer_canonical = _list(article.matched_entities.get("ManufacturerCanonical"))
    brands = _list(article.matched_entities.get("Brand"))
    manufacturers = _list(article.matched_entities.get("Manufacturer"))
    food_types = _list(article.matched_entities.get("FoodType"))
    safety_issues = _list(article.matched_entities.get("SafetyIssue"))
    recall_reasons = _list(article.matched_entities.get("RecallReason"))
    alias_traces = _alias_traces(article)
    sanction_dates = _date_range_text(article)

    fields: dict[str, Any] = {
        "country": source.country,
        "brands": brands,
        "brand_canonical": brand_canonical,
        "manufacturers": manufacturers,
        "manufacturer_canonical": manufacturer_canonical,
        "food_types": food_types,
        "safety_issues": safety_issues,
        "recall_reasons": recall_reasons,
        "alias_traces": alias_traces,
        "verification_role": str(source.config.get("verification_role") or "").strip(),
        "merge_policy": str(source.config.get("merge_policy") or "").strip(),
        "verification_status": _verification_status(source, event_model),
    }
    if event_model == "recall_status_change":
        fields.update(
            {
                "notice_date": _notice_date(article, source),
                "recall_status": _recall_status(article),
                "release_date": _release_date(article),
            }
        )
    elif event_model == "enforcement_action":
        fields.update(
            {
                "sanction_start_date": _first(
                    article.matched_entities, "SanctionStartDate"
                )
                or sanction_dates[0],
                "sanction_end_date": _first(article.matched_entities, "SanctionEndDate")
                or sanction_dates[1],
                "sanction_type": _sanction_type(article),
            }
        )
    elif event_model == "complaint_signal":
        fields.update(
            {
                "complaint_channel": source.name,
                "observed_at": _notice_date(article, source),
                "official_merge_allowed": False,
            }
        )
    return fields


def _source_status(
    *,
    source: Source,
    tracked: bool,
    article_count: int,
    sla_days: int | None,
    latest_event_at: datetime | None,
    age_days: float | None,
) -> str:
    if not source.enabled:
        return "skipped_disabled"
    if source.type.lower() not in COLLECTED_SOURCE_TYPES:
        return "catalog_only"
    if not tracked:
        return "not_tracked"
    if article_count == 0:
        return "missing"
    if latest_event_at is None or age_days is None:
        return "unknown_event_date"
    if sla_days is not None and age_days > sla_days:
        return "stale"
    return "fresh"


def _latest_article(articles: list[Article], source: Source) -> Article | None:
    candidates = [(article, _event_datetime(article, source)) for article in articles]
    dated = [(article, event_at) for article, event_at in candidates if event_at is not None]
    if not dated:
        return articles[0] if articles else None
    return max(dated, key=lambda item: item[1])[0]


def _event_datetime(article: Article | None, source: Source) -> datetime | None:
    if article is None:
        return None
    field = str(
        source.config.get("event_date_field")
        or source.config.get("observed_date_field")
        or ""
    )
    if field == "collected_at":
        return _as_utc(article.collected_at) if article.collected_at else None
    if field == "published" and article.published:
        return _as_utc(article.published)

    for key in _event_field_entity_keys(field):
        parsed = _parse_text_datetime(_first(article.matched_entities, key))
        if parsed:
            return parsed
    if field == "published":
        observed_field = str(source.config.get("observed_date_field") or "")
        if observed_field == "collected_at" and article.collected_at:
            return _as_utc(article.collected_at)
    elif article.published:
        return _as_utc(article.published)

    if _is_date_only_text(article.summary):
        parsed = _extract_first_datetime(article.summary)
        if parsed:
            return parsed
    for text in (article.title, article.summary):
        parsed = _extract_first_datetime(text)
        if parsed:
            return parsed
    return _as_utc(article.collected_at) if article.collected_at else None


def _event_field_entity_keys(field: str) -> tuple[str, ...]:
    if field in {"notice_date", "published"}:
        return ("NoticeDate", "RecallNoticeDate", "ObservedAt", "SanctionStartDate")
    if field == "recall_notice_date":
        return ("RecallNoticeDate", "NoticeDate")
    if field == "observed_at":
        return ("ObservedAt",)
    if field == "sanction_start_date":
        return ("SanctionStartDate",)
    return ("NoticeDate", "RecallNoticeDate", "ObservedAt", "SanctionStartDate")


def _is_tracked_source(
    source: Source,
    event_model: str,
    tracked_event_models: set[str],
) -> bool:
    return (
        source.enabled
        and source.type.lower() in COLLECTED_SOURCE_TYPES
        and event_model in tracked_event_models
    )


def _article_collected_at(article: Article | None) -> str | None:
    if article is None or article.collected_at is None:
        return None
    return _as_utc(article.collected_at).isoformat()


def _age_days(generated_at: datetime, event_at: datetime) -> float:
    return max(0.0, (_as_utc(generated_at) - _as_utc(event_at)).total_seconds() / 86400)


def _source_event_model(source: Source) -> str:
    raw = source.config.get("event_model")
    return str(raw).strip() if raw is not None else ""


def _source_sla_days(
    source: Source,
    event_model: str,
    freshness_sla: Mapping[str, object],
) -> int | None:
    raw_source_sla = source.config.get("freshness_sla_days")
    if isinstance(raw_source_sla, bool):
        return None
    if isinstance(raw_source_sla, int | float):
        return int(raw_source_sla)
    if isinstance(raw_source_sla, str) and raw_source_sla.strip().isdigit():
        return int(raw_source_sla.strip())

    model_sla = freshness_sla.get(event_model)
    if isinstance(model_sla, Mapping):
        raw_model_sla = model_sla.get("max_age_days")
        if isinstance(raw_model_sla, bool):
            return None
        if isinstance(raw_model_sla, int | float):
            return int(raw_model_sla)
        if isinstance(raw_model_sla, str) and raw_model_sla.strip().isdigit():
            return int(raw_model_sla.strip())
    return None


def _tracked_event_models(quality: Mapping[str, object]) -> set[str]:
    outputs = _dict(quality, "quality_outputs")
    output_models = _string_set(outputs.get("tracked_event_models"))
    if output_models:
        return output_models & TRACKED_EVENT_MODELS or set(TRACKED_EVENT_MODELS)
    configured_models = _string_set(quality.get("event_models"))
    return configured_models & TRACKED_EVENT_MODELS or set(TRACKED_EVENT_MODELS)


def _event_status(
    *,
    event_at: datetime | None,
    age_days: float | None,
    sla_days: int | None,
) -> str:
    if event_at is None or age_days is None:
        return "unknown_event_date"
    if sla_days is not None and age_days > sla_days:
        return "stale"
    return "fresh"


def _food_event_key(
    *,
    article: Article | None,
    source: Source,
    event_model: str,
    fields: Mapping[str, Any],
) -> str:
    if article is None:
        return ""
    date_part = (
        str(fields.get("notice_date") or "")
        or str(fields.get("observed_at") or "")
        or str(fields.get("sanction_start_date") or "")
        or (article.published.date().isoformat() if article.published else "")
    )
    producer_part = ",".join(_list(fields.get("manufacturer_canonical"))) or ",".join(
        _list(fields.get("manufacturers"))
    )
    brand_part = ",".join(_list(fields.get("brand_canonical"))) or ",".join(
        _list(fields.get("brands"))
    )
    type_part = ",".join(_list(fields.get("food_types")))
    reason_part = ",".join(_list(fields.get("recall_reasons"))) or ",".join(
        _list(fields.get("safety_issues"))
    )
    key_parts = [
        event_model,
        source.country,
        source.name,
        date_part,
        producer_part,
        brand_part,
        type_part,
        reason_part,
        article.link or article.title,
    ]
    return ":".join(_normalize_key_text(part) for part in key_parts if str(part).strip())


def _notice_date(article: Article, source: Source) -> str:
    for key in ("NoticeDate", "RecallNoticeDate", "ObservedAt", "SanctionStartDate"):
        parsed = _parse_text_datetime(_first(article.matched_entities, key))
        if parsed:
            return parsed.date().isoformat()
    event_at = _event_datetime(article, source)
    return event_at.date().isoformat() if event_at else ""


def _release_date(article: Article) -> str:
    parsed = _parse_text_datetime(_first(article.matched_entities, "ReleaseDate"))
    return parsed.date().isoformat() if parsed else ""


def _recall_status(article: Article) -> str:
    explicit = _first(article.matched_entities, "RecallStatus")
    if explicit:
        return explicit
    text = f"{article.title}\n{article.summary}".casefold()
    if "release" in text or "resolved" in text or "해제" in text:
        return "released"
    if "sales stop" in text or "판매중지" in text:
        return "sales_stop"
    if "recall" in text or "회수" in text or "리콜" in text:
        return "recall"
    return "notice"


def _sanction_type(article: Article) -> str:
    explicit = _first(article.matched_entities, "SanctionType")
    if explicit:
        return explicit
    text = f"{article.title}\n{article.summary}".casefold()
    if "영업정지" in text or "business suspension" in text:
        return "business_suspension"
    if "과징금" in text or "penalty" in text:
        return "penalty"
    if "시정명령" in text or "corrective" in text:
        return "corrective_order"
    if "과태료" in text or "fine" in text:
        return "fine"
    return "enforcement_action"


def _verification_status(source: Source, event_model: str) -> str:
    if event_model == "complaint_signal":
        return "auxiliary_only"
    if str(source.trust_tier).startswith("T1_"):
        return "official_source"
    return "corroborating_source"


def _alias_traces(article: Article) -> list[str]:
    traces: list[str] = []
    for entity_name in ALIAS_ENTITY_TYPES:
        traces.extend(_list(article.matched_entities.get(f"{entity_name}AliasTrace")))
    return list(dict.fromkeys(traces))


def _date_range_text(article: Article) -> tuple[str, str]:
    dates = _extract_datetimes(article.title) or _extract_datetimes(article.summary)
    if not dates:
        return "", ""
    start = dates[0].date().isoformat()
    end = dates[1].date().isoformat() if len(dates) > 1 else ""
    return start, end


def _extract_first_datetime(text: str) -> datetime | None:
    dates = _extract_datetimes(text)
    return dates[0] if dates else None


def _extract_datetimes(text: str) -> list[datetime]:
    results: list[datetime] = []
    for match in COMPACT_DATE_PATTERN.finditer(text or ""):
        parsed = _date_from_parts(
            match.group(1),
            match.group(2),
            match.group(3),
        )
        if parsed:
            results.append(parsed)
    for match in DATE_PATTERN.finditer(text or ""):
        parsed = _date_from_parts(
            match.group("year"),
            match.group("month"),
            match.group("day"),
        )
        if parsed:
            results.append(parsed)
    return results


def _date_from_parts(year: str, month: str, day: str) -> datetime | None:
    try:
        return datetime(int(year), int(month), int(day), tzinfo=UTC)
    except ValueError:
        return None


def _is_date_only_text(value: str) -> bool:
    return bool(DATE_ONLY_TEXT_PATTERN.match(value or ""))


def _source_errors(source_name: str, errors: list[str]) -> list[str]:
    colon_prefix = f"{source_name}:"
    bracket_prefix = f"[{source_name}]"
    return [
        error
        for error in errors
        if error.startswith(colon_prefix) or error.startswith(bracket_prefix)
    ]


def _first(mapping: Mapping[str, list[str]], key: str) -> str:
    values = _list(mapping.get(key))
    return values[0] if values else ""


def _list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _string_set(value: object) -> set[str]:
    if isinstance(value, list):
        return {str(item).strip() for item in value if str(item).strip()}
    if isinstance(value, tuple | set):
        return {str(item).strip() for item in value if str(item).strip()}
    if isinstance(value, str) and value.strip():
        return {value.strip()}
    if isinstance(value, Mapping):
        return {str(item).strip() for item in value if str(item).strip()}
    return set()


def _normalize_key_text(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value)).strip().casefold()
    normalized = "".join(char if char.isalnum() else "-" for char in text)
    return "-".join(part for part in normalized.split("-") if part)


def _build_alias_candidates(articles: list[Article]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
    examples: dict[tuple[str, str], set[str]] = defaultdict(set)
    canonical_names: dict[tuple[str, str], str] = {}
    for article in articles:
        _collect_alias_traces(article, grouped, examples, canonical_names)
        for entity_name, alias_type in ALIAS_ENTITY_TYPES.items():
            values = article.matched_entities.get(entity_name, [])
            if not isinstance(values, list):
                continue
            for value in values:
                variant = str(value).strip()
                normalized = _normalize_alias(variant)
                if not normalized:
                    continue
                key = (alias_type, normalized)
                grouped[key][variant] += 1
                if len(examples[key]) < 3:
                    examples[key].add(article.link)

    candidates: list[dict[str, Any]] = []
    for (alias_type, normalized), variants in sorted(grouped.items()):
        variant_names = sorted(variants)
        if (
            (alias_type, normalized) not in canonical_names
            and len(variant_names) <= 1
            and sum(variants.values()) <= 1
        ):
            continue
        candidates.append(
            {
                "alias_type": alias_type,
                "canonical": canonical_names.get((alias_type, normalized), ""),
                "normalized": normalized,
                "variants": variant_names,
                "count": int(sum(variants.values())),
                "example_urls": sorted(examples[(alias_type, normalized)]),
            }
        )
    return candidates


def _collect_alias_traces(
    article: Article,
    grouped: dict[tuple[str, str], Counter[str]],
    examples: dict[tuple[str, str], set[str]],
    canonical_names: dict[tuple[str, str], str],
) -> None:
    for entity_name, alias_type in ALIAS_ENTITY_TYPES.items():
        trace_values = article.matched_entities.get(f"{entity_name}AliasTrace", [])
        if not isinstance(trace_values, list):
            continue

        for trace in trace_values:
            match = ALIAS_TRACE_PATTERN.match(str(trace).strip())
            if match is None:
                continue
            variant = match.group("variant").strip()
            canonical = match.group("canonical").strip()
            normalized = _normalize_alias(canonical)
            if not variant or not normalized:
                continue

            key = (alias_type, normalized)
            canonical_names[key] = canonical
            grouped[key][variant] += 1
            if len(examples[key]) < 3:
                examples[key].add(article.link)


def _normalize_alias(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    normalized = re.sub(r"\b(co|corp|corporation|inc|ltd|llc)\b\.?", "", normalized)
    normalized = re.sub(r"(주식회사|\(주\)|㈜|유한회사|농업회사법인|영농조합법인)", "", normalized)
    normalized = re.sub(r"[^0-9a-z가-힣]+", "", normalized)
    return normalized.strip()


def _dict(mapping: Mapping[str, object], key: str) -> Mapping[str, object]:
    value = mapping.get(key)
    return value if isinstance(value, Mapping) else {}


def _as_utc(dt: datetime | None) -> datetime:
    if dt is None:
        return datetime.now(UTC)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _parse_iso_datetime(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return _as_utc(datetime.fromisoformat(value.replace("Z", "+00:00")))
    except ValueError:
        return None


def _parse_text_datetime(value: str) -> datetime | None:
    parsed = _parse_iso_datetime(value)
    if parsed:
        return parsed
    return _extract_first_datetime(value)
