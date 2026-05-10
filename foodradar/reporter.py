from __future__ import annotations

from collections.abc import Iterable
from html import escape
from pathlib import Path
from typing import Any, Mapping

from radar_core.ontology import build_summary_ontology_metadata
from radar_core.report_utils import (
    generate_index_html as _core_generate_index_html,
)
from radar_core.report_utils import (
    generate_report as _core_generate_report,
)

from .models import Article, CategoryConfig


def generate_report(
    *,
    category: CategoryConfig,
    articles: Iterable[Article],
    output_path: Path,
    stats: dict[str, int],
    errors: list[str] | None = None,
    store=None,
    quality_report: Mapping[str, Any] | None = None,
) -> Path:
    """Generate HTML report (delegates to radar-core)."""
    articles_list = list(articles)
    plugin_charts = []

    # --- Universal plugins (entity heatmap + source reliability) ---
    try:
        from radar_core.plugins.entity_heatmap import get_chart_config as _heatmap_config

        _heatmap = _heatmap_config(articles=articles_list)
        if _heatmap is not None:
            plugin_charts.append(_heatmap)
    except Exception:
        pass
    try:
        from radar_core.plugins.source_reliability import get_chart_config as _reliability_config

        _reliability = _reliability_config(store=store)
        if _reliability is not None:
            plugin_charts.append(_reliability)
    except Exception:
        pass

    result = _core_generate_report(
        category=category,
        articles=articles_list,
        output_path=output_path,
        stats=stats,
        errors=errors,
        plugin_charts=plugin_charts if plugin_charts else None,
        ontology_metadata=build_summary_ontology_metadata(
            "FoodRadar",
            category_name=category.category_name,
            search_from=Path(__file__).resolve(),
        ),
    )
    if quality_report:
        _inject_quality_traceability_panel(result, quality_report)
        _inject_latest_dated_report_panel(result, category.category_name, quality_report)
    return result


def generate_index_html(
    report_dir: Path,
    summaries_dir: Path | None = None,
) -> Path:
    """Generate index.html (delegates to radar-core)."""
    radar_name = "Food Radar"
    return _core_generate_index_html(report_dir, radar_name)


def _inject_latest_dated_report_panel(
    output_path: Path,
    category_name: str,
    quality_report: Mapping[str, Any],
) -> None:
    dated_reports = sorted(
        output_path.parent.glob(
            f"{category_name}_[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9].html"
        ),
        key=lambda path: path.stat().st_mtime,
    )
    if dated_reports:
        _inject_quality_traceability_panel(dated_reports[-1], quality_report)


def _inject_quality_traceability_panel(
    output_path: Path,
    quality_report: Mapping[str, Any],
) -> None:
    if not output_path.exists():
        return
    html = output_path.read_text(encoding="utf-8")
    if 'id="quality-traceability"' in html:
        return

    marker = '<section id="entities"'
    if marker not in html:
        return

    panel = _render_quality_traceability_panel(quality_report).rstrip()
    rendered = html.replace(marker, panel + "\n      " + marker, 1)
    rendered = "\n".join(line.rstrip() for line in rendered.splitlines()) + "\n"
    output_path.write_text(rendered, encoding="utf-8")


def _render_quality_traceability_panel(quality_report: Mapping[str, Any]) -> str:
    summary = quality_report.get("summary")
    summary_map = summary if isinstance(summary, Mapping) else {}
    sources = [row for row in _list(quality_report.get("sources")) if isinstance(row, Mapping)]
    events = [row for row in _list(quality_report.get("events")) if isinstance(row, Mapping)]
    alias_candidates = [
        row for row in _list(quality_report.get("alias_candidates")) if isinstance(row, Mapping)
    ]
    match_review_items = [
        row
        for row in _list(quality_report.get("match_coverage_review_items"))
        if isinstance(row, Mapping)
    ]
    flagged_sources = [
        row
        for row in sources
        if str(row.get("status")) in {"stale", "missing", "unknown_event_date"}
    ][:6]
    event_rows = [
        row
        for row in events
        if str(row.get("event_model"))
        in {"recall_status_change", "enforcement_action", "complaint_signal"}
    ][:6]

    chips = [
        ("fresh", summary_map.get("fresh_sources", 0)),
        ("stale", summary_map.get("stale_sources", 0)),
        ("missing", summary_map.get("missing_sources", 0)),
        ("recall events", summary_map.get("recall_status_change_events", 0)),
        ("enforcement", summary_map.get("enforcement_action_events", 0)),
        ("complaints", summary_map.get("complaint_signal_events", 0)),
        ("alias candidates", summary_map.get("alias_candidate_count", 0)),
        ("product aliases", summary_map.get("product_alias_candidate_count", 0)),
        ("manufacturer aliases", summary_map.get("manufacturer_alias_candidate_count", 0)),
        ("alias traced", summary_map.get("event_alias_trace_count", 0)),
        (
            "coverage review",
            summary_map.get("match_coverage_review_item_count", len(match_review_items)),
        ),
    ]
    chip_html = "\n".join(
        f'<span class="chip"><strong>{escape(label)}</strong> {escape(str(value))}</span>'
        for label, value in chips
    )
    event_html = _render_food_events(event_rows)
    source_html = _render_quality_sources(flagged_sources)
    match_review_html = _render_match_coverage_review_items(match_review_items[:8])
    alias_html = _render_alias_candidates(alias_candidates[:6])
    return f"""
      <section id="quality-traceability" class="section" aria-label="Quality traceability">
        <div class="section-hd">
          <h2>Quality Traceability</h2>
          <div class="right">
            <span class="kbd">food_quality.json</span>
            <span class="kbd">alias map</span>
          </div>
        </div>
        <article class="panel">
          <header class="panel-hd">
            <div>
              <p class="panel-title">Freshness and Alias Checks</p>
              <p class="panel-sub">source freshness, missing source count, and product/manufacturer alias trace</p>
            </div>
          </header>
          <div class="panel-bd">
            <div class="row" aria-label="Quality summary">
              {chip_html}
            </div>
            {event_html}
            {source_html}
            {match_review_html}
            {alias_html}
          </div>
        </article>
      </section>
"""


