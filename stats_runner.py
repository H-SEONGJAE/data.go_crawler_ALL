# -*- coding: utf-8 -*-
"""
Streamlit wrapper runner for crawler.py.

중요 원칙:
- crawler.py 내부 Selenium 크롤링 로직은 수정하지 않는다.
- 진행상황은 기존 status_callback만 사용한다.
"""
import argparse
import sys
import json
import time
import urllib.parse
from pathlib import Path

import pandas as pd

from crawler import collect_file_data_from_url


# Keep redirected stdout/stderr line-buffered for the Streamlit live log panel.
try:
    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)
except Exception:
    pass



def build_org_file_list_url(org_name: str) -> str:
    """crawler.py에 넘길 기관별 파일데이터 목록 URL 생성."""
    org = " ".join(str(org_name or "").strip().split())
    params = {
        "dType": "FILE",
        "keyword": "",
        "detailKeyword": "",
        "publicDataPk": "",
        "recmSe": "",
        "detailText": "",
        "relatedKeyword": "",
        "commaNotInData": "",
        "commaAndData": "",
        "commaOrData": "",
        "must_not": "",
        "tabId": "",
        "dataSetCoreTf": "",
        "coreDataNm": "",
        "sort": "updtDt",
        "relRadio": "",
        "orgFullName": org,
        "orgFilter": org,
        "org": org,
        "orgSearch": "",
        "currentPage": "1",
        "perPage": "10",
        "brm": "",
        "instt": "",
        "svcType": "",
        "kwrdArray": "",
        "extsn": "",
        "coreDataNmArray": "",
        "operator": "AND",
        "pblonsipScopeCode": "PBDE07",
    }
    return "https://www.data.go.kr/tcs/dss/selectDataSetList.do?" + urllib.parse.urlencode(params)



def main():
    parser = argparse.ArgumentParser(description="기관별 조회수/다운로드 수 수집 wrapper")
    parser.add_argument("--org-name", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--result-json", required=True)
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    org = " ".join(args.org_name.strip().split())
    target_url = build_org_file_list_url(org)

    print("=" * 80, flush=True)
    print("[Streamlit wrapper - stats_runner]", flush=True)
    print(f"- org_name: {org}", flush=True)
    print(f"- target_url: {target_url}", flush=True)
    print("※ crawler.py 원본 collect_file_data_from_url()을 그대로 실행합니다.", flush=True)
    print("=" * 80, flush=True)

    def update_status(msg):
        print(msg, flush=True)

    df = collect_file_data_from_url(target_url, status_callback=update_status)

    timestamp = time.strftime("%Y%m%d_%H%M%S")
    safe_org_name = org.replace("(", "_").replace(")", "")
    excel_path = output_dir / f"공공데이터_{safe_org_name}_조회수_다운로드수_{timestamp}.xlsx"

    with pd.ExcelWriter(excel_path, engine="xlsxwriter", engine_kwargs={"options": {"strings_to_urls": False}}) as writer:
        df.to_excel(writer, index=False, sheet_name="FILE_집계")

    result = {
        "status": "completed",
        "org_name": org,
        "row_count": int(len(df)),
        "output_dir": str(output_dir),
        "excel_path": str(excel_path),
    }
    Path(args.result_json).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[저장 완료] {excel_path}", flush=True)


if __name__ == "__main__":
    main()
