# -*- coding: utf-8 -*-
"""
Streamlit wrapper runner for crawler_metadata.py.

v5 URL 최적화 원칙
- UI 검색 결과/URL을 그대로 믿지 않고 수집 시작 시점에 다시 검증한다.
- 정확 기관 필터 URL이 0건이면 keyword/orgSearch fallback을 허용하되,
  crawler_metadata.py에 LIST_TITLE_PREFIX_FILTER를 주입하여 목록명 prefix가 입력 기관과 맞는 URL만 수집한다.
- crawler_metadata.py의 상세 파싱/저장 로직은 그대로 사용한다.
"""
import argparse
import json
import sys
from pathlib import Path
from datetime import datetime

import crawler_metadata as cm

from org_url_resolver import build_collection_target

try:
    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)
except Exception:
    pass


def resolve_org_target(org_name: str, target_url: str = "") -> dict:
    result = build_collection_target(
        org_input=org_name,
        target_url=target_url,
        headers=cm.build_http_headers(),
        timeout=5,
        per_page=1000,
        allow_keyword_fallback=True,
    )
    if not result.get("found") or not result.get("target_url"):
        raise RuntimeError(
            "기관별 메타데이터 수집 URL을 확정하지 못했습니다. "
            "정확 기관 필터 URL이 0건이고, 목록명 prefix 기준 fallback도 실패했습니다. "
            f"input={org_name}, debug={json.dumps(result.get('debug', {}), ensure_ascii=False)[:2000]}"
        )
    return result


def main():
    parser = argparse.ArgumentParser(description="공공데이터포털 메타데이터 크롤러 wrapper")
    parser.add_argument("--scope", choices=["all", "org"], required=True)
    parser.add_argument("--org-name", default="")
    parser.add_argument("--target-url", default="", help="UI에서 이미 확정한 기관별 파일데이터 URL")
    parser.add_argument("--run-mode", choices=["MAIN", "BOTH", "RETRY_FAILED"], default="MAIN")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--result-json", required=True)
    parser.add_argument("--max-pages", type=int, default=None)
    parser.add_argument("--max-items", type=int, default=None)
    parser.add_argument("--list-per-page", type=int, default=None)
    parser.add_argument("--concurrency", type=int, default=None)
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    resolved_debug = {}
    title_prefix_filter = ""

    if args.scope == "org":
        if not args.org_name.strip():
            raise ValueError("기관별 수집에는 --org-name이 필요합니다.")
        resolved = resolve_org_target(args.org_name.strip(), args.target_url.strip())
        exact_org = resolved.get("exact_org") or args.org_name.strip()
        target_url = resolved["target_url"]
        title_prefix_filter = resolved.get("title_prefix_filter") or ""
        resolved_debug = resolved.get("debug", {})
        job_name = f"공공데이터_{exact_org}_메타데이터"
        max_pages = 0 if args.max_pages is None else args.max_pages
        max_items = 0 if args.max_items is None else args.max_items
    else:
        target_url = cm.TARGET_URL
        job_name = "공공데이터포털_전체_메타데이터"
        max_pages = cm.MAX_PAGES if args.max_pages is None else args.max_pages
        max_items = cm.MAX_DETAIL_ITEMS if args.max_items is None else args.max_items

    print("=" * 80, flush=True)
    print("[Streamlit wrapper - metadata_runner]", flush=True)
    print(f"- scope: {args.scope}", flush=True)
    print(f"- org_name: {args.org_name}", flush=True)
    if args.scope == "org":
        print(f"- resolved_org_name: {job_name.replace('공공데이터_', '').replace('_메타데이터', '')}", flush=True)
        print(f"- title_prefix_filter: {title_prefix_filter}", flush=True)
        print(f"- resolve_mode: {resolved.get('mode') if 'resolved' in locals() else ''}", flush=True)
    print(f"- target_url: {target_url}", flush=True)
    print(f"- run_mode: {args.run_mode}", flush=True)
    print(f"- output_dir: {output_dir}", flush=True)
    print(f"- max_pages: {max_pages}", flush=True)
    print(f"- max_items: {max_items}", flush=True)
    print("※ crawler_metadata.py 수집 엔진을 실행합니다.", flush=True)
    print("=" * 80, flush=True)

    cm.RUN_MODE = args.run_mode
    cm.JOB_NAME = job_name
    cm.TARGET_URL = target_url
    cm.OUTPUT_DIR = str(output_dir)
    cm.MAX_PAGES = int(max_pages)
    cm.MAX_DETAIL_ITEMS = int(max_items)
    cm.LIST_TITLE_PREFIX_FILTER = title_prefix_filter
    if args.list_per_page is not None:
        cm.LIST_PER_PAGE = int(args.list_per_page)
    if args.concurrency is not None:
        cm.DETAIL_CONCURRENCY = int(args.concurrency)

    # 같은 프로세스 안에서 경로가 이전 실행값으로 남지 않도록 초기화한다.
    cm.RETRY_FAIL_LOG_PATH = None
    cm.RETRY_EXISTING_METADATA_PATH = None
    cm.RETRY_EXISTING_COLUMNS_PATH = None

    started_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    status = "unknown"
    try:
        print("[metadata_runner] crawler_metadata.py 실행 진입", flush=True)
        cm.main()
        status = "completed"
    except KeyboardInterrupt:
        status = "stopped"
        print("\n[중지] 사용자 요청으로 중지되었습니다.", flush=True)
    except Exception as e:
        status = "error"
        print(f"\n[오류] metadata_runner 실행 실패: {repr(e)}", flush=True)
        raise
    finally:
        result = {
            "status": status,
            "scope": args.scope,
            "org_name": job_name.replace("공공데이터_", "").replace("_메타데이터", "") if args.scope == "org" else args.org_name,
            "target_url": target_url,
            "title_prefix_filter": title_prefix_filter,
            "resolve_debug": resolved_debug,
            "started_at": started_at,
            "finished_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "output_dir": str(output_dir),
            "metadata_path": str(output_dir / "메타데이터.xlsx"),
            "fail_path": str(output_dir / "실패로그.xlsx"),
        }
        Path(args.result_json).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
