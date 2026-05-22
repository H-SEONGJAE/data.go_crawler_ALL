# 검증 메모

이 패키지는 다음 항목을 로컬 코드 수준에서 검증했습니다.

- 전체 Python 파일 `compileall` 문법 검사 통과
- `metadata_runner.py --help` 실행 확인
- `stats_runner.py --help` 실행 확인
- `download_runner.py --help` 실행 확인
- 샘플 HTML 기준 제공기관 후보 추출 확인
- 샘플 HTML 기준 `강원도 고성군` 검색 시 `강원특별자치도 고성군`, `경상남도 고성군`, `고성군` 후보가 모두 표시되는 구조 확인

주의: 현재 ChatGPT 실행 환경에서는 공공데이터포털 실시간 end-to-end 접속 수집 검증이 제한될 수 있습니다. 실제 포털 접속 검증은 사용자의 로컬 PC에서 `streamlit run app.py`로 확인해야 합니다.
