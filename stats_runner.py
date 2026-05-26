# -*- coding: utf-8 -*-
"""
stats_runner.py

조회수/다운로드 수 수집 실행기.
1순위: Resolver가 검증한 resolved_url을 기존 crawler.py에 전달
2순위: 기존 crawler.py가 실패/빈 결과이면 Resolver manifest(detail_items)에 포함된 목록 카드 값을 엑셀로 저장
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Dict, List

import pandas as pd

from crawler import collect_file_data_from_url


def manifest_to_df(detail_items: List[Dict]) -> pd.DataFrame:
    rows = []
    for item in detail_items or []:
        rows.append({
            "데이터명": item.get("title") or item.get("raw_title") or item.get("파일데이터명") or "",
            "조회수": pd.to_numeric(item.get("조회수", ""), errors="coerce"),
            "다운로드수": pd.to_numeric(item.get("다운로드수") or item.get("다운로드(바로가기)") or "", errors="coerce"),
            "상세페이지 URL": item.get("detail_url", ""),
            "source_list_url": item.get("source_list_url", ""),
        })
    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.drop_duplicates(subset=["데이터명", "상세페이지 URL"], keep="first")
    return df


def write_excel(df: pd.DataFrame, path: str | Path) -> str:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(path, engine="xlsxwriter", engine_kwargs={"options": {"strings_to_urls": False}}) as writer:
        df.to_excel(writer, index=False, sheet_name="FILE_집계")
    return str(path)


def main():
    parser = argparse.ArgumentParser(description="조회수/다운로드 수 수집")
    parser.add_argument("--resolution-json", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--result-json", required=True)
    args = parser.parse_args()

    data = json.loads(Path(args.resolution_json).read_text(encoding="utf-8"))
    org = data.get("selected_provider") or data.get("input_keyword") or "기관"
    resolved_url = data.get("resolved_url", "")
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    print("=" * 80, flush=True)
    print("[stats_runner] 조회수/다운로드 수 수집", flush=True)
    print(f"- org: {org}", flush=True)
    print(f"- resolved_url: {resolved_url}", flush=True)
    print("- 1순위: 기존 crawler.py URL 실행", flush=True)
    print("- 2순위: Resolver manifest fallback", flush=True)
    print("=" * 80, flush=True)

    df = pd.DataFrame()
    mode = "crawler_url"
    error_msg = ""
    try:
        df = collect_file_data_from_url(resolved_url, status_callback=lambda msg: print(msg, flush=True))
        if df is None or df.empty:
            raise RuntimeError("기존 crawler.py 결과가 비어 있습니다.")
    except Exception as e:
        error_msg = repr(e)
        print(f"[경고] 기존 crawler.py 방식 실패 또는 빈 결과: {error_msg}", flush=True)
        print("[Fallback] Resolver manifest의 목록 카드 조회수/다운로드수로 저장합니다.", flush=True)
        df = manifest_to_df(data.get("detail_items", []))
        mode = "resolver_manifest_fallback"

    if df is None or df.empty:
        raise RuntimeError("조회수/다운로드 수 수집 결과가 비어 있습니다. URL 검증 결과와 포털 화면을 확인하세요.")

    safe_org = str(org).replace("/", "_").replace("\\", "_")
    excel_path = output_dir / f"공공데이터_{safe_org}_조회수_다운로드수.xlsx"
    write_excel(df, excel_path)

    result = {
        "status": "completed",
        "mode": mode,
        "error_msg": error_msg,
        "org": org,
        "resolved_url": resolved_url,
        "rows": int(len(df)),
        "excel_path": str(excel_path.resolve()),
    }
    Path(args.result_json).parent.mkdir(parents=True, exist_ok=True)
    Path(args.result_json).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
