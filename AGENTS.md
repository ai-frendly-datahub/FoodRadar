# FOODRADAR

식품 안전 정보를 수집·분석하는 Standard Tier 레이더입니다. 식품안전나라/식약처 공식 RSS, 해외 식품안전 매체, Reddit 커뮤니티 신호를 함께 수집하고 식품 유형·브랜드·안전 이슈·규제 엔티티로 분류합니다.

## STRUCTURE

```
FoodRadar/
├── foodradar/
│   ├── collector.py              # collect_sources() — RSS + Reddit 수집, 비표준 소스 타입 명시 보고
│   ├── analyzer.py               # apply_entity_rules() — 식품/브랜드/안전이슈 키워드 매칭
│   ├── reporter.py               # generate_report(), generate_index_html()
│   ├── storage.py                # RadarStorage — DuckDB upsert/query/retention
│   ├── models.py                 # radar-core 기반 모델 재사용
│   ├── config_loader.py          # YAML 로딩
│   ├── logger.py                 # structlog 기반 로깅
│   ├── resilience.py             # 재시도/장애 격리 유틸
│   └── exceptions.py             # 커스텀 예외 클래스
├── config/
│   ├── config.yaml
│   └── categories/food.yaml      # 소스 + 엔티티 정의
├── data/                         # DuckDB, raw data
├── reports/                      # 일자별 summary + index.html
├── tests/                        # analyzer / reporter / storage 테스트
├── docs/                         # 분석 산출물
└── main.py                       # CLI 엔트리포인트
```

## ENTITIES

| Entity | Examples |
|--------|----------|
| FoodType | 가공식품, 건강기능식품, 수산물, 축산물, 유제품 |
| Brand | 브랜드명, 제조사명 |
| SafetyIssue | 회수, 이물질, 위해성, 식중독, 부적합 |
| Regulation | 행정처분, 표시기준, 수입검사, 규제 변경 |

## DEVIATIONS FROM TEMPLATE

- `radar-core` 공통 파이프라인 위에 `foodradar/*` 래퍼 모듈을 두는 경량 Standard 패턴
- dated snapshot 정책을 기본으로 사용
- `reports/`에 일자별 `*_summary.json` 누적 산출물이 존재
- `config/categories/food.yaml`의 `integration_candidates`는 식품 영양/성분 조회 capability 후보이며 standard source pipeline과 분리한다.

## COMMANDS

```bash
python main.py --category food --recent-days 7
pytest tests/ -v
```

## NOTES

- 식품 소스 추가 시 `config/categories/food.yaml` 먼저 수정
- 표준 수집 경로는 `rss`와 `reddit`만 지원한다. MCP 서버는 `integration_candidates`로 기록하고 별도 agent/tool 통합 경로에서 다룬다.
- capability 후보 메모는 [integration-candidates.md](/home/kjs/projects/ai-frendly-datahub/FoodRadar/docs/integration-candidates.md)를 본다.
- 출력 경로와 파일명 규칙을 바꾸면 `radar-dashboard` 영향 여부를 확인
