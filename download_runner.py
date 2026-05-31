# -*- coding: utf-8 -*-
"""
download_runner.py

Streamlit 파일데이터 다운로드 메뉴에서 호출하는 별도 실행 프로세스용 runner입니다.
- main.py와 같은 폴더에 배치합니다.
- crawler_data.collect_portal_files() 실행은 이 파일에서 수행합니다.
- 진행 로그는 stdout으로 출력하고, 최종 결과는 result JSON에 기록합니다.
"""

import json
import os
import sys
import traceback
from pathlib import Path

import crawler_data


def _print_status(message: str):
    print(str(message), flush=True)


def main():
    if len(sys.argv) < 3:
        raise RuntimeError("사용법: python download_runner.py <config_json_path> <result_json_path>")

    config_path = Path(sys.argv[1])
    result_path = Path(sys.argv[2])
    result_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)

        zip_path = crawler_data.collect_portal_files(
            inst_name=config["inst_name"],
            org_url=config.get("org_url"),
            include_past=bool(config.get("include_past", True)),
            output_root=config.get("output_root", "."),
            status_callback=_print_status,
            per_page=int(config.get("per_page", 100)),
            max_pages=int(config.get("max_pages", 1000)),
            headless=bool(config.get("headless", True)),
        )

        result = {
            "ok": True,
            "zip_path": os.path.abspath(zip_path),
            "error": "",
            "traceback": "",
        }

    except Exception as e:
        err = repr(e)
        _print_status(f"🚨 파일데이터 다운로드 runner 오류: {err}")
        result = {
            "ok": False,
            "zip_path": "",
            "error": err,
            "traceback": traceback.format_exc(),
        }

    with open(result_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    if not result.get("ok"):
        sys.exit(1)


if __name__ == "__main__":
    main()
