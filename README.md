# 공공데이터포털 통합 크롤러 - URL Resolver 개선 버전

## 핵심 변경

기존 참고 코드의 기관별 URL 수집은 `selectDataSetList.do?...&org=기관명`을 직접 붙이는 방식이었습니다. 이 방식은 포털의 실제 검색/필터 상태와 맞지 않아 0건, 상세 URL 누락, 다음 단계 미진행 오류가 발생할 수 있습니다.

이번 버전은 다음 방식으로 변경했습니다.

1. 기관명 일부 또는 관련 검색어로 파일데이터를 keyword 검색합니다.
2. 검색 결과의 상세페이지 URL을 수집합니다.
3. 상세페이지 메타데이터 table에서 `제공기관` 값을 읽어 기관 후보를 만듭니다.
4. 사용자가 후보 기관을 선택합니다.
5. 선택 기관명과 상세페이지의 제공기관명을 다시 비교해 검증 통과 URL만 확정합니다.
6. 메타데이터/조회수·다운로드 수/파일 다운로드는 검증된 상세 URL 목록으로 실행합니다.

즉, `org=` URL 직접 조립은 사용하지 않습니다.

## 실행 방법

```bash
pip install -r requirements.txt
playwright install chromium
streamlit run main.py
```

## 파일 구성

```text
main.py                         Streamlit UI. 왼쪽 메뉴 기반.
org_url_resolver.py             기관 후보 검색, 상세 URL 수집, 제공기관 교차 검증.
metadata_resolved_runner.py     검증 URL 목록을 기존 crawler_metadata.py 상세 수집 엔진에 연결.
stats_resolved_runner.py        검증 URL 목록의 조회수/다운로드 수를 엑셀로 저장.
download_resolved_runner.py     검증 상세 URL 목록으로 최신/과거 파일 다운로드.
crawler_metadata.py             기존 원본 메타데이터 크롤러 보존.
crawler.py                      기존 원본 조회수/다운로드 수 크롤러 보존.
crawler_data_original.py        기존 원본 파일데이터 다운로드 크롤러 보존.
main_legacy_uploaded.py         사용자가 올린 기존 UI 원본 보존.
page1_org_metadata_legacy_uploaded.py 사용자가 올린 기관별 메타데이터 탭 원본 보존.
```

## 사용 순서

1. `기관 검색 · URL 검증` 메뉴에서 `중부발전`처럼 일부 기관명을 입력합니다.
2. `기관 후보 검색`을 실행합니다.
3. 제공기관 후보 목록에서 실제 수집 기관을 선택합니다.
4. `선택 기관 URL 교차 검증`을 실행합니다.
5. 검증 통과 URL 건수를 확인합니다.
6. 다른 메뉴에서 수집 기능을 실행합니다.

## 주의

- 이 코드는 URL 수집 오류를 줄이기 위해 상세페이지의 제공기관 값을 기준으로 교차 검증합니다.
- 포털 HTML 구조가 변경되면 selector 보강이 필요할 수 있습니다.
- 대량 다운로드 전에는 다운로드 메뉴의 최대 건수를 1~3으로 두고 먼저 테스트하세요.
