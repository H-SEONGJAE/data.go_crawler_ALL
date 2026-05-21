# 공공데이터 포털 크롤링 통합 홈페이지

## 핵심 원칙

검증 완료된 원본 크롤러 내부 로직은 수정하지 않고, Streamlit은 실행 wrapper 역할만 수행합니다.

- `crawler_metadata.py`: 전체/기관별 메타데이터 수집 원본 엔진
- `crawler.py`: 기관별 조회수/다운로드 수 수집 원본 엔진
- `crawler_data.py`: 기관별 파일데이터 다운로드 엔진. EXE/config 실행부만 제거하고 Streamlit 직접 호출용으로 최소 수정
- `metadata_runner.py`, `stats_runner.py`, `download_runner.py`: Streamlit에서 원본 엔진을 별도 프로세스로 실행하기 위한 wrapper
- `main.py`: Streamlit UI

## 로컬 실행

```bash
pip install -r requirements.txt
playwright install chromium
streamlit run main.py
```

## Streamlit Cloud

GitHub 저장소 루트에 파일을 업로드한 뒤 Streamlit 앱의 Main file path를 `main.py`로 지정합니다.

## 중지 기능

중지 버튼은 크롤러 내부 루프를 변경하지 않고, 실행 중인 wrapper 프로세스를 종료하는 방식입니다. 따라서 원본 크롤러의 수집/파싱 로직에 영향을 주지 않습니다.
