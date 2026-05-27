# 공공데이터포털 통합 크롤러 - 기관 유사도 점수 기반 URL 수집 버전

## 핵심 변경사항

1. `org=기관명` 직접 조립 방식을 사용하지 않습니다.
2. 검색어 기반 파일데이터 목록에서 상세 URL을 수집합니다.
3. 상세페이지의 `제공기관` 값을 읽어 입력 검색어와 유사도 점수를 계산합니다.
4. 기본 80점 이상 후보만 기관 선택 목록에 표시합니다.
5. 선택 기관 기준으로 다시 상세 URL을 수집하고, 제공기관명 유사도 80점 이상인 URL만 확정합니다.
6. Connection reset 대응을 위해 Edge 계열 User-Agent, requests Session, urllib3 Retry, 수동 재시도, 지터 대기를 적용했습니다.
7. UI는 밝은 박스 + 검정 글씨 + 균일 카드 높이 기준으로 재구성했습니다.

## 실행

```bash
pip install -r requirements.txt
streamlit run main.py
```

파일데이터 다운로드 기능은 Playwright 브라우저가 필요합니다.

```bash
python -m playwright install chromium
```

## 사용 흐름

1. 왼쪽 메뉴 `기관 검색 · URL 수집` 선택
2. 기관명 일부 입력 예: `중부발전`
3. 후보 검색 페이지와 점수 기준 설정 후 `기관 후보 검색`
4. 80점 이상 제공기관 후보 중 선택
5. `선택 기관 URL 확정`
6. 메타데이터 / 조회수·다운로드 수 / 파일데이터 다운로드 실행

## 오류 대응

- `Connection reset by peer`가 간헐적으로 나면 같은 버튼을 다시 누르지 말고 10~30초 후 재시도하세요.
- 후보 검색 페이지 수를 너무 크게 잡으면 차단 가능성이 올라갑니다. 처음에는 2~3페이지 권장입니다.
- 파일 다운로드 시 Playwright 브라우저 오류가 나면 `python -m playwright install chromium`을 실행하세요.
