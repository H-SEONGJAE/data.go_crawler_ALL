# -*- coding: utf-8 -*-
"""
Streamlit wrapper runner for crawler_metadata.py.

기관별 메타데이터 수집 원칙
- crawler_metadata.py의 목록 수집/상세 수집/파싱/저장 로직은 건드리지 않는다.
- 기관별 수집은 Streamlit에서 전달받은 target_url 하나를 crawler_metadata.py 원본 엔진에 그대로 넣어 실행한다.
- 후보 URL/키워드 URL/후처리 제공기관 필터링을 하지 않는다. 이 과정에서 0건/누락/혼선이 발생했기 때문이다.
"""
import argparse
import json
import sys
import urllib.parse
from pathlib import Path
from datetime import datetime

import crawler_metadata as cm

try:
    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)
except Exception:
    pass


def build_provider_target_url(org_name: str, *, current_page: int = 1, per_page: int = 1000) -> str:
    """
    공공데이터포털 제공기관별 파일데이터 목록 URL 생성.
    화면에서 사용자가 직접 URL을 수정/붙여넣을 수 있으므로, 여기서는 기본 URL만 생성한다.
    """
    org = (org_name or "").strip()
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
        "currentPage": str(current_page),
        "perPage": str(per_page),
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


def run_cm_once(*, target_url: str, job_name: str, output_dir: Path, run_mode: str, max_pages: int, max_items: int, list_per_page: int, concurrency: int | None):
    output_dir.mkdir(parents=True, exist_ok=True)

    cm.RUN_MODE = run_mode
    cm.JOB_NAME = job_name
    cm.TARGET_URL = target_url
    cm.OUTPUT_DIR = str(output_dir)
    cm.MAX_PAGES = int(max_pages)
    cm.MAX_DETAIL_ITEMS = int(max_items)
    cm.LIST_PER_PAGE = int(list_per_page)
    if concurrency is not None:
        cm.DETAIL_CONCURRENCY = int(concurrency)

    cm.RETRY_FAIL_LOG_PATH = None
    cm.RETRY_EXISTING_METADATA_PATH = None
    cm.RETRY_EXISTING_COLUMNS_PATH = None

    print("\n" + "=" * 80, flush=True)
    print("[metadata_runner] crawler_metadata.py 원본 엔진 실행", flush=True)
    print(f"- job_name: {job_name}", flush=True)
    print(f"- target_url: {target_url}", flush=True)
    print(f"- output_dir: {output_dir}", flush=True)
    print(f"- run_mode: {run_mode}", flush=True)
    print(f"- max_pages: {max_pages}", flush=True)
    print(f"- max_items: {max_items}", flush=True)
    print(f"- list_per_page: {list_per_page}", flush=True)
    print("=" * 80, flush=True)

    cm.main()


def main():
    parser = argparse.ArgumentParser(description="공공데이터포털 메타데이터 크롤러 wrapper")
    parser.add_argument("--scope", choices=["all", "org"], required=True)
    parser.add_argument("--org-name", default="")
    parser.add_argument("--target-url", default="", help="기관별 수집 시 Streamlit 화면에서 생성/입력한 목록 URL")
    parser.add_argument("--run-mode", choices=["MAIN", "BOTH", "RETRY_FAILED"], default="MAIN")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--result-json", required=True)
    parser.add_argument("--max-pages", type=int, default=None)
    parser.add_argument("--max-items", type=int, default=None)
    parser.add_argument("--list-per-page", type=int, default=1000)
    parser.add_argument("--concurrency", type=int, default=None)
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    started_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    status = "unknown"

    print("=" * 80, flush=True)
    print("[Streamlit wrapper - metadata_runner]", flush=True)
    print(f"- scope: {args.scope}", flush=True)
    print(f"- org_name: {args.org_name}", flush=True)
    print(f"- target_url: {args.target_url}", flush=True)
    print(f"- run_mode: {args.run_mode}", flush=True)
    print(f"- output_dir: {output_dir}", flush=True)
    print(f"- max_pages: {args.max_pages}", flush=True)
    print(f"- max_items: {args.max_items}", flush=True)
    print(f"- list_per_page: {args.list_per_page}", flush=True)
    print("※ 기관별 수집은 전달받은 URL 1개만 crawler_metadata.py 원본 엔진에 넣어 실행합니다.", flush=True)
    print("=" * 80, flush=True)

    try:
        if args.scope == "all":
            target_url = cm.TARGET_URL
            job_name = "공공데이터포털_전체_메타데이터"
            max_pages = cm.MAX_PAGES if args.max_pages is None else args.max_pages
            max_items = cm.MAX_DETAIL_ITEMS if args.max_items is None else args.max_items
        else:
            if not args.org_name.strip():
                raise ValueError("기관별 수집에는 --org-name이 필요합니다.")
            target_url = args.target_url.strip() or build_provider_target_url(args.org_name, per_page=args.list_per_page)
            job_name = f"공공데이터_{args.org_name.strip()}_메타데이터"
            max_pages = 0 if args.max_pages is None else args.max_pages
            max_items = 0 if args.max_items is None else args.max_items

        run_cm_once(
            target_url=target_url,
            job_name=job_name,
            output_dir=output_dir,
            run_mode=args.run_mode,
            max_pages=max_pages,
            max_items=max_items,
            list_per_page=args.list_per_page,
            concurrency=args.concurrency,
        )
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
            "org_name": args.org_name,
            "target_url": args.target_url,
            "started_at": started_at,
            "finished_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "output_dir": str(output_dir),
            "metadata_path": str(output_dir / "메타데이터.xlsx"),
            "fail_path": str(output_dir / "실패로그.xlsx"),
        }
        Path(args.result_json).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
