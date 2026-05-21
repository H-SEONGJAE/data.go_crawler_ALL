# -*- coding: utf-8 -*-
"""
Streamlit wrapper runner for crawler.py.

원칙
- crawler.py 내부 Selenium 크롤링 로직은 수정하지 않는다.
- 기관명이 약식으로 들어온 경우 wrapper에서 후보 기관명 URL만 재시도한다.
"""
import argparse
import json
import sys
import time
import urllib.parse
from pathlib import Path

import pandas as pd
from crawler import collect_file_data_from_url

try:
    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)
except Exception:
    pass


def make_org_candidates(user_input: str) -> list[str]:
    base = (user_input or "").strip()
    if not base:
        return []
    candidates = [base]
    if "(주)" not in base and "㈜" not in base:
        candidates.extend([base + "(주)", base + "㈜"])
    else:
        candidates.extend([base.replace("(주)", "㈜"), base.replace("㈜", "(주)")])
    if "강원특별자치도" in base:
        candidates.append(base.replace("강원특별자치도", "강원도"))
    if "강원도" in base:
        candidates.append(base.replace("강원도", "강원특별자치도"))
    return list(dict.fromkeys([c for c in candidates if c.strip()]))


def build_org_url(org_name: str) -> str:
    """
    조회수/다운로드 수 수집은 검증된 crawler.py가 FILE 탭을 직접 클릭하는 구조이므로,
    기존에 정상 동작하던 최소 기관 URL을 사용한다.
    """
    org = (org_name or "").strip()
    return "https://www.data.go.kr/tcs/dss/selectDataSetList.do?org=" + urllib.parse.quote(org)


def main():
    parser = argparse.ArgumentParser(description="기관별 조회수/다운로드 수 수집 wrapper")
    parser.add_argument("--org-name", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--result-json", required=True)
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    org_input = args.org_name.strip()
    candidates = make_org_candidates(org_input)

    print("=" * 80, flush=True)
    print("[Streamlit wrapper - stats_runner]", flush=True)
    print(f"- org_name: {org_input}", flush=True)
    print(f"- candidates: {candidates}", flush=True)
    print("※ crawler.py 원본 collect_file_data_from_url()을 실행합니다.", flush=True)
    print("=" * 80, flush=True)

    def update_status(msg):
        print(msg, flush=True)

    last_error = None
    used_org = org_input
    used_url = ""
    df = pd.DataFrame()

    for idx, org in enumerate(candidates, start=1):
        target_url = build_org_url(org)
        used_org = org
        used_url = target_url
        print(f"\n[기관 후보 {idx}/{len(candidates)}] {org}", flush=True)
        print(f"- target_url: {target_url}", flush=True)
        try:
            df = collect_file_data_from_url(target_url, status_callback=update_status)
            if df is not None and not df.empty:
                print(f"[성공] {org} 기준 {len(df):,}건 수집", flush=True)
                break
            print(f"[알림] {org} 기준 수집 결과 0건. 다음 후보를 확인합니다.", flush=True)
        except Exception as e:
            last_error = e
            print(f"[경고] {org} 기준 수집 실패: {repr(e)}", flush=True)
            df = pd.DataFrame()
            continue

    if df is None or df.empty:
        if last_error is not None:
            raise RuntimeError(f"모든 기관 후보에서 조회수/다운로드 수 수집 실패. 마지막 오류: {repr(last_error)}")
        raise RuntimeError("모든 기관 후보에서 수집 결과가 0건입니다.")

    timestamp = time.strftime("%Y%m%d_%H%M%S")
    safe_org_name = used_org.replace("(", "_").replace(")", "")
    excel_path = output_dir / f"공공데이터_{safe_org_name}_조회수_다운로드수_{timestamp}.xlsx"

    with pd.ExcelWriter(excel_path, engine="xlsxwriter", engine_kwargs={"options": {"strings_to_urls": False}}) as writer:
        df.to_excel(writer, index=False, sheet_name="FILE_집계")

    result = {
        "status": "completed",
        "org_name": used_org,
        "target_url": used_url,
        "row_count": int(len(df)),
        "output_dir": str(output_dir),
        "excel_path": str(excel_path),
    }
    Path(args.result_json).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[저장 완료] {excel_path}", flush=True)


if __name__ == "__main__":
    main()
