from __future__ import annotations

import calendar
import html
import os
import re
import threading
import time
from collections.abc import Mapping
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urlparse

import feedparser
import requests
from pybreaker import CircuitBreakerError
from radar_core import AdaptiveThrottler, CrawlHealthStore
from radar_core.url_extractor import extract_url_content_safe
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .exceptions import NetworkError, ParseError, SourceError
from .models import Article, Source
from .resilience import get_circuit_breaker_manager


_DEFAULT_HEADERS: dict[str, str] = {
    "User-Agent": "Mozilla/5.0 (compatible; RadarTemplateBot/1.0; +https://github.com/zzragida/ai-frendly-datahub)",
}
_DEFAULT_HEALTH_DB_PATH = "data/radar_data.duckdb"
_COLLECTION_CONTROL_LOCK = threading.Lock()
_ACTIVE_THROTTLER: AdaptiveThrottler | None = None
_ACTIVE_HEALTH_STORE: CrawlHealthStore | None = None
DATE_ONLY_SUMMARY_PATTERN = re.compile(
    r"^\s*(?:20\d{2}\d{2}\d{2}|20\d{2}[.\-/년\s]+\d{1,2}[.\-/월\s]+\d{1,2})\s*\.?\s*$"
)
COMPACT_DATE_PATTERN = re.compile(r"^\s*(20\d{2})(\d{2})(\d{2})\s*$")
SEPARATED_DATE_PATTERN = re.compile(
    r"^\s*(20\d{2})\s*(?:[.\-/]|년)\s*" r"(\d{1,2})\s*(?:[.\-/]|월)\s*" r"(\d{1,2})"
)


def _set_collection_controls(throttler: AdaptiveThrottler, health_store: CrawlHealthStore) -> None:
    global _ACTIVE_THROTTLER, _ACTIVE_HEALTH_STORE
    with _COLLECTION_CONTROL_LOCK:
        _ACTIVE_THROTTLER = throttler
        _ACTIVE_HEALTH_STORE = health_store


def _clear_collection_controls() -> None:
    global _ACTIVE_THROTTLER, _ACTIVE_HEALTH_STORE
    with _COLLECTION_CONTROL_LOCK:
        _ACTIVE_THROTTLER = None
        _ACTIVE_HEALTH_STORE = None


def _get_collection_controls() -> tuple[AdaptiveThrottler | None, CrawlHealthStore | None]:
    with _COLLECTION_CONTROL_LOCK:
        return _ACTIVE_THROTTLER, _ACTIVE_HEALTH_STORE


class RateLimiter:
    def __init__(self, min_interval: float = 0.5):
        self._min_interval: float = min_interval
        self._last_request: float = 0.0
        self._lock: threading.Lock = threading.Lock()

    def acquire(self) -> None:
        with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_request
            if elapsed < self._min_interval:
                time.sleep(self._min_interval - elapsed)
            self._last_request = time.monotonic()


def _resolve_max_workers(max_workers: int | None = None) -> int:
    if max_workers is None:
        raw_value = os.environ.get("RADAR_MAX_WORKERS", "5")
        try:
            parsed = int(raw_value)
        except ValueError:
            parsed = 5
    else:
        parsed = max_workers

    return max(1, min(parsed, 10))


def _source_bypasses_crawl_health(source: Source) -> bool:
    raw = source.config.get("bypass_crawl_health", False)
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        return raw.strip().lower() in {"1", "true", "yes", "y", "on"}
    return False


