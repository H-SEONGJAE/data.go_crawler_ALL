# -*- coding: utf-8 -*-
"""
Streamlit wrapper runner for crawler_data.py.

v5 URL 최적화
- 기관명만 입력해도 공통 URL resolver로 기관별 URL을 확정한다.
- 정확 기관 필터 URL이 0건이면 keyword/orgSearch fallback을 허용하되,
  crawler_data.py에 title_prefix_filter를 넘겨 목록명 prefix가 맞는 데이터만 다운로드한다.
- crawler_data.py의 현재데이터/과거데이터 다운로드 루프는 유지한다.
"""
import argparse
import json
import os
import sys
from pathlib import Path

from crawler_data import main as run_download_crawler
from org_url_resolver import build_collection_target

try:
    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)
except Exception:
    pass


def resolve_download_target(inst_name: str, org_url: str = "") -> dict:
    result = build_collection_target(
        org_input=inst_name,
        target_url=org_url,
        timeout=5,
        per_page=10,
        allow_keyword_fallback=True,
    )
    if not result.get("found") or not result.get("target_url"):
        raise RuntimeError(
            "기관별 파일데이터 다운로드 URL을 확정하지 못했습니다. "
            "정확 기관 필터 URL이 0건이고, 목록명 prefix 기준 fallback도 실패했습니다. "
            f"input={inst_name}, debug={json.dumps(result.get('debug', {}), ensure_ascii=False)[:2000]}"
        )
    return result


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

    resolved = resolve_download_target(args.inst_name.strip(), args.org_url.strip())
    resolved_inst_name = resolved.get("exact_org") or args.inst_name.strip()
    resolved_org_url = resolved["target_url"]
    title_prefix_filter = resolved.get("title_prefix_filter") or ""

    print("=" * 80, flush=True)
    print("[Streamlit wrapper - download_runner]", flush=True)
    print(f"- input inst_name: {args.inst_name}", flush=True)
    print(f"- resolved inst_name: {resolved_inst_name}", flush=True)
    print(f"- resolved org_url: {resolved_org_url}", flush=True)
    print(f"- resolve_mode: {resolved.get('mode')}", flush=True)
    print(f"- title_prefix_filter: {title_prefix_filter}", flush=True)
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
            title_prefix_filter=title_prefix_filter,
        )
        zip_path = str(Path(zip_path).resolve())
    finally:
        os.chdir(old_cwd)

    result = {
        "status": "completed",
        "input_inst_name": args.inst_name.strip(),
        "inst_name": resolved_inst_name,
        "org_url": resolved_org_url,
        "resolve_mode": resolved.get("mode"),
        "title_prefix_filter": title_prefix_filter,
        "output_dir": str(output_dir),
        "zip_path": zip_path,
        "resolve_debug": resolved.get("debug", {}),
    }
    Path(args.result_json).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[ZIP 저장 완료] {zip_path}", flush=True)


if __name__ == "__main__":
    main()
