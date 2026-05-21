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

import pandas as pd
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



def normalize_provider_name(value: str) -> str:
    """제공기관명 비교용 정규화.

    - 공백/개행 정리
    - (주) / ㈜ 표기 차이는 alias에서 처리하므로 여기서는 과도하게 제거하지 않는다.
    """
    return " ".join(str(value or "").replace("\xa0", " ").split()).strip()


def build_provider_aliases(org_name: str):
    """기관명 변경/표기 차이를 고려한 제공기관 alias 생성.

    중요한 기준:
    - 파일데이터명 접두어(예: 강원도 고성군_...)가 아니라 상세 메타데이터의 '제공기관' 값으로 필터링한다.
    - 강원도 → 강원특별자치도처럼 기관 명칭이 변경된 경우도 같은 기관으로 볼 수 있게 alias를 만든다.
    - 회사 표기 (주)/㈜ 차이도 보조로 허용한다.
    """
    base = normalize_provider_name(org_name)
    aliases = {base}

    province_pairs = [
        ("강원특별자치도", "강원도"),
        ("전북특별자치도", "전라북도"),
        ("제주특별자치도", "제주도"),
    ]

    for new_name, old_name in province_pairs:
        current = list(aliases)
        for name in current:
            if name.startswith(new_name + " "):
                aliases.add(old_name + " " + name[len(new_name):].strip())
            if name.startswith(old_name + " "):
                aliases.add(new_name + " " + name[len(old_name):].strip())
            if name == new_name:
                aliases.add(old_name)
            if name == old_name:
                aliases.add(new_name)

    # (주)/㈜ 표기 차이 보정
    current = list(aliases)
    for name in current:
        aliases.add(name.replace("(주)", "㈜"))
        aliases.add(name.replace("㈜", "(주)"))

    return sorted({normalize_provider_name(x) for x in aliases if normalize_provider_name(x)})


def apply_exact_provider_filter(output_dir: Path, org_name: str):
    """기관별 메타데이터 수집 결과를 상세 메타데이터의 '제공기관' 기준으로 후처리 필터링한다.

    왜 필요한가:
    - 공공데이터포털 제공기관 검색 URL은 명칭 변경/별칭 때문에 목록에서 유사 기관 또는 과거 명칭 데이터명이 같이 보일 수 있다.
    - 예: 파일데이터명은 '강원도 고성군_...'이지만 실제 상세 메타데이터 제공기관은
      '강원특별자치도 고성군'인 경우가 있다.
    - 따라서 파일데이터명 접두어나 URL 응답만 믿지 않고, 최종 산출물은 반드시 '제공기관' 값으로 걸러낸다.

    crawler_metadata.py의 수집/파싱 로직은 건드리지 않고, 산출 엑셀만 후처리한다.
    """
    metadata_path = output_dir / "메타데이터.xlsx"
    if not metadata_path.exists():
        print(f"[기관 필터] 메타데이터 파일이 없어 필터를 생략합니다: {metadata_path}", flush=True)
        return {"applied": False, "reason": "metadata_not_found"}

    df = pd.read_excel(metadata_path)
    before_rows = len(df)

    if df.empty:
        print("[기관 필터] 메타데이터가 비어 있어 필터를 생략합니다.", flush=True)
        return {"applied": False, "reason": "empty_metadata", "before_rows": before_rows, "after_rows": 0}

    if "제공기관" not in df.columns:
        print("[기관 필터] '제공기관' 컬럼이 없어 필터를 생략합니다.", flush=True)
        return {"applied": False, "reason": "provider_column_missing", "before_rows": before_rows}

    aliases = build_provider_aliases(org_name)
    alias_set = set(aliases)

    provider_norm = df["제공기관"].fillna("").map(normalize_provider_name)
    filtered = df[provider_norm.isin(alias_set)].copy()
    after_rows = len(filtered)

    print("[기관 필터] 상세 메타데이터 '제공기관' 기준 필터 적용", flush=True)
    print(f"- 입력 기관명: {org_name}", flush=True)
    print(f"- 허용 기관명: {aliases}", flush=True)
    print(f"- 필터 전 rows: {before_rows}", flush=True)
    print(f"- 필터 후 rows: {after_rows}", flush=True)

    # 필터 결과가 있으면 최종 메타데이터.xlsx를 필터링된 결과로 덮어쓴다.
    # 0건이면 실제 해당 제공기관 데이터가 없거나 제공기관 추출이 비정상일 수 있으므로,
    # 원본을 보존하지 않고 0건 결과를 저장해 '다른 기관 데이터가 섞이는 문제'를 우선 차단한다.
    try:
        cm.write_excel_no_url_warning(filtered, metadata_path, sheet_name="메타데이터")
    except Exception:
        # 원본 엔진 함수 사용 실패 시 pandas 기본 저장으로 fallback
        with pd.ExcelWriter(metadata_path, engine="xlsxwriter", engine_kwargs={"options": {"strings_to_urls": False}}) as writer:
            filtered.to_excel(writer, index=False, sheet_name="메타데이터")

    return {
        "applied": True,
        "before_rows": before_rows,
        "after_rows": after_rows,
        "aliases": aliases,
        "metadata_path": str(metadata_path),
    }


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
        filter_info = None
        if args.scope == "org":
            filter_info = apply_exact_provider_filter(output_dir, args.org_name)
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
            "provider_filter": locals().get("filter_info"),
        }
        Path(args.result_json).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
