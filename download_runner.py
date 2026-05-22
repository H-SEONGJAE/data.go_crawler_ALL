# -*- coding: utf-8 -*-
"""
Streamlit wrapper runner for crawler_data.py.

중요 원칙:
- crawler_data.py의 파일데이터/과거데이터 다운로드 루프는 유지한다.
- URL 확정 방식만 org_url_resolver.py의 공통 제공기관명 해석 로직으로 변경한다.
- 실행 위치만 output_dir로 바꿔 결과 ZIP을 찾기 쉽게 한다.
"""
import argparse
import json
import os
import sys
import urllib.parse
from pathlib import Path

from crawler_data import main as run_download_crawler
from org_url_resolver import (
    build_org_filter_url,
    resolve_org_name_and_url_fast,
    url_has_dataset_items,
)


# Keep redirected stdout/stderr line-buffered for the Streamlit live log panel.
try:
    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)
except Exception:
    pass


def is_exact_org_filter_url(url: str) -> bool:
    """keyword 검색 URL이 아니라 org/orgFullName/orgFilter가 들어간 기관 필터 URL인지 확인한다."""
    try:
        parsed = urllib.parse.urlparse(url or "")
        q = urllib.parse.parse_qs(parsed.query)
        org_values = []
        for key in ["orgFullName", "orgFilter", "org"]:
            org_values.extend([v.strip() for v in q.get(key, []) if v and v.strip()])
        keyword_values = [v.strip() for v in q.get("keyword", []) if v and v.strip()]
        return bool(org_values) and not keyword_values
    except Exception:
        return False


def resolve_download_target(inst_name: str, org_url: str = "") -> tuple[str, str, dict]:
    """
    기관별 파일데이터 다운로드용 기관명/URL을 확정한다.

    원칙:
    - 사용자가 직접 URL을 넘긴 경우에도 1페이지 목록이 있는 URL인지 먼저 확인한다.
    - URL이 없거나 0건이면, 메타데이터/조회수 파트와 동일한 방식으로 실제 제공기관명을 찾는다.
    - 최종 다운로드 URL에는 포털 목록에서 확인된 실제 제공기관명 원문을 넣는다.
    - 정확한 기관명을 끝까지 못 찾은 경우 keyword 검색 결과를 다운로드하지 않고 중단한다.
      잘못된 기관 데이터까지 대량 다운로드하는 것을 막기 위함이다.
    """
    inst = (inst_name or "").strip()
    given_url = (org_url or "").strip()

    debug = {
        "input_inst_name": inst,
        "input_org_url": given_url,
        "used_input_url": False,
        "resolve_result": None,
    }

    if given_url and is_exact_org_filter_url(given_url) and url_has_dataset_items(given_url, timeout=5):
        debug["used_input_url"] = True
        return inst, given_url, debug

    if given_url and not is_exact_org_filter_url(given_url):
        debug["ignored_input_url_reason"] = "keyword_or_non_org_filter_url"

    result = resolve_org_name_and_url_fast(
        inst,
        timeout=3,
        per_page=10,
        max_workers=4,
    )
    debug["resolve_result"] = result

    if result.get("found"):
        exact_org = (result.get("exact_org") or inst).strip()
        target_url = build_org_filter_url(exact_org, current_page=1, per_page=10)
        if not url_has_dataset_items(target_url, timeout=5):
            raise RuntimeError(
                "기관명은 해석했지만 최종 기관별 파일데이터 URL에서 목록을 찾지 못했습니다. "
                f"exact_org={exact_org}, url={target_url}"
            )
        return exact_org, target_url, debug

    # 정확한 기관명을 못 찾은 상태에서 keyword 검색 URL로 다운로드하면 타기관 데이터가 섞일 수 있다.
    # 단, 사용자가 정확한 기관명을 이미 입력한 경우를 위해 org filter URL만 마지막으로 검증한다.
    direct_url = build_org_filter_url(inst, current_page=1, per_page=10)
    if inst and url_has_dataset_items(direct_url, timeout=5):
        debug["used_direct_org_filter"] = True
        return inst, direct_url, debug

    raise RuntimeError(
        "기관별 파일데이터 다운로드 URL을 확정하지 못했습니다. "
        "제공기관명을 조금 더 정확히 입력하거나, 검색 결과의 제공기관 후보를 선택한 뒤 다시 실행하세요. "
        f"input={inst}"
    )


def main():
    parser = argparse.ArgumentParser(description="기관별 파일데이터 다운로드 wrapper")
    parser.add_argument("--inst-name", required=True)
    parser.add_argument("--org-url", default="", help="선택 입력. 비워두면 제공기관명으로 자동 확정합니다.")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--result-json", required=True)
    parser.add_argument("--headless", choices=["true", "false"], default="true")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    resolved_inst_name, resolved_org_url, resolve_debug = resolve_download_target(
        args.inst_name.strip(),
        args.org_url.strip(),
    )

    print("=" * 80, flush=True)
    print("[Streamlit wrapper - download_runner]", flush=True)
    print(f"- input inst_name: {args.inst_name}", flush=True)
    print(f"- resolved inst_name: {resolved_inst_name}", flush=True)
    print(f"- resolved org_url: {resolved_org_url}", flush=True)
    print(f"- output_dir: {output_dir}", flush=True)
    print("※ crawler_data.py의 다운로드 로직은 그대로 실행합니다.", flush=True)
    print("※ URL 확정만 공통 기관명 해석 로직을 사용합니다.", flush=True)
    print("=" * 80, flush=True)

    old_cwd = os.getcwd()
    try:
        os.chdir(output_dir)
        zip_path = run_download_crawler(
            resolved_inst_name,
            resolved_org_url,
            headless=(args.headless.lower() == "true"),
        )
        zip_path = str(Path(zip_path).resolve())
    finally:
        os.chdir(old_cwd)

    result = {
        "status": "completed",
        "input_inst_name": args.inst_name.strip(),
        "inst_name": resolved_inst_name,
        "org_url": resolved_org_url,
        "output_dir": str(output_dir),
        "zip_path": zip_path,
        "resolve_debug": resolve_debug,
    }
    Path(args.result_json).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[ZIP 저장 완료] {zip_path}", flush=True)


if __name__ == "__main__":
    main()
