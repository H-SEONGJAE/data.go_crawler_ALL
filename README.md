# 공공데이터 포털 크롤링 통합 Streamlit

## 기능

Streamlit 탭 UI로 5개 영역을 제공합니다.

1. **전체 메타데이터**: 공공데이터포털 파일데이터 전체 목록의 상세 메타데이터 수집
2. **기관 메타데이터**: 기관명 입력 기반 기관별 파일데이터 메타데이터 수집
3. **조회/다운로드 집계**: 기관별 파일데이터 목록의 조회수/다운로드수 엑셀 수집
4. **최신·과거 다운로드**: 기관별 최신 파일과 과거데이터 다운로드 후 ZIP 생성
5. **실행 이력**: `runs` 폴더 기준 최근 실행 로그와 결과 확인

## 실행 방법

```bash
pip install -r requirements.txt
python -m playwright install chromium
streamlit run app.py
```

## GitHub / Streamlit 배포

저장소 루트에 아래 파일을 그대로 올립니다.

- app.py
- portal_common.py
- crawler_metadata.py
- crawler.py
- crawler_data.py
- metadata_runner.py
- stats_runner.py
- download_runner.py
- requirements.txt
- packages.txt

Streamlit Cloud는 `requirements.txt`를 읽어 Python 패키지를 설치하고, `packages.txt`를 읽어 시스템 Chromium을 설치합니다.

## UI/UX 변경 사항

- 기존 사이드바 메뉴를 제거하고 `st.tabs()` 기반 탭 화면으로 변경했습니다.
- 각 기능 탭 안에 입력, 고급 옵션, 실행 상태, 실행 로그, 결과 다운로드 영역을 분리했습니다.
- 실행 중 작업 중단 버튼과 완료 후 상태 초기화 버튼을 추가했습니다.
- 조회수/다운로드수 결과는 수집 건수와 미리보기를 함께 제공합니다.
- 실행 결과는 `runs/` 하위에 작업별 폴더로 저장되며, 실행 이력 탭에서 최근 로그를 확인할 수 있습니다.

## 설계 요약

- 기존 메타데이터 크롤러의 상세 파싱/저장 로직은 유지했습니다.
- URL 직접 입력 방식은 제거하고 `portal_common.build_file_list_url()`에서 기관명 기반 목록 URL을 생성합니다.
- 긴 작업은 Streamlit 본문에서 직접 실행하지 않고 runner subprocess로 실행해 로그와 결과 파일을 분리했습니다.
- 다운로드 크롤러는 EXE 전용 Chromium 경로를 제거하고 Playwright 기본 브라우저 또는 시스템 Chromium을 사용합니다.
- 다운로드 작업 종료 시 context/browser를 finally에서 닫고 ZIP 생성 후 subprocess가 종료됩니다.
