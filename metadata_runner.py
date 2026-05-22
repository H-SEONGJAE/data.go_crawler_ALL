# -*- coding: utf-8 -*-
"""Streamlit/CLI용 메타데이터 크롤러 실행 래퍼."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from portal_common import (
    build_file_list_url,
    build_url_for_selected_org,
    clean_text,
    discover_org_candidates_by_keyword,
)

try:
    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)
except Exception:
    pass


def _resolve_target_url_for_cli(org_name: str, target_url: str, list_per_page: int) -> tuple[str, str, list[str]]:
    """CLI 단독 실행용 URL 확정.

    Streamlit에서는 사용자가 선택한 제공기관 URL을 --target-url로 넘기므로 이 함수가 거의 사용되지 않는다.
    CLI에서 target_url 없이 실행한 경우:
    - 후보가 1개면 자동 사용
    - 후보가 여러 개면 1순위로 진행하지 않고 에러로 후보 목록을 안내한다.
    """
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
    parser = argparse.ArgumentParser(description="공공데이터포털 파일데이터 메타데이터 수집")
    parser.add_argument("--scope", choices=["all", "org"], required=True)
    parser.add_argument("--org-name", default="")
    parser.add_argument("--target-url", default="")
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

    headless_bool = args.headless.lower() == "true"
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    if args.scope == "org":
        if not clean_text(args.org_name):
            raise ValueError("기관별 수집은 --org-name이 필요합니다.")
        org_name, target_url, candidate_names = _resolve_target_url_for_cli(
            args.org_name,
            args.target_url,
            args.list_per_page,
        )
    else:
        org_name = ""
        candidate_names = []
        target_url = clean_text(args.target_url) or build_file_list_url(
            "", current_page=1, per_page=args.list_per_page, d_type="FILE"
        )

    job_name = "공공데이터포털_메타데이터_전체" if args.scope == "all" else f"{org_name}_메타데이터"

    cm.RUN_MODE = args.run_mode
    cm.JOB_NAME = job_name
    cm.TARGET_URL = target_url
    cm.OUTPUT_DIR = str(output_dir)
    cm.MAX_PAGES = int(args.max_pages or 0)
    cm.MAX_DETAIL_ITEMS = int(args.max_items or 0)
    cm.LIST_PER_PAGE = int(args.list_per_page or 1000)
    cm.DETAIL_CONCURRENCY = int(args.detail_concurrency or 20)
    cm.HEADLESS = headless_bool
    cm.RETRY_HEADLESS = cm.HEADLESS
    cm.BOTH_MODE_WAIT_SEC = int(args.both_wait_sec or 0)
    cm.MAKE_ZIP = False

    print("=" * 80, flush=True)
    print("[metadata_runner]", flush=True)
    print(f"- scope: {args.scope}", flush=True)
    print(f"- selected_org_name: {org_name}", flush=True)
    print(f"- target_url: {target_url}", flush=True)
    print(f"- output_dir: {output_dir}", flush=True)
    print(f"- run_mode: {args.run_mode}", flush=True)
    print("=" * 80, flush=True)
    print("PROGRESS|1|설정 확인 완료", flush=True)

    cm.main()

    result = {
        "status": "completed",
        "scope": args.scope,
        "org_name": org_name,
        "target_url": target_url,
        "candidate_names": candidate_names,
        "output_dir": str(output_dir),
        "metadata_path": str(output_dir / "메타데이터.xlsx"),
        "fail_path": str(output_dir / "실패로그.xlsx"),
    }
    Path(args.result_json).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print("PROGRESS|100|메타데이터 수집 완료", flush=True)
    print("[metadata_runner] result.json 저장 완료", flush=True)


if __name__ == "__main__":
    main()
