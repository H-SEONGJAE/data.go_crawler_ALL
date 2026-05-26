# 공공데이터포털 통합 크롤러

## 실행

```bash
pip install -r requirements.txt
playwright install chromium
streamlit run app.py
```

## 핵심 구조

- `app.py`: Streamlit 통합 UI
- `org_resolver.py`: 기관명 일부 검색 → 기관 후보 선택 → 파일데이터 목록 URL/상세 URL 교차 검증
- `crawler_metadata.py`: 기존 메타데이터 수집 엔진 원본 유지
- `metadata_bridge.py`: Resolver가 만든 상세 URL 목록을 기존 메타데이터 엔진에 전달
- `crawler.py`: 기존 조회수/다운로드 수 수집 코드 원본 유지
- `stats_runner.py`: 기존 `crawler.py` 실행 후 실패 시 Resolver manifest fallback
- `crawler_data_integrated.py`: 기존 다운로드 과정 유지 + EXE 의존 제거 + 파일데이터 목록 진입 안정화
- `download_runner.py`: Streamlit subprocess 실행용 다운로드 wrapper

## URL 처리 원칙

단순히 `?org=기관명` URL을 조립하지 않습니다. `org_resolver.py`가 Playwright로 포털 화면에서 실제 검색을 수행하고, 후보 기관을 선택한 뒤 상세페이지 제공기관 링크/목록 li/상세 URL/메타테이블을 교차 검증합니다.

## UIUX

macOS/Figma 톤의 카드형 UI를 적용했으며, 글씨/URL 잘림 방지를 위해 `white-space: normal`, `overflow-wrap: anywhere`, `height:auto` 기준으로 구성했습니다.
