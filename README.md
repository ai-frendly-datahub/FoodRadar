# FoodRadar - 식품 안전 레이더

**🌐 Live Report**: https://ai-frendly-datahub.github.io/FoodRadar/

식품 안전 정보를 수집·분석하는 레이더입니다. 식품안전나라/식약처 공식 RSS와 해외 식품안전 매체, Reddit 커뮤니티 신호를 함께 수집해 식품 유형·브랜드·안전 이슈별로 분류하고 GitHub Pages에 배포합니다.

## 개요

- **수집 소스**: 식품안전나라 회수판매중지, 행정처분, 식약처 보도자료, 식품안전 국내·해외뉴스, 해외 식품안전 전문 매체, Reddit 커뮤니티
- **분석 대상**: 식품 유형(FoodType), 브랜드(Brand), 안전 이슈(SafetyIssue), 규제(Regulation)
- **출력**: GitHub Pages HTML 리포트 (Flatpickr 캘린더 + Chart.js 트렌드)
- **운영 메모**: MCP 서버는 `sources`가 아니라 `integration_candidates`로 관리합니다. capability 후보이며 표준 크롤 파이프라인에는 포함되지 않습니다.

## 빠른 시작

```bash
pip install -e ".[dev]"
python main.py --category food --recent-days 7
```

## 구조

```
FoodRadar/
  foodradar/
    collector.py    # RSS + Reddit 수집, 비표준 source type은 명시적으로 보고
    analyzer.py     # 엔티티 분석 (radar-core 위임)
    storage.py      # DuckDB 저장 (radar-core 위임)
    reporter.py     # HTML 리포트 생성 (radar-core 위임)
  config/
    config.yaml           # database_path, report_dir
    categories/food.yaml  # 수집 소스 + 엔티티 정의
  main.py           # CLI 진입점
  tests/            # 단위 테스트
```

## 설정

`config/config.yaml` 및 `config/categories/food.yaml` 참조.

- `type: rss`와 `type: reddit`는 표준 파이프라인에서 수집됩니다.
- `config/categories/food.yaml`의 `integration_candidates`는 향후 에이전트/도구 통합용 capability 후보입니다.
- 관련 메모: [integration-candidates.md](docs/integration-candidates.md)

## 데이터 품질 운영

- 품질 기준은 [data-quality-plan.md](docs/data-quality-plan.md)와 `config/categories/food.yaml`의 `data_quality` 섹션을 기준으로 관리합니다.
- 식품안전나라 회수판매중지 RSS는 `recall_status_change`, 행정처분 RSS는 `enforcement_action`, Reddit 소비자 신호는 `complaint_signal`로 분리합니다.
- 제품명·제조사·브랜드 alias는 `data_quality.canonical_keys` 기준으로 정규화하고, 공식 source와 커뮤니티 complaint는 같은 근거로 병합하지 않습니다.
- 매 실행 후 `reports/food_quality.json`과 일자별 `food_YYYYMMDD_quality.json`을 생성해 source별 freshness/stale/missing 상태와 alias 후보를 점검합니다.
- `consumer24_recall_and_damage`, `ccn_1372_consumer_counsel`, `mfds_fooddb_mcp`는 `source_backlog`/`integration_candidates`에 남기고 API·ToS·개인정보 검토 전까지 표준 수집 source로 활성화하지 않습니다.

## 개발

```bash
pytest tests/ -v
```

## 스케줄

GitHub Actions로 매일 자동 수집 후 GitHub Pages 배포.

<!-- DATAHUB-OPS-AUDIT:START -->
## DataHub Operations

- CI/CD workflows: `deploy-pages.yml`, `radar-crawler.yml`.
- GitHub Pages visualization: `reports/index.html` (valid HTML); https://ai-frendly-datahub.github.io/FoodRadar/.
- Latest remote Pages check: HTTP 200, HTML.
- Local workspace audit: 19 Python files parsed, 0 syntax errors.
- Re-run audit from the workspace root: `python scripts/audit_ci_pages_readme.py --syntax-check --write`.
- Latest audit report: `_workspace/2026-04-14_github_ci_pages_readme_audit.md`.
- Latest Pages URL report: `_workspace/2026-04-14_github_pages_url_check.md`.
<!-- DATAHUB-OPS-AUDIT:END -->
