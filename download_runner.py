# -*- coding: utf-8 -*-
"""Streamlit/CLI용 기관별 파일데이터 최신/과거 다운로드 래퍼."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from crawler_data import main as run_download_crawler
from portal_common import build_file_list_url, clean_text

try:
    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)
except Exception:
    pass


def main():
    parser = argparse.ArgumentParser(description="기관별 파일데이터 최신/과거 다운로드")
    parser.add_argument("--inst-name", required=True)
    parser.add_argument("--org-url", default="")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--result-json", required=True)
    parser.add_argument("--headless", choices=["true", "false"], default="true")
    parser.add_argument("--max-pages", type=int, default=0)
    parser.add_argument("--per-page", type=int, default=100)
    parser.add_argument("--auto-shutdown", choices=["true", "false"], default="false")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    inst_name = clean_text(args.inst_name)
    org_url = clean_text(args.org_url) or build_file_list_url(inst_name, current_page=1, per_page=args.per_page)

    print("=" * 80, flush=True)
    print("[download_runner]", flush=True)
    print(f"- inst_name: {inst_name}", flush=True)
    print(f"- org_url: {org_url}", flush=True)
    print(f"- output_dir: {output_dir}", flush=True)
    print("=" * 80, flush=True)

    zip_path = run_download_crawler(
        inst_name,
        org_url,
        headless=args.headless.lower() == "true",
        output_root=output_dir,
        max_pages=args.max_pages,
        per_page=args.per_page,
        auto_shutdown=False,
    )
    result = {
        "status": "completed",
        "inst_name": inst_name,
        "org_url": org_url,
        "output_dir": str(output_dir),
        "zip_path": str(Path(zip_path).resolve()),
    }
    Path(args.result_json).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[download_runner] ZIP 저장 완료: {zip_path}", flush=True)

    if args.auto_shutdown.lower() == "true":
        sys.exit(0)


if __name__ == "__main__":
    main()
