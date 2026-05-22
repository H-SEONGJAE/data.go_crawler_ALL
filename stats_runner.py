# -*- coding: utf-8 -*-
"""Streamlit/CLI용 조회수/다운로드수 수집 래퍼."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from crawler import collect_file_data_from_url, save_stats_excel
from portal_common import build_url_for_selected_org, clean_text, discover_org_candidates_by_keyword

try:
    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)
except Exception:
    pass


def _resolve_target_url_for_cli(org_name: str, target_url: str, list_per_page: int) -> tuple[str, str, list[str]]:
    org_name = clean_text(org_name)
    target_url = clean_text(target_url)
    if target_url:
        return org_name, target_url, []
    rows = discover_org_candidates_by_keyword(org_name, max_pages=2, per_page=100)
    names = [clean_text(r.get("provider")) for r in rows if clean_text(r.get("provider"))]
    if len(names) == 1:
        return names[0], build_url_for_selected_org(names[0], per_page=list_per_page), names
    if len(names) > 1:
        raise RuntimeError(
            "제공기관 후보가 여러 개입니다. Streamlit UI에서 후보를 선택하거나 --target-url을 직접 지정하세요.\n"
            + "\n".join(f"- {n}" for n in names[:30])
        )
    return org_name, build_url_for_selected_org(org_name, per_page=list_per_page), []


def main():
    parser = argparse.ArgumentParser(description="기관별 파일데이터 조회수/다운로드수 수집")
    parser.add_argument("--org-name", required=True)
    parser.add_argument("--target-url", default="")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--result-json", required=True)
    parser.add_argument("--max-pages", type=int, default=0)
    parser.add_argument("--max-items", type=int, default=0)
    parser.add_argument("--list-per-page", type=int, default=1000)
    parser.add_argument("--headless", choices=["true", "false"], default="true")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    excel_path = output_dir / "공공데이터_FILE_조회수_다운로드.xlsx"

    try:
        org_name, target_url, candidate_names = _resolve_target_url_for_cli(
            args.org_name, args.target_url, args.list_per_page
        )
        print("=" * 80, flush=True)
        print("[stats_runner]", flush=True)
        print(f"- selected_org_name: {org_name}", flush=True)
        print(f"- target_url: {target_url}", flush=True)
        print(f"- output_dir: {output_dir}", flush=True)
        print("=" * 80, flush=True)
        print("PROGRESS|5|목록 수집 시작", flush=True)

        df = collect_file_data_from_url(
            target_url,
            max_pages=args.max_pages,
            max_items=args.max_items,
            list_per_page=args.list_per_page,
            headless=args.headless.lower() == "true",
        )
        print("PROGRESS|90|엑셀 저장 중", flush=True)
        save_stats_excel(df, excel_path)
        result = {
            "status": "completed",
            "org_name": org_name,
            "target_url": target_url,
            "candidate_names": candidate_names,
            "output_dir": str(output_dir),
            "excel_path": str(excel_path),
            "rows": len(df),
        }
        print(f"[stats_runner] 저장 완료: {excel_path}", flush=True)
        print("PROGRESS|100|조회수/다운로드수 수집 완료", flush=True)
    except Exception as e:
        error_path = output_dir / "수집오류.txt"
        error_path.write_text(str(e), encoding="utf-8")
        result = {
            "status": "failed",
            "org_name": clean_text(args.org_name),
            "target_url": clean_text(args.target_url),
            "output_dir": str(output_dir),
            "excel_path": "",
            "rows": 0,
            "error_path": str(error_path),
            "error": str(e),
        }
        print(f"[stats_runner] 수집 실패: {e}", flush=True)

    Path(args.result_json).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
