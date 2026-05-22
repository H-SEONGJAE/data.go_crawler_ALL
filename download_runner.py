# -*- coding: utf-8 -*-
"""Streamlit/CLI용 기관별 파일데이터 최신/과거 다운로드 래퍼."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from crawler_data import main as run_download_crawler
from portal_common import build_url_for_selected_org, clean_text, discover_org_candidates_by_keyword

try:
    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)
except Exception:
    pass


def _resolve_target_url_for_cli(inst_name: str, org_url: str, per_page: int) -> tuple[str, str, list[str]]:
    inst_name = clean_text(inst_name)
    org_url = clean_text(org_url)
    if org_url:
        return inst_name, org_url, []
    rows = discover_org_candidates_by_keyword(inst_name, max_pages=2, per_page=100)
    names = [clean_text(r.get("provider")) for r in rows if clean_text(r.get("provider"))]
    if len(names) == 1:
        return names[0], build_url_for_selected_org(names[0], per_page=per_page), names
    if len(names) > 1:
        raise RuntimeError(
            "제공기관 후보가 여러 개입니다. Streamlit UI에서 후보를 선택하거나 --org-url을 직접 지정하세요.\n"
            + "\n".join(f"- {n}" for n in names[:30])
        )
    return inst_name, build_url_for_selected_org(inst_name, per_page=per_page), []


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
    headless_bool = args.headless.lower() == "true"

    selected_name, selected_url, candidate_names = _resolve_target_url_for_cli(
        args.inst_name, args.org_url, args.per_page
    )

    print("=" * 80, flush=True)
    print("[download_runner]", flush=True)
    print(f"- selected_inst_name: {selected_name}", flush=True)
    print(f"- org_url: {selected_url}", flush=True)
    print(f"- output_dir: {output_dir}", flush=True)
    print("=" * 80, flush=True)
    print("PROGRESS|5|파일 다운로드 수집 시작", flush=True)

    zip_path = run_download_crawler(
        selected_name,
        selected_url,
        headless=headless_bool,
        output_root=output_dir,
        max_pages=args.max_pages,
        per_page=args.per_page,
        auto_shutdown=False,
    )
    result = {
        "status": "completed",
        "inst_name": selected_name,
        "org_url": selected_url,
        "candidate_names": candidate_names,
        "output_dir": str(output_dir),
        "zip_path": str(Path(zip_path).resolve()),
    }
    Path(args.result_json).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print("PROGRESS|100|최신/과거 파일 다운로드 완료", flush=True)
    print(f"[download_runner] ZIP 저장 완료: {zip_path}", flush=True)

    if args.auto_shutdown.lower() == "true":
        sys.exit(0)


if __name__ == "__main__":
    main()
