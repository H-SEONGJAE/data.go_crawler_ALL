# -*- coding: utf-8 -*-
"""Streamlit/CLI용 메타데이터 크롤러 실행 래퍼."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from portal_common import build_file_list_url, clean_filename, clean_text

try:
    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)
except Exception:
    pass


def main():
    parser = argparse.ArgumentParser(description="공공데이터포털 파일데이터 메타데이터 수집")
    parser.add_argument("--scope", choices=["all", "org"], required=True)
    parser.add_argument("--org-name", default="")
    parser.add_argument("--run-mode", choices=["MAIN", "BOTH", "RETRY_FAILED"], default="MAIN")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--result-json", required=True)
    parser.add_argument("--max-pages", type=int, default=0)
    parser.add_argument("--max-items", type=int, default=0)
    parser.add_argument("--list-per-page", type=int, default=1000)
    parser.add_argument("--detail-concurrency", type=int, default=20)
    parser.add_argument("--headless", choices=["true", "false"], default="true")
    parser.add_argument("--both-wait-sec", type=int, default=180)
    args = parser.parse_args()

    import crawler_metadata as cm

    org_name = clean_text(args.org_name)
    if args.scope == "org" and not org_name:
        raise ValueError("기관별 수집은 --org-name이 필요합니다.")

    target_url = build_file_list_url(
        org_name if args.scope == "org" else "",
        current_page=1,
        per_page=args.list_per_page,
    )
    job_name = "공공데이터포털_메타데이터_전체" if args.scope == "all" else f"{org_name}_메타데이터"
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # 원본 수집 엔진은 전역 설정을 읽으므로 실행 직전에만 주입한다.
    cm.RUN_MODE = args.run_mode
    cm.JOB_NAME = job_name
    cm.TARGET_URL = target_url
    cm.OUTPUT_DIR = str(output_dir)
    cm.MAX_PAGES = int(args.max_pages or 0)
    cm.MAX_DETAIL_ITEMS = int(args.max_items or 0)
    cm.LIST_PER_PAGE = int(args.list_per_page or 1000)
    cm.DETAIL_CONCURRENCY = int(args.detail_concurrency or 20)
    cm.HEADLESS = args.headless.lower() == "true"
    cm.RETRY_HEADLESS = cm.HEADLESS
    cm.BOTH_MODE_WAIT_SEC = int(args.both_wait_sec or 0)
    cm.MAKE_ZIP = False

    print("=" * 80, flush=True)
    print("[metadata_runner]", flush=True)
    print(f"- scope: {args.scope}", flush=True)
    print(f"- org_name: {org_name}", flush=True)
    print(f"- target_url: {target_url}", flush=True)
    print(f"- output_dir: {output_dir}", flush=True)
    print(f"- run_mode: {args.run_mode}", flush=True)
    print("=" * 80, flush=True)

    cm.main()

    result = {
        "status": "completed",
        "scope": args.scope,
        "org_name": org_name,
        "target_url": target_url,
        "output_dir": str(output_dir),
        "metadata_path": str(output_dir / "메타데이터.xlsx"),
        "fail_path": str(output_dir / "실패로그.xlsx"),
    }
    Path(args.result_json).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print("[metadata_runner] result.json 저장 완료", flush=True)


if __name__ == "__main__":
    main()
