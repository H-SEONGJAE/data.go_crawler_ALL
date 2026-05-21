# 공공데이터 포털 크롤링 통합 홈페이지

## 핵심 원칙

- `crawler_metadata.py`: 전체/기관별 메타데이터 수집 원본 엔진
- `crawler.py`: 기관별 조회수/다운로드 수 수집 원본 엔진
- `crawler_data.py`: 기관별 파일데이터 다운로드 엔진
- `metadata_runner.py`, `stats_runner.py`, `download_runner.py`: Streamlit에서 원본 엔진을 별도 프로세스로 실행하기 위한 wrapper
- `main.py`: Streamlit UI

## 로컬 실행

```bash
pip install -r requirements.txt
playwright install chromium
streamlit run main.py
```

