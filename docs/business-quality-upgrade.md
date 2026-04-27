# Business Quality Upgrade

- Generated: `2026-04-14T04:48:11.525239+00:00`
- Portfolio verdict: `충분`
- Business value score: `85.9`
- Upgrade phase: P0 리콜/제품 alias 품질 강화
- Primary motion: `compliance-risk`
- Weakest dimension: `traceability`

## Current Evidence

- Primary rows: `1032`
- Today raw rows: `11`
- Latest report items: `34`
- Match rate: `97.1%`
- Collection errors: `0`
- Freshness gap: `4`

## Upgrade Actions

- 제품/제조사 alias map을 analyzer와 HTML/JSON 리포트 출력에 연결한다.
- recall_status_change의 notice date/status change를 stale/missing 요약에 포함한다.
- 소비자 complaint 후보는 API, ToS, 개인정보 검토 뒤 공식 리콜 근거의 보조 레이어로만 활성화한다.

## Quality Contracts

- `config/categories/food.yaml`: output `reports/food_quality.json`, tracked `recall_status_change, enforcement_action, complaint_signal`, backlog items `3`

## Contract Gaps

- None.
