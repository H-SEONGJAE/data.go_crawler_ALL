# -*- coding: utf-8 -*-
"""
Streamlit wrapper runner for 기관별 조회수/다운로드 수 수집.

v5 URL 최적화
- metadata_runner와 같은 build_collection_target() 공통 로직을 사용한다.
- 정확 기관 필터 URL이 0건이면 keyword/orgSearch fallback을 허용하되,
  목록명 prefix가 입력 기관과 맞는 카드만 수집한다.
- 결과 컬럼은 기존과 동일하게 유지한다: 데이터명 / 조회수 / 다운로드수
"""

import argparse
import json
import sys
import time
from pathlib import Path

import pandas as pd
import requests

from org_url_resolver import (
    build_collection_target,
    title_prefix_matches_input,
)

from crawler_metadata import (
    build_http_headers,
    collect_dataset_links_from_html,
    clean_dataset_title,
    optimize_list_url,
)

try:
    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)
except Exception:
    pass

LIST_PER_PAGE = 1000
REQUEST_TIMEOUT = 25
PAGE_SLEEP_SEC = 0.15
MAX_EMPTY_PAGES = 2
MAX_PAGES_GUARD = 300


def to_int_or_blank(value):
    s = str(value or "").strip().replace(",", "")
    if not s:
        return ""
    try:
        return int(s)
    except Exception:
        return s


def item_matches_prefix(item: dict, title_prefix_filter: str) -> bool:
    if not title_prefix_filter:
        return True
    title = str(item.get("title") or item.get("raw_title") or "")
    try:
        return bool(title_prefix_matches_input(title, title_prefix_filter))
    except Exception:
        return False


