# -*- coding: utf-8 -*-
"""
Streamlit wrapper runner for crawler_data.py.

중요 원칙:
- crawler_data.py의 파일데이터/과거데이터 다운로드 루프는 유지한다.
- 실행 위치만 output_dir로 바꿔 결과 ZIP을 찾기 쉽게 한다.
"""
import argparse
import json
import os
from pathlib import Path

from crawler_data import main as run_download_crawler


def main():
    parser = argparse.ArgumentParser(description="기관별 파일데이터 다운로드 wrapper")
    parser.add_argument("--inst-name", required=True)
    parser.add_argument("--org-url", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--result-json", required=True)
    parser.add_argument("--headless", choices=["true", "false"], default="true")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    print("=" * 80, flush=True)
    print("[Streamlit wrapper - download_runner]", flush=True)
    print(f"- inst_name: {args.inst_name}", flush=True)
    print(f"- org_url: {args.org_url}", flush=True)
    print(f"- output_dir: {output_dir}", flush=True)
    print("※ crawler_data.py의 다운로드 로직을 그대로 실행합니다.", flush=True)
    print("=" * 80, flush=True)

    old_cwd = os.getcwd()
    try:
        os.chdir(output_dir)
        zip_path = run_download_crawler(
            args.inst_name.strip(),
            args.org_url.strip(),
            headless=(args.headless.lower() == "true"),
        )
        zip_path = str(Path(zip_path).resolve())
    finally:
        os.chdir(old_cwd)

    result = {
        "status": "completed",
        "inst_name": args.inst_name.strip(),
        "org_url": args.org_url.strip(),
        "output_dir": str(output_dir),
        "zip_path": zip_path,
    }
    Path(args.result_json).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[ZIP 저장 완료] {zip_path}", flush=True)


if __name__ == "__main__":
    main()