def _create_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(_DEFAULT_HEADERS)

    retry_strategy = Retry(
        total=3,
        backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=frozenset(["GET"]),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("http://", adapter)
    session.mount("https://", adapter)

    return session


def _fetch_url_with_retry(
    url: str,
    timeout: int,
    headers: dict[str, str] | None = None,
    session: requests.Session | None = None,
    source_name: str | None = None,
    throttler: AdaptiveThrottler | None = None,
    health_store: CrawlHealthStore | None = None,
    max_attempts: int = 3,
) -> requests.Response:
    """Fetch URL with retry logic on transient errors."""
    merged = {**_DEFAULT_HEADERS, **(headers or {})}
    if throttler is None or health_store is None:
        active_throttler, active_health_store = _get_collection_controls()
        throttler = throttler or active_throttler
        health_store = health_store or active_health_store

    retryable_errors = (
        requests.exceptions.Timeout,
        requests.exceptions.ConnectionError,
        requests.exceptions.HTTPError,
    )

    for attempt in range(max_attempts):
        if source_name is not None and throttler is not None:
            throttler.acquire(source_name)

        try:
            if session is not None:
                response = session.get(url, timeout=timeout, headers=merged)
            else:
                response = requests.get(url, timeout=timeout, headers=merged)
            response.raise_for_status()

            if source_name is not None and throttler is not None:
                throttler.record_success(source_name)
                if health_store is not None:
                    delay = throttler.get_current_delay(source_name)
                    health_store.record_success(source_name, delay)

            return response
        except retryable_errors as exc:
            if source_name is not None and throttler is not None:
                retry_after: int | str | None = None
                if isinstance(exc, requests.exceptions.HTTPError):
                    response = exc.response
                    if response is not None and response.status_code == 429:
                        retry_after = _parse_retry_after(response.headers.get("Retry-After"))

                throttler.record_failure(source_name, retry_after=retry_after)
                if health_store is not None:
                    delay = throttler.get_current_delay(source_name)
                    health_store.record_failure(source_name, str(exc), delay)

            if attempt == max_attempts - 1:
                raise

    raise RuntimeError("Retry loop exited unexpectedly")


def _parse_retry_after(value: str | None) -> int | str | None:
    if value is None:
        return None

    stripped = value.strip()
    if not stripped:
        return None

    if stripped.isdigit():
        return int(stripped)

    return stripped


def _detect_encoding(response: requests.Response) -> str:
    """Detect encoding for Korean .kr sites that may use EUC-KR."""
    content_type = response.headers.get("Content-Type", "")
    if "euc-kr" in content_type.lower() or "euc_kr" in content_type.lower():
        return "euc-kr"
    if "charset=" in content_type.lower():
        for part in content_type.split(";"):
            part = part.strip().lower()
            if part.startswith("charset="):
                return part.split("=", 1)[1].strip()
    return "utf-8"


def collect_sources(
    sources: list[Source],
    *,
    category: str,
    limit_per_source: int = 30,
    timeout: int = 15,
    min_interval_per_host: float = 0.5,
    max_workers: int | None = None,
    health_db_path: str | None = None,
) -> tuple[list[Article], list[str]]:
    """Fetch items from all configured sources, returning articles and errors."""
    articles: list[Article] = []
    errors: list[str] = []
    enabled_sources = [source for source in sources if source.enabled]
    rss_sources = [source for source in enabled_sources if source.type.lower() == "rss"]
    reddit_sources = [source for source in enabled_sources if source.type.lower() == "reddit"]
    unsupported_sources = [
        source for source in enabled_sources if source.type.lower() not in {"rss", "reddit"}
    ]
    manager = get_circuit_breaker_manager()
    workers = _resolve_max_workers(max_workers)
    source_hosts: dict[str, str] = {
        source.name: (urlparse(source.url).netloc.lower() or source.name) for source in rss_sources
    }
    rate_limiters: dict[str, RateLimiter] = {
        host: RateLimiter(min_interval=min_interval_per_host) for host in set(source_hosts.values())
    }
    host_locks: dict[str, threading.Lock] = {
        host: threading.Lock() for host in set(source_hosts.values())
    }
    throttler = AdaptiveThrottler(min_delay=max(0.001, min_interval_per_host))
    health_store = CrawlHealthStore(
        health_db_path or os.environ.get("RADAR_CRAWL_HEALTH_DB_PATH", _DEFAULT_HEALTH_DB_PATH)
    )
    _set_collection_controls(throttler, health_store)

    def _collect_for_source(source: Source) -> tuple[list[Article], list[str]]:
        if not _source_bypasses_crawl_health(source) and health_store.is_disabled(source.name):
            return [], [f"{source.name}: Source disabled (crawl health threshold reached)"]

        source_session = _create_session()
        try:
            host = source_hosts[source.name]
            with host_locks[host]:
                rate_limiters[host].acquire()
                breaker = manager.get_breaker(source.name)
                result = breaker.call(
                    _collect_single,
                    source,
                    category=category,
                    limit=limit_per_source,
                    timeout=timeout,
                    session=source_session,
                )
                _mark_crawl_health_recovered(health_store, source.name)
            return result, []
        except CircuitBreakerError:
            return [], [f"{source.name}: Circuit breaker open (source unavailable)"]
        except SourceError as exc:
            return [], [str(exc)]
        except (NetworkError, ParseError) as exc:
            return [], [f"{source.name}: {exc}"]
        except Exception as exc:
            return [], [f"{source.name}: Unexpected error - {type(exc).__name__}: {exc}"]
        finally:
            source_session.close()

    try:
        if workers == 1:
            for source in rss_sources:
                source_articles, source_errors = _collect_for_source(source)
                articles.extend(source_articles)
                errors.extend(source_errors)
        else:
            if rss_sources:
                with ThreadPoolExecutor(max_workers=workers) as executor:
                    future_map: list[Future[tuple[list[Article], list[str]]]] = [
                        executor.submit(_collect_for_source, source) for source in rss_sources
                    ]

                    for future in future_map:
                        source_articles, source_errors = future.result()
                        articles.extend(source_articles)
                        errors.extend(source_errors)

        if reddit_sources:
            try:
                from radar_core import collect_reddit_sources

                reddit_articles, reddit_errors = collect_reddit_sources(
                    reddit_sources,
                    category=category,
                    limit=limit_per_source,
                    timeout=timeout,
                    health_db_path=health_db_path
                    or os.environ.get("RADAR_CRAWL_HEALTH_DB_PATH", _DEFAULT_HEALTH_DB_PATH),
                )
                articles.extend(reddit_articles)
                errors.extend(reddit_errors)
            except ImportError:
                errors.append(
                    f"Reddit collection unavailable for {len(reddit_sources)} source(s). "
                    "Ensure radar-core reddit support is installed."
                )

        for source in unsupported_sources:
            errors.append(
                f"{source.name}: Source type '{source.type}' is cataloged but not collected by the standard pipeline"
            )
    finally:
        health_store.close()
        _clear_collection_controls()

    return articles, errors


def _collect_single(
    source: Source,
    *,
    category: str,
    limit: int,
    timeout: int,
    session: requests.Session | None = None,
) -> list[Article]:
    if source.type.lower() != "rss":
        raise SourceError(source.name, f"Unsupported source type '{source.type}'")

    try:
        request_timeout = _source_request_timeout(source, timeout)
        max_attempts = _source_max_attempts(source, 3)
        response = _fetch_url_with_retry(
            source.url,
            request_timeout,
            session=session,
            source_name=source.name,
            max_attempts=max_attempts,
        )
    except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as exc:
        raise NetworkError(f"Network error fetching {source.name}: {exc}") from exc
    except requests.exceptions.RequestException as exc:
        raise SourceError(source.name, f"Request failed: {exc}", exc) from exc

    try:
        # Handle EUC-KR encoding for Korean .kr sites
        encoding = _detect_encoding(response)
        if encoding.lower().replace("-", "") == "euckr":
            content = response.content.decode("euc-kr", errors="replace").encode("utf-8")
        else:
            content = response.content

        feed = feedparser.parse(content)
        items: list[Article] = []

        for entry in feed.entries[:limit]:
            published = _extract_datetime(entry)
            summary = _entry_text(entry, "summary") or _entry_text(entry, "description")
            if not summary:
                _content = entry.get("content", [])
                if isinstance(_content, list) and _content:
                    first_item = _content[0]
                    if isinstance(first_item, Mapping):
                        value = first_item.get("value")
                        if isinstance(value, str):
                            summary = value

            # Fallback: use title as summary if no description available
            if not summary or not summary.strip():
                raw_title = _entry_text(entry, "title").strip()
                summary = f"[식품안전] {raw_title}" if raw_title else ""

            # URL extraction fallback: fetch content from link if summary is too short.
            # Resolve first so a relative, list, or javascript: href is not fetched.
            link = _article_link(entry, source.url)
            if link and (
                not summary or (len(summary.strip()) < 50 and not _is_date_only_summary(summary))
            ):
                extracted = extract_url_content_safe(link)
                if extracted and extracted.content:
                    extracted_summary = extracted.content[:2000].strip()
                    if len(extracted_summary) > len(summary or ""):
                        summary = extracted_summary

            if published is None and _is_date_only_summary(summary):
                published = _extract_summary_date(summary)

            raw_title = html.unescape(_entry_text(entry, "title").strip()) or "(no title)"
            normalized_title, normalized_summary = _normalize_entry_text(
                source=source,
                title=raw_title,
                summary=html.unescape(summary.strip()),
            )

            items.append(
                Article(
                    title=normalized_title,
                    link=link,
                    summary=normalized_summary,
                    published=published,
                    source=source.name,
                    category=category,
                )
            )

        return items
    except Exception as exc:
        raise ParseError(f"Failed to parse feed from {source.name}: {exc}") from exc


def _source_request_timeout(source: Source, default: int) -> int:
    raw = source.config.get("request_timeout", source.config.get("timeout"))
    if isinstance(raw, bool):
        return default
    if isinstance(raw, int | float):
        return max(1, int(raw))
    if isinstance(raw, str):
        try:
            return max(1, int(float(raw.strip())))
        except ValueError:
            return default
    return default


def _source_max_attempts(source: Source, default: int) -> int:
    raw = source.config.get("max_attempts")
    if isinstance(raw, bool):
        return default
    if isinstance(raw, int | float):
        return max(1, int(raw))
    if isinstance(raw, str):
        try:
            return max(1, int(float(raw.strip())))
        except ValueError:
            return default
    return default


def _mark_crawl_health_recovered(health_store: CrawlHealthStore, source_name: str) -> None:
    """Clear stale disabled state after a bypassed source succeeds."""
    try:
        health_store.flush()
        write_lock = getattr(health_store, "_write_lock", None)
        conn = getattr(health_store, "conn", None)
        if write_lock is None or conn is None:
            return
        with write_lock:
            _ = conn.execute(
                """
                UPDATE crawl_health
                SET disabled = FALSE,
                    failure_count = 0,
                    last_error = NULL,
                    updated_at = NOW()
                WHERE source_name = ?
                """,
                [source_name],
            )
            _ = conn.commit()
    except Exception:
        return


def _is_date_only_summary(value: str) -> bool:
    return bool(DATE_ONLY_SUMMARY_PATTERN.match(value or ""))


def _extract_summary_date(value: str) -> datetime | None:
    text = value.strip()
    compact_match = COMPACT_DATE_PATTERN.match(text)
    if compact_match is not None:
        return _date_from_parts(
            compact_match.group(1),
            compact_match.group(2),
            compact_match.group(3),
        )

    separated_match = SEPARATED_DATE_PATTERN.match(text)
    if separated_match is not None:
        return _date_from_parts(
            separated_match.group(1),
            separated_match.group(2),
            separated_match.group(3),
        )
    return None


def _date_from_parts(year: str, month: str, day: str) -> datetime | None:
    try:
        return datetime(int(year), int(month), int(day), tzinfo=UTC)
    except ValueError:
        return None


def _normalize_entry_text(source: Source, title: str, summary: str) -> tuple[str, str]:
    if not _is_official_enforcement_source(source):
        return title, summary
    if not _is_date_only_summary(summary):
        return title, summary
    if _is_placeholder_title(title):
        notice_date = _format_summary_date(summary)
        if notice_date:
            return (
                f"{source.name} {notice_date}",
                f"{source.name} 공고일 {notice_date}",
            )
        return source.name, f"{source.name} 공고일 미확인"

    clean_title = _single_line(title)
    return _compact_title(clean_title), clean_title


def _format_summary_date(value: str) -> str | None:
    extracted = _extract_summary_date(value)
    if extracted is None:
        return None
    return extracted.date().isoformat()


def _is_official_enforcement_source(source: Source) -> bool:
    return str(source.config.get("event_model") or "").strip() == "enforcement_action"


def _is_placeholder_title(title: str) -> bool:
    return _single_line(title).casefold() in {"", "(no title)", "no title"}


def _single_line(value: str) -> str:
    return " ".join(html.unescape(value or "").split())


def _compact_title(value: str, max_chars: int = 160) -> str:
    text = _single_line(value)
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "…"


def _extract_datetime(entry: Mapping[str, Any]) -> datetime | None:
    """Parse a feed entry date into a timezone-aware datetime."""
    published_parsed = entry.get("published_parsed")
    if isinstance(published_parsed, time.struct_time):
        return datetime.fromtimestamp(calendar.timegm(published_parsed), tz=UTC)

    updated_parsed = entry.get("updated_parsed")
    if isinstance(updated_parsed, time.struct_time):
        return datetime.fromtimestamp(calendar.timegm(updated_parsed), tz=UTC)

    for key in ("published", "updated", "date"):
        raw = entry.get(key)
        if raw:
            try:
                dt = parsedate_to_datetime(str(raw))
                if dt and dt.tzinfo is None:
                    dt = dt.replace(tzinfo=UTC)
                return dt
            except Exception:
                continue
    return None


def _entry_text(entry: Mapping[str, Any], key: str) -> str:
    value = entry.get(key)
    return value if isinstance(value, str) else ""


_FOODSAFETYKOREA_ORIGIN = "https://www.foodsafetykorea.go.kr"
_CANONICAL_DETAIL_PATHS: dict[str, str] = {
    "administmeasuredetail": "/portal/administMeasureDetail.do",
    "suspensiondetail": "/portal/suspensionDetail.do",
    "boarddetail": "/portal/board/boardDetail.do",
}
_DETAIL_ID_PARAMS: dict[str, str] = {
    "administmeasuredetail": "seq",
    "suspensiondetail": "search_keyword",
    "boarddetail": "ntctxt_no",
}
_LIST_PAGE_SUFFIXES: tuple[str, ...] = (
    "/portal/fooddanger/administMeasureList.do",
    "/portal/fooddanger/suspension.do",
    "/portal/board/board.do",
    "/rss/rss.do",
)
_DETAIL_KIND_PATTERN = re.compile(
    r"(?P<kind>administMeasureDetail|suspensionDetail|boardDetail)\.do$",
    re.IGNORECASE,
)
_JS_MOVE_DETAIL2_PATTERN = re.compile(
    r"fn_moveDetail2\s*\(\s*['\"]?(?P<seq>\d+)",
    re.IGNORECASE,
)
_JS_MOVE_DETAIL_PATTERN = re.compile(
    r"fn_moveDetail\s*\(\s*['\"]?(?P<keyword>\d+)",
    re.IGNORECASE,
)
_JS_VIEW_DETAIL_PATTERN = re.compile(
    r"viewDetail\s*\(\s*['\"](?P<ntc>\d+)['\"]",
    re.IGNORECASE,
)
_DIGITS_PATTERN = re.compile(r"[0-9]+$")
_BBS_NO_PATTERN = re.compile(r"[A-Za-z0-9_]+$")


def resolve_article_link(raw_link: str, base_url: str) -> str:
    """Return a stable absolute article URL for one feed item.

    식품안전나라 list pages navigate with ``javascript:`` or a ``/layer``/``/popup``
    URL that adds a cache-busting ``t`` parameter. Those are rewritten to the
    public ``/portal`` detail URL using the notice id (``seq``, ``search_keyword``,
    or ``ntctxt_no``). Relative detail paths from the RSS feed are made absolute
    against the source host. Other publishers' absolute links are left unchanged.
    """
    link = html.unescape(raw_link or "").strip()
    base = html.unescape(base_url or "").strip()
    if link.startswith("//"):
        scheme = urlparse(base).scheme
        if scheme not in {"http", "https"}:
            scheme = "https"
        link = f"{scheme}:{link}"

    if _should_resolve_foodsafetykorea_link(link, base):
        detail = _foodsafetykorea_detail_url(link, base)
        if detail:
            return detail
        if _foodsafetykorea_link_is_unusable(link):
            return ""
    return _absolutize_http_link(link, base)


def _article_link(entry: Mapping[str, Any], base_url: str) -> str:
    raw_link = _raw_entry_link(entry)
    resolved = resolve_article_link(raw_link, base_url)
    if resolved:
        return resolved
    for key in ("id", "guid"):
        candidate = _entry_text(entry, key).strip()
        if not candidate or candidate == raw_link:
            continue
        resolved = resolve_article_link(candidate, base_url)
        if resolved:
            return resolved
    return ""


def _raw_entry_link(entry: Mapping[str, Any]) -> str:
    link = _entry_text(entry, "link").strip()
    if link:
        return link
    links = entry.get("links")
    if not isinstance(links, list):
        return ""
    for item in links:
        if not isinstance(item, Mapping):
            continue
        href = item.get("href")
        if not isinstance(href, str) or not href.strip():
            continue
        rel = item.get("rel")
        if rel in (None, "", "alternate"):
            return href.strip()
    return ""


def _should_resolve_foodsafetykorea_link(link: str, base_url: str) -> bool:
    link_host = _http_hostname(link)
    if link_host:
        return _is_foodsafetykorea_host(link_host)
    if _http_hostname(base_url) and not _is_foodsafetykorea_host(_http_hostname(base_url)):
        return False
    if not link or link.lower().startswith("javascript:"):
        return _is_foodsafetykorea_host(_http_hostname(base_url))
    parsed = urlparse(link)
    if parsed.scheme and parsed.scheme not in {"http", "https"}:
        return False
    return _is_foodsafetykorea_host(_http_hostname(base_url))


def _foodsafetykorea_detail_url(link: str, base_url: str) -> str | None:
    origin = _foodsafetykorea_origin(link, base_url)
    path, query = _split_path_query(link)
    kind = _detail_kind(path)
    if kind:
        cleaned = _strip_query_param(query, "t")
        item_id = _query_param(cleaned, _DETAIL_ID_PARAMS[kind])
        if item_id and _DIGITS_PATTERN.fullmatch(item_id):
            return f"{origin}{_CANONICAL_DETAIL_PATHS[kind]}?{cleaned}"

    identifiers = _parse_foodsafetykorea_js(link)
    if identifiers is None:
        return None
    return _detail_from_identifiers(origin, base_url, identifiers)


def _detail_from_identifiers(
    origin: str, base_url: str, identifiers: Mapping[str, str]
) -> str | None:
    seq = identifiers.get("seq", "")
    if seq and _DIGITS_PATTERN.fullmatch(seq):
        return f"{origin}/portal/administMeasureDetail.do?seq={seq}"

    keyword = identifiers.get("keyword", "")
    if keyword and _DIGITS_PATTERN.fullmatch(keyword):
        return f"{origin}/portal/suspensionDetail.do?search_keyword={keyword}"

    ntc = identifiers.get("ntc", "")
    if not ntc or not _DIGITS_PATTERN.fullmatch(ntc):
        return None
    bbs_no = _query_param(urlparse(base_url).query, "bbs_no")
    menu_no = _query_param(urlparse(base_url).query, "menu_no")
    if not _BBS_NO_PATTERN.fullmatch(bbs_no) or not _DIGITS_PATTERN.fullmatch(menu_no):
        return None
    return (
        f"{origin}/portal/board/boardDetail.do"
        f"?bbs_no={bbs_no}&ntctxt_no={ntc}&menu_no={menu_no}"
    )


def _parse_foodsafetykorea_js(link: str) -> dict[str, str] | None:
    if "javascript:" not in link.lower():
        return None
    move2 = _JS_MOVE_DETAIL2_PATTERN.search(link)
    if move2 is not None:
        return {"seq": move2.group("seq")}
    move = _JS_MOVE_DETAIL_PATTERN.search(link)
    if move is not None:
        return {"keyword": move.group("keyword")}
    view = _JS_VIEW_DETAIL_PATTERN.search(link)
    if view is not None:
        return {"ntc": view.group("ntc")}
    return None


def _foodsafetykorea_link_is_unusable(link: str) -> bool:
    if not link or link.lower().startswith("javascript:"):
        return True
    path, _query = _split_path_query(link)
    normalized = (path.rstrip("/") or "/").lower()
    if normalized in {"", "/"}:
        return True
    return any(normalized.endswith(suffix.lower()) for suffix in _LIST_PAGE_SUFFIXES)


def _foodsafetykorea_origin(link: str, base_url: str) -> str:
    for candidate in (base_url, link):
        if _is_foodsafetykorea_host(_http_hostname(candidate)):
            origin = _origin_from_http_url(candidate)
            if origin:
                return origin
    return _FOODSAFETYKOREA_ORIGIN


def _absolutize_http_link(link: str, base_url: str) -> str:
    if not link:
        return ""
    if link.lower().startswith("javascript:"):
        return link
    parsed = urlparse(link)
    if parsed.scheme in {"http", "https"} and parsed.netloc:
        return link
    if parsed.scheme and parsed.scheme not in {"http", "https"}:
        return link
    origin = _origin_from_http_url(base_url)
    if not origin:
        return link
    if link.startswith("/"):
        return origin + link
    return origin + "/" + link.lstrip("./")


def _detail_kind(path: str) -> str:
    match = _DETAIL_KIND_PATTERN.search(path.rstrip("/"))
    if match is None:
        return ""
    return match.group("kind").lower()


def _split_path_query(link: str) -> tuple[str, str]:
    if not link or link.lower().startswith("javascript:"):
        return "", ""
    if "://" in link or link.startswith("//"):
        parsed = urlparse(link if "://" in link else f"https:{link}")
        return parsed.path or "", parsed.query or ""
    path, _, query = link.partition("?")
    path = path.split("#", 1)[0]
    query = query.split("#", 1)[0]
    if path and not path.startswith("/"):
        path = "/" + path
    return path, query


def _strip_query_param(query: str, name: str) -> str:
    kept: list[str] = []
    for part in query.split("&"):
        if not part:
            continue
        key = part.split("=", 1)[0]
        if key == name:
            continue
        kept.append(part)
    return "&".join(kept)


def _query_param(query: str, name: str) -> str:
    for part in query.split("&"):
        if not part or "=" not in part:
            continue
        key, value = part.split("=", 1)
        if key == name and value:
            return value
    return ""


def _http_hostname(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return ""
    return (parsed.hostname or "").lower()


def _origin_from_http_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return ""
    return f"{parsed.scheme}://{parsed.hostname}"


def _is_foodsafetykorea_host(hostname: str) -> bool:
    host = hostname.lower().rstrip(".")
    return host == "foodsafetykorea.go.kr" or host.endswith(".foodsafetykorea.go.kr")
