# FoodRadar Integration Candidates

`FoodRadar`는 시간순 기사/공지 피드와 에이전트 capability 후보를 분리해서 관리한다.

## 원칙

- `sources`: 리포트 파이프라인이 직접 수집하는 시간순 신호
- `integration_candidates`: 에이전트가 질의형으로 붙일 수 있는 capability 후보

## 현재 후보

- `식약처 식품DB MCP`
  - 역할: 식품 영양·성분 조회
  - 이유: 공식성이 높지만 시계열 뉴스 피드가 아니라 질의형 reference API에 가깝다
  - 현재 위치: `config/categories/food.yaml`의 `integration_candidates`
  - 향후 쓰임새: 상품 비교, 영양 요약, 성분 기반 보강 분석

## 데이터 품질 backlog

- `consumer24_recall_and_damage`
  - 역할: 소비자24 상품안전·피해구제 기반 리콜 상태와 피해구제 신호 후보
  - 이유: 공식 소비자 포털이지만 표준 RSS 파이프라인 대상이 아니므로 API/검색 경로와 개인정보 노출 범위를 먼저 검증해야 한다
  - 현재 위치: `config/categories/food.yaml`의 `source_backlog.consumer_complaint_candidates`
  - 향후 쓰임새: `recall_status_change`의 해제/확산 여부 보조 검증
- `ccn_1372_consumer_counsel`
  - 역할: 1372 소비자상담센터 기반 complaint 보조 검증 후보
  - 이유: 상담 데이터는 개인정보와 상담 내용 노출 범위가 핵심 리스크라 공식 리콜 source와 바로 병합하면 안 된다
  - 현재 위치: `config/categories/food.yaml`의 `source_backlog.consumer_complaint_candidates`
  - 향후 쓰임새: `complaint_signal` 이벤트의 조기 경보와 중복 complaint 추적