def collect_stats_by_metadata_list_parser(target_url: str, session: requests.Session, title_prefix_filter: str = "") -> list[dict]:
    """
    crawler_metadata.py의 목록 HTML 파서로 조회수/다운로드 수를 수집한다.
    currentPage 파라미터를 직접 증가시켜 페이지 누락을 방지한다.
    title_prefix_filter가 있으면 keyword/orgSearch fallback 결과에서 타기관 데이터 혼입을 차단한다.
    """
    rows = []
    seen_urls = set()
    empty_pages = 0

    for page_no in range(1, MAX_PAGES_GUARD + 1):
        list_url = optimize_list_url(target_url, per_page=LIST_PER_PAGE, current_page=page_no)
        print(f"[LIST] page {page_no:03d} 요청", flush=True)

        resp = session.get(list_url, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()

        items = collect_dataset_links_from_html(resp.text, list_url)
        if title_prefix_filter:
            items = [item for item in items if item_matches_prefix(item, title_prefix_filter)]

        print(f"[LIST] page {page_no:03d} +{len(items):,}건", flush=True)

        if not items:
            empty_pages += 1
            if empty_pages >= MAX_EMPTY_PAGES:
                break
            continue

        empty_pages = 0
        new_count = 0
        for item in items:
            detail_url = (item.get("detail_url") or "").strip()
            if not detail_url or detail_url in seen_urls:
                continue
            seen_urls.add(detail_url)

            title = clean_dataset_title(item.get("title") or item.get("raw_title") or "")
            if not title:
                title = detail_url

            rows.append({
                "데이터명": title,
                "조회수": to_int_or_blank(item.get("조회수", "")),
                "다운로드수": to_int_or_blank(item.get("다운로드수", "") or item.get("다운로드(바로가기)", "")),
            })
            new_count += 1

        print(f"[LIST] page {page_no:03d} 신규 {new_count:,}건 | 누적 {len(rows):,}건", flush=True)

        if new_count == 0:
            break

        time.sleep(PAGE_SLEEP_SEC)

    return rows


def resolve_stats_target(org_input: str, target_url: str = "") -> dict:
    result = build_collection_target(
        org_input=org_input,
        target_url=target_url,
        headers=build_http_headers(),
        timeout=5,
        per_page=LIST_PER_PAGE,
        allow_keyword_fallback=True,
    )
    if not result.get("found") or not result.get("target_url"):
        raise RuntimeError(
            "조회수/다운로드 수 수집 URL을 확정하지 못했습니다. "
            f"input={org_input}, debug={json.dumps(result.get('debug', {}), ensure_ascii=False)[:2000]}"
        )
    return result


def main():
    parser = argparse.ArgumentParser(description="기관별 조회수/다운로드 수 수집 wrapper")
    parser.add_argument("--org-name", required=True)
    parser.add_argument("--target-url", default="", help="UI에서 이미 확정한 기관별 파일데이터 URL")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--result-json", required=True)
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    org_input = args.org_name.strip()
    resolved = resolve_stats_target(org_input, args.target_url.strip())
    exact_org = resolved.get("exact_org") or org_input
    target_urls = resolved.get("target_urls") or [resolved.get("target_url")]
    title_prefix_filter = resolved.get("title_prefix_filter") or ""

    print("=" * 80, flush=True)
    print("[Streamlit wrapper - stats_runner]", flush=True)
    print(f"- org_name: {org_input}", flush=True)
    print(f"- resolved_org_name: {exact_org}", flush=True)
    print(f"- resolve_mode: {resolved.get('mode')}", flush=True)
    print(f"- title_prefix_filter: {title_prefix_filter}", flush=True)
    print(f"- target_url_count: {len(target_urls)}", flush=True)
    print("※ crawler_metadata.py의 목록 파서로 조회수/다운로드 수를 수집합니다.", flush=True)
    print("※ 결과 컬럼은 기존과 동일하게 데이터명 / 조회수 / 다운로드수로 저장합니다.", flush=True)
    print("=" * 80, flush=True)

    session = requests.Session()
    session.headers.update(build_http_headers())

    best = {
        "org": exact_org,
        "url": "",
        "rows": [],
        "error": None,
    }

    for target_url in target_urls:
        if not target_url:
            continue
        print("\n" + "-" * 80, flush=True)
        print(f"[URL 후보] {target_url}", flush=True)
        try:
            rows = collect_stats_by_metadata_list_parser(target_url, session, title_prefix_filter=title_prefix_filter)
            print(f"[후보 결과] {exact_org} / {len(rows):,}건", flush=True)
            if len(rows) > len(best["rows"]):
                best = {"org": exact_org, "url": target_url, "rows": rows, "error": None}
            if rows:
                break
        except Exception as e:
            print(f"[경고] 후보 수집 실패: {repr(e)}", flush=True)
            best["error"] = repr(e)
            continue

    if not best["rows"]:
        raise RuntimeError(f"모든 기관/URL 후보에서 수집 결과가 0건입니다. 마지막 오류: {best.get('error')}")

    df = pd.DataFrame(best["rows"], columns=["데이터명", "조회수", "다운로드수"])

    timestamp = time.strftime("%Y%m%d_%H%M%S")
    safe_org_name = best["org"].replace("(", "_").replace(")", "")
    excel_path = output_dir / f"공공데이터_{safe_org_name}_조회수_다운로드수_{timestamp}.xlsx"

    with pd.ExcelWriter(excel_path, engine="xlsxwriter", engine_kwargs={"options": {"strings_to_urls": False}}) as writer:
        df.to_excel(writer, index=False, sheet_name="FILE_집계")

    result = {
        "status": "completed",
        "org_name": best["org"],
        "target_url": best["url"],
        "title_prefix_filter": title_prefix_filter,
        "resolve_mode": resolved.get("mode"),
        "row_count": int(len(df)),
        "output_dir": str(output_dir),
        "excel_path": str(excel_path),
        "resolve_debug": resolved.get("debug", {}),
    }
    Path(args.result_json).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n" + "=" * 80, flush=True)
    print("[저장 완료]", flush=True)
    print(f"- 사용 기관명: {best['org']}", flush=True)
    print(f"- 수집 건수: {len(df):,}", flush=True)
    print(f"- 저장 파일: {excel_path}", flush=True)
    print("=" * 80, flush=True)


if __name__ == "__main__":
    main()
