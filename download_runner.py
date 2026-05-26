# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import json
from pathlib import Path

from crawler_data_integrated import main as run_download


def main():
    parser = argparse.ArgumentParser(description="Resolver URL 기반 파일데이터 다운로드 runner")
    parser.add_argument("--resolution-json", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--result-json", required=True)
    parser.add_argument("--headless", choices=["true", "false"], default="true")
    parser.add_argument("--browser-executable-path", default="")
    parser.add_argument("--max-pages", default="0")
    args = parser.parse_args()

    data = json.loads(Path(args.resolution_json).read_text(encoding="utf-8"))
    org = data.get("selected_provider") or data.get("input_keyword") or "기관"
    org_url = data.get("resolved_url") or data.get("provider_url_from_detail")
    if not org_url:
        raise RuntimeError("resolution_json에 resolved_url이 없습니다.")

    print("=" * 80, flush=True)
    print("[download_runner] 파일데이터 다운로드", flush=True)
    print(f"- org: {org}", flush=True)
    print(f"- org_url: {org_url}", flush=True)
    print("※ crawler_data_integrated.py의 현재/과거 다운로드 로직을 실행합니다.", flush=True)
    print("=" * 80, flush=True)

    zip_path = run_download(
        inst_name=org,
        org_url=org_url,
        output_dir=args.output_dir,
        headless=args.headless.lower() == "true",
        browser_executable_path=args.browser_executable_path or None,
        max_pages=int(args.max_pages or 0),
    )

    result = {
        "status": "completed",
        "org": org,
        "org_url": org_url,
        "output_dir": str(Path(args.output_dir).resolve()),
        "zip_path": str(Path(zip_path).resolve()),
    }
    Path(args.result_json).parent.mkdir(parents=True, exist_ok=True)
    Path(args.result_json).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
