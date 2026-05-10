# Business Quality Upgrade

- Generated: `2026-04-29T09:21:50+09:00`
- Portfolio verdict: `충분`
- Business value score: `87.2`
- Upgrade phase: P0 리콜/제품 매칭 커버리지 보강 완료
- Primary motion: `compliance-risk`
- Weakest dimension: `traceability`

## Current Evidence

- Primary rows: `1314`
- Today raw rows: `22`
- Latest report items: `16`
- Match rate: `100.0%`
- Collection errors: `0`
- Freshness gap: `0`

## Upgrade Actions

- `match_coverage_review_items`를 일일 운영 큐로 사용해 7일 창의 이미지 중심 커뮤니티 잔여 항목을 점검한다.
- 소비자 complaint 후보는 API, ToS, 개인정보 검토 뒤 공식 리콜 근거의 보조 레이어로만 활성화한다.
- 판매중단 확산·유통 가격 데이터를 후속 운영 source로 보강한다.

## Completed Actions

- Product/Brand/Manufacturer alias map을 analyzer에 연결했다.
- Product/Manufacturer canonical field와 alias trace를 `food_quality.json`, HTML Quality Traceability 패널, summary JSON에 노출했다.
- recall_status_change의 notice date/status와 stale/missing source summary를 quality report와 summary JSON에 포함했다.
- 식품안전나라 회수판매중지의 공식 리콜 feed는 title/summary 기반 product/manufacturer fallback extraction을 적용했다.
- Food Dive와 high-signal community 항목의 Product/FoodType/FoodGeneral/SafetyIssue keyword coverage를 보강했다.
- 저장 DB의 과거 기사도 report/quality 생성 시 현재 엔티티·별칭·소스 규칙으로 재분석하도록 보강했다.
- `match_coverage_review_items`를 quality JSON, HTML 패널, summary JSON에 노출해 이후 미매칭 표본을 직접 추적할 수 있게 했다.

## Quality Contracts

- `config/categories/food.yaml`: output `reports/food_quality.json`, tracked `recall_status_change, enforcement_action, complaint_signal`, backlog items `3`

## Contract Gaps

- None.
