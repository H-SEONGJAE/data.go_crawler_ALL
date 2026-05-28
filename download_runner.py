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
import re
import sys
import time
import traceback
from pathlib import Path

import crawler_data


def _print_status(message: str):
    print(str(message), flush=True)


def _write_json_atomic(path: Path, payload: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, path)


def _elapsed_text(start_time):
    elapsed = max(0, int(time.time() - float(start_time or time.time())))
    h, rem = divmod(elapsed, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def _update_progress_from_message(progress: dict, message: str):
    msg = str(message).strip()
    start_time = progress.get("start_time") or time.time()
    expected_pages = max(int(progress.get("expected_pages") or 1), 1)

    percent = int(progress.get("percent") or 5)
    display_msg = f"{msg} / 경과 {_elapsed_text(start_time)}"

    page_match = (
        re.search(r"파일데이터 목록\s+(\d+)페이지", msg)
        or re.search(r"(\d+)페이지 완료", msg)
    )
    target_match = re.search(r"다운로드 대상 데이터셋\s+([\d,]+)건", msg)
    dataset_match = re.search(r"\[(\d+)\s*/\s*(\d+)\]", msg)

    if page_match:
        page_no = int(page_match.group(1))
        progress["page_no"] = page_no
        percent = 5 + min(25, int((page_no / expected_pages) * 25))
        display_msg = (
            f"파일데이터 목록 수집 중... {page_no:,}/{expected_pages:,}페이지 / "
            f"경과 {_elapsed_text(start_time)}"
        )

    elif target_match:
        total = int(target_match.group(1).replace(",", ""))
        progress["total"] = total
        percent = 30
        display_msg = (
            f"다운로드 대상 데이터셋 {total:,}건 확인 완료. 파일 다운로드를 시작합니다. / "
            f"경과 {_elapsed_text(start_time)}"
        )

    elif dataset_match:
        current = int(dataset_match.group(1))
        total = max(int(dataset_match.group(2)), 1)
        progress["current"] = current
        progress["total"] = total

        ratio = current / total
        percent = 30 + int(ratio * 60)
        display_msg = (
            f"파일데이터 다운로드 중... {current:,}/{total:,}건 "
            f"({ratio * 100:.1f}%) / "
            f"현재데이터 저장 {int(progress.get('current_saved') or 0):,}건 / "
            f"과거데이터 저장 {int(progress.get('past_saved') or 0):,}건 / "
            f"실패 {int(progress.get('failed') or 0):,}건 / "
            f"경과 {_elapsed_text(start_time)}"
        )

    elif "현재데이터" in msg and "저장" in msg:
        progress["current_saved"] = int(progress.get("current_saved") or 0) + 1
        current = int(progress.get("current") or 0)
        total = max(int(progress.get("total") or 1), 1)
        ratio = current / total if total else 0
        percent = 30 + int(ratio * 60)
        display_msg = (
            f"현재데이터 저장 완료... {current:,}/{total:,}건 "
            f"({ratio * 100:.1f}%) / "
            f"현재데이터 저장 {int(progress.get('current_saved') or 0):,}건 / "
            f"과거데이터 저장 {int(progress.get('past_saved') or 0):,}건 / "
            f"실패 {int(progress.get('failed') or 0):,}건 / "
            f"경과 {_elapsed_text(start_time)}"
        )

    elif "과거데이터" in msg and "저장" in msg:
        progress["past_saved"] = int(progress.get("past_saved") or 0) + 1
        current = int(progress.get("current") or 0)
        total = max(int(progress.get("total") or 1), 1)
        ratio = current / total if total else 0
        percent = 30 + int(ratio * 60)
        display_msg = (
            f"과거데이터 저장 중... {current:,}/{total:,}건 "
            f"({ratio * 100:.1f}%) / "
            f"현재데이터 저장 {int(progress.get('current_saved') or 0):,}건 / "
            f"과거데이터 저장 {int(progress.get('past_saved') or 0):,}건 / "
            f"실패 {int(progress.get('failed') or 0):,}건 / "
            f"경과 {_elapsed_text(start_time)}"
        )

    elif "실패" in msg or "오류" in msg:
        progress["failed"] = int(progress.get("failed") or 0) + 1
        current = int(progress.get("current") or 0)
        total = max(int(progress.get("total") or 1), 1)
        ratio = current / total if total else 0
        percent = 30 + int(ratio * 60)
        display_msg = (
            f"일부 파일 처리 실패 후 계속 진행 중... {current:,}/{total:,}건 "
            f"({ratio * 100:.1f}%) / "
            f"현재데이터 저장 {int(progress.get('current_saved') or 0):,}건 / "
            f"과거데이터 저장 {int(progress.get('past_saved') or 0):,}건 / "
            f"실패 {int(progress.get('failed') or 0):,}건 / "
            f"경과 {_elapsed_text(start_time)}"
        )

    elif "전체 다운로드 완료" in msg:
        percent = 98
        display_msg = f"전체 다운로드 완료. ZIP 파일 생성 중... / 경과 {_elapsed_text(start_time)}"

    progress["percent"] = max(0, min(100, int(percent)))
    progress["message"] = display_msg
    progress["raw_message"] = msg
    progress["updated_at"] = time.time()
    progress["state"] = "running"
    return progress


def main():
    if len(sys.argv) < 3:
        raise RuntimeError("사용법: python download_runner.py <config_json_path> <result_json_path>")

    config_path = Path(sys.argv[1])
    result_path = Path(sys.argv[2])
    result_path.parent.mkdir(parents=True, exist_ok=True)

    progress_path = result_path.with_name(result_path.name.replace("download_result_", "download_progress_"))
    progress = {
        "state": "running",
        "percent": 1,
        "message": "파일데이터 다운로드 작업을 시작합니다.",
        "raw_message": "",
        "current": 0,
        "total": 0,
        "current_saved": 0,
        "past_saved": 0,
        "failed": 0,
        "page_no": 0,
        "expected_pages": 1,
        "start_time": time.time(),
        "updated_at": time.time(),
        "zip_path": "",
        "error": "",
    }

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)

        if config.get("progress_path"):
            progress_path = Path(config["progress_path"])

        progress["expected_pages"] = max(int(config.get("expected_pages") or 1), 1)
        progress["total"] = int(config.get("total_count") or 0)
        progress["message"] = "파일데이터 다운로드 준비 중..."
        _write_json_atomic(progress_path, progress)

        def _status_callback(message: str):
            nonlocal progress
            _print_status(message)
            progress = _update_progress_from_message(progress, message)
            _write_json_atomic(progress_path, progress)

        zip_path = crawler_data.collect_portal_files(
            inst_name=config["inst_name"],
            org_url=config.get("org_url"),
            include_past=bool(config.get("include_past", True)),
            output_root=config.get("output_root", "."),
            status_callback=_status_callback,
            per_page=int(config.get("per_page", 100)),
            max_pages=int(config.get("max_pages", 1000)),
            headless=bool(config.get("headless", True)),
        )

        progress["state"] = "completed"
        progress["percent"] = 100
        progress["zip_path"] = os.path.abspath(zip_path)
        progress["message"] = "파일데이터 다운로드 및 ZIP 생성이 완료되었습니다."
        progress["updated_at"] = time.time()
        _write_json_atomic(progress_path, progress)

        result = {
            "ok": True,
            "zip_path": os.path.abspath(zip_path),
            "error": "",
            "traceback": "",
        }

    except Exception as e:
        err = repr(e)
        _print_status(f"🚨 파일데이터 다운로드 runner 오류: {err}")
        progress["state"] = "failed"
        progress["percent"] = 100
        progress["message"] = f"파일 다운로드 중 오류가 발생했습니다: {err}"
        progress["error"] = err
        progress["updated_at"] = time.time()
        try:
            _write_json_atomic(progress_path, progress)
        except Exception:
            pass

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
