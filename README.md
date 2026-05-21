# 공공데이터 포털 크롤링 통합 홈페이지

Streamlit 기반 공공데이터포털 크롤링 통합 홈페이지입니다.

## 메뉴 구성

1. 공공데이터 포털 메타데이터 크롤링
   - 전체 파일데이터 메타데이터 수집
   - 기관별 파일데이터 메타데이터 수집
2. 기관별 포털 데이터 목록 조회수 및 다운로드 수
   - 기관별 파일데이터명, 조회수, 다운로드수 수집
3. 기관별 포털 파일데이터 다운로드 크롤러
   - crawler_data.py를 Streamlit에서 직접 호출
   - EXE 실행, config.json 다운로드, GitHub ZIP 다운로드 방식 제거
   - 기존 현재데이터/과거데이터 다운로드 로직과 폴더 구조 유지

## 핵심 파일

```text
main.py                 # Streamlit 메인 UI
crawler_metadata.py     # 전체/기관별 메타데이터 수집 엔진
page1_org_metadata.py   # 기관별 메타데이터 UI
crawler.py              # 조회수/다운로드수 수집 엔진
crawler_data.py         # 파일데이터 다운로드 엔진(Streamlit 직접 호출형)
requirements.txt        # Python 의존성
packages.txt            # Streamlit Cloud/Linux 시스템 패키지
runtime.txt             # Python 버전 고정
```

## 로컬 실행 방법

```bash
pip install -r requirements.txt
playwright install chromium
streamlit run main.py
```

## GitHub 업로드 절차

```bash
git init
git add .
git commit -m "Initial Streamlit portal crawler"
git branch -M main
git remote add origin https://github.com/<계정>/<저장소명>.git
git push -u origin main
```

## Streamlit Community Cloud 배포 절차

1. GitHub 저장소에 이 폴더의 파일을 업로드합니다.
2. Streamlit Community Cloud에서 `New app`을 선택합니다.
3. Repository, Branch, Main file path를 선택합니다.
   - Main file path: `main.py`
4. Deploy를 누릅니다.
5. Python 패키지는 `requirements.txt`, 시스템 패키지는 `packages.txt` 기준으로 설치됩니다.

## 주의사항

- 공공데이터포털 대량 크롤링은 요청 제한, 403/429, 네트워크 상태 영향을 받을 수 있습니다.
- 전체 메타데이터 8만 건 이상 수집은 시간이 오래 걸리므로 로컬 실행을 우선 권장합니다.
- 3번 파일데이터 다운로드 기능은 기존 `crawler_data.py`의 selector와 다운로드 흐름을 유지했습니다. 변경된 부분은 EXE/config 실행 제거, Streamlit callback 추가, Playwright 브라우저 실행 방식뿐입니다.

## 진행상황/시작/중지 표시

이번 버전은 각 크롤링 파트별로 다음 UI를 제공합니다.

- 시작 버튼: 크롤링 작업을 백그라운드에서 실행
- 중지 버튼: 현재 처리 중인 요청/다운로드를 마친 뒤 안전하게 종료 요청
- 진행상황 패널: 상태, 처리 건수, 경과 시간, 최근 메시지, 실행 로그 표시
- 자동 새로고침: 작업 실행 중 1초 단위로 화면을 갱신하여 진행 로그 표시

중지 기능은 이미 저장된 결과를 삭제하지 않습니다. 중지 시점까지 수집/다운로드된 결과만 남습니다.
