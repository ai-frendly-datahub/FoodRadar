# Data Quality Plan

- 생성 시각: `2026-04-11T16:05:37.910248+00:00`
- 우선순위: `P0`
- 데이터 품질 점수: `93`
- 가장 약한 축: `추적성`
- Governance: `high`
- Primary Motion: `compliance-risk`

## 현재 이슈

- 현재 설정상 즉시 차단 이슈 없음. 운영 지표와 freshness SLA만 명시하면 됨

## 필수 신호

- 리콜·회수·판매중단 공식 고시
- 제품명·제조사·브랜드·유통채널 식별자
- 소비자 불만과 판매/유통 확산 신호

## 품질 게이트

- 리콜 상태와 고시일·해제일을 분리
- 동일 제품의 표기 차이를 alias로 관리
- 공식 source와 커뮤니티 complaint를 같은 근거로 섞지 않음

## 구현 완료

- `reports/food_quality.json`과 일자별 `food_YYYYMMDD_quality.json`으로 source별 freshness/stale/missing 상태를 검증 산출물에 추가
- 리포트에는 event model, freshness SLA, 최신 event date, collection error, Product/Brand/Manufacturer alias 후보를 포함
- Product/Manufacturer alias map을 analyzer에 연결하고 canonical product/manufacturer field와 alias trace를 JSON/HTML 리포트에 출력
- `food_quality.json`의 stale/missing 요약을 HTML Quality Traceability 패널과 summary JSON `quality_summary`/`quality_flagged_sources`에 노출
- 식품안전나라 회수판매중지 공식 리콜 feed는 title/summary 기반 product/manufacturer fallback extraction으로 food event key를 보강
- Product/FoodType/FoodGeneral/SafetyIssue/Brand keyword coverage를 보강해 최신 1일 리포트 매칭률을 `16/16`으로 회복
- 저장 DB에서 다시 읽은 recent article도 현재 엔티티·별칭·소스 규칙으로 재분석해 리포트와 품질 점검의 규칙 적용을 일치시킴
- `match_coverage_review_items`를 quality JSON, HTML 패널, summary JSON에 추가해 미매칭 표본을 운영 큐로 노출

## 다음 구현 순서

- `match_coverage_review_items`에 남는 이미지 중심 커뮤니티 항목을 일일 점검하되, 의미 없는 광역 키워드로 매칭률을 부풀리지 않음
- 소비자 complaint 후보는 source_backlog에서 API·ToS·개인정보 검토 후 보조 레이어로 활성화
- 판매중단 확산·유통 가격 데이터를 후속 운영 source로 보강

## 운영 규칙

- 원문 URL, 수집일, 이벤트 발생일은 별도 필드로 유지한다.
- 공식 source와 커뮤니티/시장 source를 같은 신뢰 등급으로 병합하지 않는다.
- collector가 인증키나 네트워크 제한으로 skip되면 실패를 숨기지 말고 skip 사유를 기록한다.
- 이 문서는 `scripts/build_data_quality_review.py --write-repo-plans`로 재생성한다.
