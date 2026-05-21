# -*- coding: utf-8 -*-
"""
Streamlit wrapper runner for crawler_metadata.py.

중요 원칙:
- crawler_metadata.py 내부 수집/파싱/재시도 로직은 수정하지 않는다.
- 기관별 수집은 TARGET_URL만 기관 조건으로 바꾼 뒤 원본 엔진을 그대로 실행한다.
"""
import argparse
import json
import os
import sys
import urllib.parse
from pathlib import Path
from datetime import datetime

import crawler_metadata as cm


# Keep redirected stdout/stderr line-buffered for the Streamlit live log panel.
try:
    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)
except Exception:
    pass



def build_org_target_url(org_name: str) -> str:
    """공공데이터포털 기관별 파일데이터 목록 URL 생성.

    포털 UI에서 복사되는 기관별 URL은 org 하나만 쓰지 않고
    orgFullName/orgFilter/org 세 파라미터를 함께 채웁니다.
    기관별 수집 실패를 줄이기 위해 동일 형식으로 생성합니다.
    """
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
    parser = argparse.ArgumentParser(description="공공데이터포털 메타데이터 크롤러 실행 wrapper")
    parser.add_argument("--scope", choices=["all", "org"], required=True)
    parser.add_argument("--org-name", default="")
    parser.add_argument("--run-mode", choices=["MAIN", "BOTH", "RETRY_FAILED"], default="BOTH")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--result-json", required=True)
    parser.add_argument("--max-pages", type=int, default=None)
    parser.add_argument("--max-items", type=int, default=None)
    parser.add_argument("--list-per-page", type=int, default=None)
    parser.add_argument("--concurrency", type=int, default=None)
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    if args.scope == "org":
        if not args.org_name.strip():
            raise ValueError("기관별 수집에는 --org-name이 필요합니다.")
        target_url = build_org_target_url(args.org_name)
        job_name = f"공공데이터_{args.org_name.strip()}_메타데이터"
        # 기관별은 전체 수집과 달리 마지막 빈 페이지까지 원본 목록 수집 루프가 돌도록 0을 기본값으로 둔다.
        max_pages = 0 if args.max_pages is None else args.max_pages
        max_items = 0 if args.max_items is None else args.max_items
    else:
        target_url = cm.TARGET_URL
        job_name = "공공데이터포털_전체_메타데이터"
        # 전체 수집은 Streamlit UI에서 기본 100/100000으로 넘기되, 값이 없으면 원본 기본값 유지.
        max_pages = cm.MAX_PAGES if args.max_pages is None else args.max_pages
        max_items = cm.MAX_DETAIL_ITEMS if args.max_items is None else args.max_items

    print("=" * 80, flush=True)
    print("[Streamlit wrapper - metadata_runner]", flush=True)
    print(f"- scope: {args.scope}", flush=True)
    print(f"- org_name: {args.org_name}", flush=True)
    print(f"- target_url: {target_url}", flush=True)
    print(f"- run_mode: {args.run_mode}", flush=True)
    print(f"- output_dir: {output_dir}", flush=True)
    print(f"- max_pages: {max_pages}", flush=True)
    print(f"- max_items: {max_items}", flush=True)
    print("※ crawler_metadata.py 원본 엔진을 그대로 실행합니다.", flush=True)
    print("=" * 80, flush=True)

    # 원본 엔진의 전역 설정만 실행 직전에 주입한다. 내부 함수/파싱 로직은 변경하지 않는다.
    cm.RUN_MODE = args.run_mode
    cm.JOB_NAME = job_name
    cm.TARGET_URL = target_url
    cm.OUTPUT_DIR = str(output_dir)
    cm.MAX_PAGES = int(max_pages)
    cm.MAX_DETAIL_ITEMS = int(max_items)
    if args.list_per_page is not None:
        cm.LIST_PER_PAGE = int(args.list_per_page)
    if args.concurrency is not None:
        cm.DETAIL_CONCURRENCY = int(args.concurrency)

    # 재수집 경로가 이전 실행 결과로 엇갈리지 않게 현재 output_dir 기준으로 고정한다.
    cm.RETRY_FAIL_LOG_PATH = None
    cm.RETRY_EXISTING_METADATA_PATH = None
    cm.RETRY_EXISTING_COLUMNS_PATH = None

    started_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
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
            "status": locals().get("status", "unknown"),
            "scope": args.scope,
            "org_name": args.org_name,
            "started_at": started_at,
            "finished_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "output_dir": str(output_dir),
            "metadata_path": str(output_dir / "메타데이터.xlsx"),
            "fail_path": str(output_dir / "실패로그.xlsx"),
        }
        Path(args.result_json).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
