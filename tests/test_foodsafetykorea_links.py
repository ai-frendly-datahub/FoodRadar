from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock, patch
from urllib.parse import urlparse

from foodradar.collector import _collect_single, resolve_article_link
from foodradar.config_loader import load_category_config
from foodradar.models import Source


_FIXTURES = Path(__file__).parent / "fixtures"
_PUBLIC_RSS = _FIXTURES / "foodsafetykorea_public_rss.xml"
_PUBLIC_PAGE = _FIXTURES / "foodsafetykorea_public_page.html"
_ORIGIN = "https://www.foodsafetykorea.go.kr"
_ENFORCEMENT_SOURCE = (
    "https://www.foodsafetykorea.go.kr/rss/rss.do?menu_no=2714&menu_grp=MENU_NEW02"
)
_RECALL_SOURCE = "https://www.foodsafetykorea.go.kr/rss/rss.do?menu_no=2713&menu_grp=MENU_NEW02"
_PRESS_SOURCE = (
    "https://www.foodsafetykorea.go.kr/rss/rss.do?bbs_no=bbs080&menu_no=2857&menu_grp=MENU_NEW05"
)


def test_public_rss_fixture_stores_absolute_detail_urls() -> None:
    source = Source(
        name="식품안전나라 행정처분",
        type="rss",
        url=_ENFORCEMENT_SOURCE,
    )
    response = Mock()
    response.content = _PUBLIC_RSS.read_bytes()
    response.headers = {"Content-Type": "application/rss+xml; charset=utf-8"}

    with (
        patch("foodradar.collector._fetch_url_with_retry", return_value=response),
        patch("foodradar.collector.extract_url_content_safe", return_value=None) as extract,
    ):
        articles = _collect_single(source, category="food", limit=20, timeout=5)

    links = [article.link for article in articles]
    recall = f"{_ORIGIN}/portal/suspensionDetail.do?search_keyword=3000236534"
    enforcement = f"{_ORIGIN}/portal/administMeasureDetail.do?seq=4749051"
    press = f"{_ORIGIN}/portal/board/boardDetail.do?bbs_no=bbs080&ntctxt_no=50448&menu_no=2857"
    overseas = f"{_ORIGIN}/portal/board/boardDetail.do?bbs_no=bbs180&ntctxt_no=1110421&menu_no=4122"

    assert links.count(recall) == 2
    assert links.count(enforcement) == 3
    assert press in links
    assert overseas in links
    assert links.count("") == 1
    assert all(link.startswith("https://") or link == "" for link in links)
    assert all("javascript:" not in link.lower() for link in links)
    assert all("administMeasureList.do" not in link for link in links)
    assert all(not link.endswith("/suspension.do") for link in links)
    assert all("&t=" not in link and "?t=" not in link for link in links)

    fetched = [call.args[0] for call in extract.call_args_list]
    assert fetched
    assert all(url.startswith(f"{_ORIGIN}/portal/") and "Detail.do" in url for url in fetched)


