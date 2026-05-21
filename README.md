# 공공데이터 포털 크롤링 통합 홈페이지

## 실행
```bash
pip install -r requirements.txt
playwright install chromium
streamlit run main.py
```

## 구성
- `main.py`: Streamlit 메인 UI
- `crawler_metadata.py`: 전체/기관별 메타데이터 수집 엔진
- `crawler.py`: 기관별 조회수/다운로드 수 수집 엔진
- `crawler_data.py`: 기관별 파일데이터 다운로드 엔진
- `metadata_runner.py`, `stats_runner.py`, `download_runner.py`: Streamlit 백그라운드 실행 wrapper
- `streamlit_task_ui.py`: 진행률/로그/결과 다운로드 UI

## 원칙
검증 완료된 크롤러의 수집 루프와 selector를 불필요하게 바꾸지 않고, Streamlit에서는 실행/진행률/결과 다운로드를 담당합니다.