def _render_quality_sources(flagged_sources: list[Mapping[str, Any]]) -> str:
    if not flagged_sources:
        return '<p class="muted small">No stale or missing tracked sources in this run.</p>'
    items = []
    for row in flagged_sources:
        source = escape(str(row.get("source", "")))
        status = escape(str(row.get("status", "")))
        model = escape(str(row.get("event_model", "")))
        age = row.get("age_days")
        age_text = "" if age is None else f", age {escape(str(age))}d"
        items.append(f"<li><strong>{source}</strong>: {status} ({model}{age_text})</li>")
    return "<ul>" + "\n".join(items) + "</ul>"


def _render_food_events(events: list[Mapping[str, Any]]) -> str:
    if not events:
        return '<p class="muted small">No tracked food safety events in this run.</p>'
    items = []
    for row in events:
        model = escape(str(row.get("event_model", "")))
        title = escape(str(row.get("title", ""))[:120])
        status = escape(str(row.get("event_status", "")))
        notice_date = escape(
            str(
                row.get("notice_date")
                or row.get("observed_at")
                or row.get("sanction_start_date")
                or row.get("event_at")
                or ""
            )
        )
        details = []
        for key, label in (
            ("product_canonical", "product"),
            ("manufacturer_canonical", "manufacturer"),
            ("brand_canonical", "brand"),
        ):
            values = _list(row.get(key))
            if values:
                details.append(
                    f"{escape(label)}={escape(', '.join(str(value) for value in values[:2]))}"
                )
        for key in ("recall_status", "sanction_type", "verification_status"):
            value = row.get(key)
            if value:
                details.append(f"{escape(key)}={escape(str(value))}")
        alias_traces = _list(row.get("alias_traces"))
        if alias_traces:
            details.append("alias=" + escape(", ".join(str(value) for value in alias_traces[:2])))
        detail_text = "; ".join(details)
        suffix = f" - {detail_text}" if detail_text else ""
        items.append(
            f"<li><strong>{model}</strong> {status} {notice_date}: {title}{suffix}</li>"
        )
    return "<ul>" + "\n".join(items) + "</ul>"


def _render_alias_candidates(alias_candidates: list[Mapping[str, Any]]) -> str:
    if not alias_candidates:
        return '<p class="muted small">No product or manufacturer alias candidates in this run.</p>'
    items = []
    for candidate in alias_candidates:
        alias_type = escape(str(candidate.get("alias_type", "")))
        canonical = str(candidate.get("canonical") or candidate.get("normalized") or "")
        variants = ", ".join(escape(str(value)) for value in _list(candidate.get("variants"))[:5])
        items.append(
            f"<li><strong>{alias_type}</strong> {escape(canonical)}: {variants}</li>"
        )
    return "<ul>" + "\n".join(items) + "</ul>"


def _render_match_coverage_review_items(items: list[Mapping[str, Any]]) -> str:
    if not items:
        return '<p class="muted small">No match coverage review items in this run.</p>'
    rendered = []
    for item in items:
        reason = escape(str(item.get("reason", "")))
        priority = escape(str(item.get("priority", "")))
        source = escape(str(item.get("source", "")))
        title = escape(str(item.get("title", ""))[:120])
        action = escape(str(item.get("recommended_action", "")))
        rendered.append(
            "<li>"
            f"<strong>{priority}</strong> {reason}: {source} - {title}"
            f"<br><span class=\"muted small\">{action}</span>"
            "</li>"
        )
    return "<ul>" + "\n".join(rendered) + "</ul>"


def _list(value: object) -> list[Any]:
    return value if isinstance(value, list) else []
