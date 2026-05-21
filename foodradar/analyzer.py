from __future__ import annotations

import html
import re
import unicodedata
from collections.abc import Iterable, Mapping

from radar_core.analyzer import apply_entity_rules as _core_apply_entity_rules

from .models import Article, EntityDefinition, Source


AliasMap = Mapping[str, Mapping[str, Iterable[str]]]


def apply_entity_rules(
    articles: Iterable[Article],
    entities: list[EntityDefinition],
    *,
    alias_map: AliasMap | None = None,
    sources: Iterable[Source] | None = None,
) -> list[Article]:
    resolved_alias_map = _normalize_alias_map(alias_map or {})
    expanded_entities = _expand_entity_keywords(entities, resolved_alias_map)
    analyzed = _core_apply_entity_rules(articles, expanded_entities)
    if sources:
        _attach_official_recall_entities(analyzed, sources)
        _attach_official_enforcement_entities(analyzed, sources)
    if resolved_alias_map:
        _attach_alias_traces(analyzed, resolved_alias_map)
    return analyzed


def _expand_entity_keywords(
    entities: list[EntityDefinition],
    alias_map: dict[str, dict[str, set[str]]],
) -> list[EntityDefinition]:
    expanded: list[EntityDefinition] = []
    for entity in entities:
        keywords = list(entity.keywords)
        for variants in alias_map.get(entity.name, {}).values():
            keywords.extend(variants)
        deduped = list(dict.fromkeys(keyword for keyword in keywords if keyword))
        expanded.append(
            EntityDefinition(
                name=entity.name,
                display_name=entity.display_name,
                keywords=deduped,
            )
        )
    return expanded


def _attach_alias_traces(
    articles: list[Article],
    alias_map: dict[str, dict[str, set[str]]],
) -> None:
    for article in articles:
        for entity_name, canonical_map in alias_map.items():
            matched_values = article.matched_entities.get(entity_name)
            if not isinstance(matched_values, list):
                continue

            canonical_values: list[str] = []
            trace_values: list[str] = []
            for value in matched_values:
                variant = str(value)
                canonical = _canonical_for_variant(variant, canonical_map)
                if canonical is None:
                    continue
                canonical_values.append(canonical)
                if _alias_key(variant) != _alias_key(canonical):
                    trace_values.append(f"{variant} -> {canonical}")

            if canonical_values:
                article.matched_entities[f"{entity_name}Canonical"] = list(
                    dict.fromkeys(canonical_values)
                )
            if trace_values:
                article.matched_entities[f"{entity_name}AliasTrace"] = list(
                    dict.fromkeys(trace_values)
                )


def _canonical_for_variant(
    value: str,
    canonical_map: dict[str, set[str]],
) -> str | None:
    value_key = _alias_key(value)
    for canonical, variants in canonical_map.items():
        if value_key in {_alias_key(variant) for variant in variants}:
            return canonical
    return None


def _normalize_alias_map(alias_map: AliasMap) -> dict[str, dict[str, set[str]]]:
    normalized: dict[str, dict[str, set[str]]] = {}
    for raw_entity_name, raw_canonical_map in alias_map.items():
        entity_name = str(raw_entity_name).strip()
        if not entity_name:
            continue
        entity_aliases: dict[str, set[str]] = {}
        for raw_canonical, raw_variants in raw_canonical_map.items():
            canonical = str(raw_canonical).strip()
            if not canonical:
                continue
            variants = {canonical}
            if isinstance(raw_variants, str):
                variants.add(raw_variants)
            else:
                variants.update(str(item).strip() for item in raw_variants if str(item).strip())
            entity_aliases[canonical] = variants
        if entity_aliases:
            normalized[entity_name] = entity_aliases
    return normalized


def _attach_official_recall_entities(
    articles: list[Article],
    sources: Iterable[Source],
) -> None:
    sources_by_name = {source.name: source for source in sources}
    for article in articles:
        source = sources_by_name.get(article.source)
        if source is None or not _is_official_recall_product_source(source):
            continue

        product_raw = _clean_text(article.summary)
        manufacturer_raw = _clean_text(article.title)
        if product_raw and not _is_date_only_text(product_raw):
            _append_entity_value(article, "Product", product_raw)
            _append_entity_value(article, "ProductCanonical", product_raw)
        if manufacturer_raw and not _is_date_only_text(manufacturer_raw):
            _append_entity_value(article, "Manufacturer", manufacturer_raw)
            _append_entity_value(article, "ManufacturerCanonical", manufacturer_raw)


def _attach_official_enforcement_entities(
    articles: list[Article],
    sources: Iterable[Source],
) -> None:
    sources_by_name = {source.name: source for source in sources}
    for article in articles:
        source = sources_by_name.get(article.source)
        if source is None or not _is_official_enforcement_source(source):
            continue
        _append_entity_value(article, "Regulation", "행정처분")


def _append_entity_value(article: Article, entity_name: str, value: str) -> None:
    existing = article.matched_entities.get(entity_name)
    if isinstance(existing, list):
        values = existing
    else:
        values = []
    if value not in values:
        values.append(value)
    article.matched_entities[entity_name] = values


def _is_official_recall_product_source(source: Source) -> bool:
    if str(source.config.get("event_model") or "").strip() != "recall_status_change":
        return False
    if not str(source.trust_tier).startswith("T1_"):
        return False
    canonical_fields = _string_set(source.config.get("canonical_key_fields"))
    return {"product_name", "manufacturer_name"}.issubset(canonical_fields)


def _is_official_enforcement_source(source: Source) -> bool:
    if str(source.config.get("event_model") or "").strip() != "enforcement_action":
        return False
    return str(source.trust_tier).startswith("T1_")


def _string_set(value: object) -> set[str]:
    if isinstance(value, list):
        return {str(item).strip() for item in value if str(item).strip()}
    if isinstance(value, tuple | set):
        return {str(item).strip() for item in value if str(item).strip()}
    if isinstance(value, str) and value.strip():
        return {value.strip()}
    return set()


def _clean_text(value: str) -> str:
    text = html.unescape(value or "")
    text = re.sub(r"<[^>]+>", " ", text)
    return " ".join(text.split())


def _is_date_only_text(value: str) -> bool:
    return bool(
        re.match(
            r"^\s*(?:20\d{2}\d{2}\d{2}|20\d{2}[.\-/년\s]+\d{1,2}[.\-/월\s]+\d{1,2})\s*\.?\s*$",
            value or "",
        )
    )


def _alias_key(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    normalized = re.sub(r"\b(co|corp|corporation|inc|ltd|llc)\b\.?", "", normalized)
    normalized = re.sub(r"(주식회사|\(주\)|㈜|유한회사|농업회사법인|영농조합법인)", "", normalized)
    normalized = re.sub(r"[^0-9a-z가-힣]+", "", normalized)
    return normalized.strip()


__all__ = ["apply_entity_rules"]