def test_public_page_javascript_navigation_builds_stable_detail_urls() -> None:
    page = _PUBLIC_PAGE.read_text(encoding="utf-8")
    assert "fn_moveDetail2" in page
    assert "/popup/administMeasureDetail.do?seq=" in page
    assert "/layer/administMeasureDetail.do?seq=" in page
    assert "fn_moveDetail" in page
    assert "/popup/suspensionDetail.do?search_keyword=" in page
    assert "/layer/suspensionDetail.do?search_keyword=" in page
    assert "javascript:viewDetail" in page
    assert 'document.baseForm.action="/portal/board/boardDetail.do"' in page

    assert (
        resolve_article_link("javascript:fn_moveDetail2('4749051')", _ENFORCEMENT_SOURCE)
        == f"{_ORIGIN}/portal/administMeasureDetail.do?seq=4749051"
    )
    assert (
        resolve_article_link(
            "/layer/administMeasureDetail.do?seq=4749051&t=1710000000000",
            _ENFORCEMENT_SOURCE,
        )
        == f"{_ORIGIN}/portal/administMeasureDetail.do?seq=4749051"
    )
    assert (
        resolve_article_link(
            "https://www.foodsafetykorea.go.kr:443/popup/administMeasureDetail.do?seq=4749051&t=9",
            _ENFORCEMENT_SOURCE,
        )
        == f"{_ORIGIN}/portal/administMeasureDetail.do?seq=4749051"
    )
    assert (
        resolve_article_link("javascript:fn_moveDetail('3000236534')", _RECALL_SOURCE)
        == f"{_ORIGIN}/portal/suspensionDetail.do?search_keyword=3000236534"
    )
    assert (
        resolve_article_link(
            "/popup/suspensionDetail.do?search_keyword=3000236534",
            _RECALL_SOURCE,
        )
        == f"{_ORIGIN}/portal/suspensionDetail.do?search_keyword=3000236534"
    )
    assert (
        resolve_article_link(
            "javascript:viewDetail('50448','user','origin')",
            _PRESS_SOURCE,
        )
        == f"{_ORIGIN}/portal/board/boardDetail.do?bbs_no=bbs080&ntctxt_no=50448&menu_no=2857"
    )
    assert (
        resolve_article_link(
            "/portal/fooddanger/administMeasureList.do?menu_no=2714&menu_grp=MENU_NEW02",
            _ENFORCEMENT_SOURCE,
        )
        == ""
    )
    assert resolve_article_link("", _ENFORCEMENT_SOURCE) == ""
    assert resolve_article_link("javascript:void(0)", _ENFORCEMENT_SOURCE) == ""


def test_configured_sources_keep_notice_links_on_their_own_host() -> None:
    config = load_category_config("food")
    foodsafety = []
    other_rss = []
    for source in config.sources:
        hostname = (urlparse(source.url).hostname or "").lower()
        if hostname.endswith("foodsafetykorea.go.kr"):
            foodsafety.append(source)
        elif source.type.lower() == "rss":
            other_rss.append(source)

    assert {source.name for source in foodsafety} == {
        "식품안전나라 회수판매중지",
        "식품안전나라 행정처분",
        "식약처 보도자료",
        "식품안전 국내뉴스",
        "식품안전 해외뉴스",
    }
    assert {source.name for source in other_rss} == {"Food Safety News", "Food Dive"}

    relative_detail = "/portal/administMeasureDetail.do?seq=4749051"
    for source in foodsafety:
        resolved = resolve_article_link(relative_detail, source.url)
        assert resolved == f"{_ORIGIN}{relative_detail}"
        assert urlparse(resolved).hostname == urlparse(source.url).hostname

    domestic = next(source for source in foodsafety if source.name == "식품안전 국내뉴스")
    assert (
        resolve_article_link("javascript:viewDetail('1110421','user','origin')", domestic.url)
        == f"{_ORIGIN}/portal/board/boardDetail.do?bbs_no=bbs082&ntctxt_no=1110421&menu_no=2859"
    )

    preserved = {
        "https://www.foodsafetynews.com/feed/": (
            "https://www.foodsafetynews.com/2026/10/"
            "taylor-farms-recalls-salad-kits-because-of-undeclared-allergens-2/"
        ),
        "https://www.fooddive.com/feeds/news/": (
            "https://www.fooddive.com/news/GLP1-low-carb-atkins-diet-repositioned/873018/"
        ),
    }
    for base_url, article_url in preserved.items():
        assert resolve_article_link(article_url, base_url) == article_url
    assert (
        resolve_article_link("/news/example/", "https://www.fooddive.com/feeds/news/")
        == "https://www.fooddive.com/news/example/"
    )
    assert (
        resolve_article_link("javascript:void(0)", "https://www.fooddive.com/feeds/news/")
        == "javascript:void(0)"
    )
