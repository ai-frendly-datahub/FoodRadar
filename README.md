# FoodRadar - 식품 안전 레이더

**🌐 Live Report**: https://ai-frendly-datahub.github.io/FoodRadar/

식품 안전 정보를 수집·분석하는 레이더. 식품안전나라 RSS 및 식약처 보도자료를 매일 수집하여 식품 유형·브랜드·안전 이슈별로 분류하고 GitHub Pages에 배포합니다.

## 개요

- **수집 소스**: 식품안전나라 회수판매중지, 행정처분, 식약처 보도자료, 식품안전 국내·해외뉴스
- **분석 대상**: 식품 유형(FoodType), 브랜드(Brand), 안전 이슈(SafetyIssue), 규제(Regulation)
- **출력**: GitHub Pages HTML 리포트 (Flatpickr 캘린더 + Chart.js 트렌드)

## 빠른 시작

```bash
pip install -e ".[dev]"
python main.py --once
```

## 구조

```
FoodRadar/
  foodradar/
    collector.py    # 식품안전나라 RSS 수집
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

## 개발

```bash
pytest tests/ -v
```

## 스케줄

GitHub Actions로 매일 자동 수집 후 GitHub Pages 배포.
