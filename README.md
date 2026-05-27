# 공공데이터포털 통합 크롤러 - 기관 파일데이터 목록 URL 방식

## 핵심 변경
- 기관별 수집에서 상세 URL 전체를 먼저 모으지 않습니다.
- 기관명 검색 후 제공기관을 선택하면, 상세페이지 안의 제공기관 링크를 역추적해 `기관의 파일데이터 목록 URL` 하나만 확보합니다.
- 확보한 목록 URL을 기존 크롤러 코드에 전달합니다.

## 실행
```bash
pip install -r requirements.txt
streamlit run main.py
```

파일 다운로드 기능은 Playwright 브라우저가 필요합니다.
```bash
python -m playwright install chromium
```

## 포함 파일
- main.py: Streamlit UI
- org_provider_url_resolver.py: 제공기관 후보 검색 및 기관 파일데이터 목록 URL 확보
- metadata_listurl_runner.py: 기존 crawler_metadata.py 실행 래퍼
- stats_listurl_runner.py: 기존 crawler.py 실행 래퍼
- download_listurl_runner.py: 기관 목록 URL 기반 다운로드 실행
- crawler_metadata.py: 원본 메타데이터 크롤러
- crawler.py: 원본 조회수/다운로드 수 크롤러
- crawler_data_original.py: 원본 다운로드 크롤러 보존
