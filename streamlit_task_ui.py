# -*- coding: utf-8 -*-
"""
Streamlit background task UI helpers.

변경 목적
- 검증 완료된 크롤러 내부 로직은 건드리지 않고, Streamlit 화면 표시만 정리합니다.
- 진행상황은 카드/장문 로그 대신 progress bar 중심으로 표시합니다.
- 실행 로그는 기본 접힘(expander expanded=False) 상태로 두고, 필요할 때만 열어봅니다.
"""
import json
import os
import re
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import streamlit as st


TASK_ROOT = Path("outputs")


def now_stamp():
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def ensure_task_state(task_key: str):
    st.session_state.setdefault(task_key, {})
    return st.session_state[task_key]


def is_proc_running(proc):
    return proc is not None and proc.poll() is None


def create_task_dir(category: str, label: str):
    safe = "".join(c if c.isalnum() or c in "-_()가-힣" else "_" for c in label.strip())[:80] or "task"
    task_dir = TASK_ROOT / category / f"{now_stamp()}_{safe}"
    task_dir.mkdir(parents=True, exist_ok=True)
    return task_dir


def start_process_task(task_key: str, cmd: list[str], task_dir: Path):
    state = ensure_task_state(task_key)

    if is_proc_running(state.get("proc")):
        st.warning("이미 실행 중인 작업이 있습니다. 먼저 중지하거나 완료 후 다시 실행하세요.")
        return

    log_path = task_dir / "run.log"
    result_json = task_dir / "result.json"
    log_f = open(log_path, "w", encoding="utf-8", buffering=1)

    env = os.environ.copy()
    # stdout이 파일로 redirect되면 print 출력이 버퍼링될 수 있으므로 실시간 로그 반영을 위해 설정합니다.
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"

    popen_kwargs = {
        "cwd": str(Path(__file__).resolve().parent),
        "stdout": log_f,
        "stderr": subprocess.STDOUT,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "env": env,
    }

    if os.name == "nt":
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        popen_kwargs["start_new_session"] = True

    proc = subprocess.Popen(cmd, **popen_kwargs)

    state.update({
        "proc": proc,
        "pid": proc.pid,
        "task_dir": str(task_dir),
        "log_path": str(log_path),
        "result_json": str(result_json),
        "started_at": time.time(),
        "status": "running",
        "cmd": cmd,
    })
    st.session_state[task_key] = state
    st.success(f"작업을 시작했습니다. PID={proc.pid}")


def stop_process_task(task_key: str):
    state = ensure_task_state(task_key)
    proc = state.get("proc")

    if not is_proc_running(proc):
        st.info("현재 실행 중인 작업이 없습니다.")
        return

    try:
        if os.name == "nt":
            proc.send_signal(signal.CTRL_BREAK_EVENT)
            time.sleep(1)
            if proc.poll() is None:
                proc.terminate()
        else:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        state["status"] = "stopping"
        st.session_state[task_key] = state
        st.warning("중지 요청을 보냈습니다. 브라우저/다운로드 정리 후 종료됩니다.")
    except Exception as e:
        st.error(f"중지 요청 실패: {e}")


def read_tail(path: str, max_chars: int = 12000):
    if not path or not Path(path).exists():
        return ""
    p = Path(path)
    try:
        with open(p, "rb") as f:
            size = f.seek(0, os.SEEK_END)
            f.seek(max(0, size - max_chars), os.SEEK_SET)
            data = f.read().decode("utf-8", errors="replace")
        return data
    except Exception as e:
        return f"로그 파일 읽기 실패: {e}"


def load_result(path: str):
    if not path or not Path(path).exists():
        return None
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return None


def _latest_match(pattern: str, text: str, flags=0):
    matches = list(re.finditer(pattern, text, flags))
    return matches[-1] if matches else None


def _parse_metadata_progress(log_text: str, running: bool, result: dict | None):
    """crawler_metadata.py 로그를 기반으로 진행률을 계산합니다."""
    if result and result.get("status") == "completed":
        return 100, "수집 완료"
    if "[⭐️전체 완료⭐️]" in log_text or "[전체 완료]" in log_text:
        return 100, "수집 완료"

    # 상세 수집 단계: [⭐️DETAIL]   100/1000 ( 10.0%) ... 형식
    m = _latest_match(r"\[⭐️DETAIL\]\s+(\d+)\s*/\s*(\d+)\s*\(\s*([\d.]+)%\)", log_text)
    if m:
        done, total, pct = int(m.group(1)), int(m.group(2)), float(m.group(3))
        # 상세 수집은 전체 흐름의 핵심 단계로 보고 30~95% 구간에 매핑합니다.
        mapped = 30 + int(min(100.0, pct) * 0.65)
        return min(mapped, 98 if running else 100), f"상세 메타데이터 수집 중 · {done:,}/{total:,}건"

    # 목록 URL 수집 단계: [LIST] page 01/17 수집 중
    m = _latest_match(r"\[LIST\]\s+page\s+(\d+)\s*/\s*(\d+)", log_text)
    if m:
        page, total = int(m.group(1)), int(m.group(2))
        if total > 0:
            pct = min(29, 5 + int((page / total) * 24))
            return pct, f"상세 URL 목록 수집 중 · {page}/{total}페이지"
        return min(25, 5 + page), f"상세 URL 목록 수집 중 · {page}페이지"

    # max_pages=0인 경우 page/0으로 나와 정확한 전체 비율을 알 수 없어 단계 진행률로 표시합니다.
    m = _latest_match(r"\[LIST\]\s+page\s+(\d+)", log_text)
    if m:
        page = int(m.group(1))
        return min(25, 5 + page), f"상세 URL 목록 수집 중 · {page}페이지"

    if "실패로그 기준 Playwright 재수집" in log_text or "[실패URL 재수집" in log_text:
        return 96, "실패 URL 재수집 중"

    if "crawler_metadata.py 실행 진입" in log_text or "공공데이터포털 메타데이터 수집" in log_text:
        return 3, "메타데이터 수집 엔진 준비 중"

    return (2 if running else 0), "대기 중"


def _parse_stats_progress(log_text: str, running: bool, result: dict | None):
    """조회수/다운로드 수 수집 진행률을 추정합니다."""
    if result and result.get("status") == "completed":
        return 100, f"수집 완료 · {result.get('row_count', 0):,}건"
    m = _latest_match(r"수집 완료:\s*총\s*(\d+)건", log_text)
    if m:
        return 100, f"수집 완료 · {int(m.group(1)):,}건"

    # metadata parser 기반 stats_runner 로그
    m = _latest_match(r"\[LIST\]\s+page\s+(\d+)\s+신규\s+([0-9,]+)건\s+\|\s+누적\s+([0-9,]+)건", log_text)
    if m:
        page = int(m.group(1))
        total_count = int(m.group(3).replace(',', ''))
        pct = min(95, 5 + page * 8)
        return pct, f"목록 수집 중 · {page}페이지 · 누적 {total_count:,}건"

    m = _latest_match(r"\[LIST\]\s+page\s+(\d+)\s+요청 중", log_text)
    if m:
        page = int(m.group(1))
        return min(90, 3 + page * 8), f"목록 페이지 요청 중 · {page}페이지"

    # 구 Selenium crawler.py 로그도 남겨둠
    m = _latest_match(r"페이지\s+(\d+)\s+수집 중\s*\(누적\s*(\d+)건\)", log_text)
    if m:
        page, count = int(m.group(1)), int(m.group(2))
        pct = min(95, 10 + page * 5)
        return pct, f"목록 수집 중 · {page}페이지 · 누적 {count:,}건"

    if "기관 후보" in log_text:
        return 2, "기관 후보 확인 중"

    return (2 if running else 0), "대기 중"


def _parse_download_progress(log_text: str, running: bool, result: dict | None):
    """crawler_data.py 로그를 기반으로 파일 다운로드 진행률을 표시합니다."""
    if result and result.get("status") == "completed":
        return 100, "다운로드 완료"
    if "전체 다운로드 완료" in log_text or "ZIP 저장 완료" in log_text:
        return 100, "다운로드 완료"

    # 현재 페이지 안에서 몇 번째 데이터셋을 처리 중인지 표시합니다.
    last_page_pos = max(log_text.rfind("📄 페이지"), log_text.rfind("페이지 "))
    page_block = log_text[last_page_pos:] if last_page_pos >= 0 else log_text

    total_m = _latest_match(r"📑\s*(\d+)개\s*데이터셋\s*발견", page_block)
    idx_m = _latest_match(r"📂\s*\[(\d+)\]", page_block)
    if total_m and idx_m:
        total = max(1, int(total_m.group(1)))
        idx = min(total, int(idx_m.group(1)))
        page_pct = int((idx / total) * 80)
        pct = min(95, 10 + page_pct)
        return pct, f"파일데이터 다운로드 중 · 현재 페이지 {idx}/{total}건"

    m = _latest_match(r"페이지\s+(\d+)\s+처리\s+시작", log_text)
    if m:
        page = int(m.group(1))
        return min(35, 8 + page * 4), f"파일데이터 목록 {page}페이지 처리 중"

    if "기관별 전용 페이지 접속 완료" in log_text:
        return 5, "기관별 페이지 접속 완료"
    if "다운로드 로직을 그대로 실행" in log_text:
        return 2, "다운로드 엔진 준비 중"

    return (2 if running else 0), "대기 중"


def infer_progress(task_key: str, log_text: str, running: bool, status_text: str, result: dict | None):
    """작업 종류별 로그를 해석해 progress bar 값을 반환합니다."""
    if status_text.startswith("failed"):
        return 100, "오류로 종료됨"
    if status_text == "stopped":
        return 100, "중지됨"
    if "meta" in task_key:
        return _parse_metadata_progress(log_text, running, result)
    if "stats" in task_key:
        return _parse_stats_progress(log_text, running, result)
    if "download" in task_key:
        return _parse_download_progress(log_text, running, result)

    # 공통 fallback: 로그에 있는 마지막 퍼센트 값을 사용합니다.
    pct_matches = re.findall(r"\((\s*[\d.]+)\s*%\)", log_text or "")
    if pct_matches:
        pct = int(float(pct_matches[-1]))
        return min(max(pct, 0), 100), f"진행 중 · {pct}%"
    return (5 if running else 100 if status_text == "completed" else 0), status_text


def render_task_panel(task_key: str, title: str = "작업 진행상황"):
    state = ensure_task_state(task_key)
    proc = state.get("proc")
    running = is_proc_running(proc)

    if proc is not None and not running and state.get("status") in ["running", "stopping"]:
        returncode = proc.returncode
        state["status"] = "completed" if returncode == 0 else f"failed({returncode})"
        state["finished_at"] = time.time()
        st.session_state[task_key] = state

    st.markdown(f"### {title}")

    if not state:
        st.info("아직 실행된 작업이 없습니다.")
        return None

    started_at = state.get("started_at")
    elapsed = int(time.time() - started_at) if started_at else 0
    status_text = "실행 중" if running else state.get("status", "대기")
    pid_text = state.get("pid", "-")

    log_text = read_tail(state.get("log_path"), max_chars=20000)
    result = load_result(state.get("result_json"))
    progress_value, progress_label = infer_progress(task_key, log_text, running, status_text, result)
    progress_value = max(0, min(100, int(progress_value)))

    elapsed_txt = f"{elapsed // 3600:02d}:{(elapsed % 3600) // 60:02d}:{elapsed % 60:02d}"
    progress_text = f"{progress_label} · {progress_value}% · 상태: {status_text} · 경과: {elapsed_txt}"
    if pid_text != "-":
        progress_text += f" · PID: {pid_text}"

    st.progress(progress_value, text=progress_text)

    if running:
        if st.button("⛔ 중지", key=f"{task_key}_stop", use_container_width=True):
            stop_process_task(task_key)
            st.rerun()

    # 로그는 화면을 복잡하게 만들지 않도록 기본 접힘 상태로 둡니다.
    with st.expander("실행 로그 보기", expanded=False):
        st.code(log_text[-20000:] if log_text else "로그가 아직 없습니다.", language="text")

    if result:
        with st.expander("결과 정보 보기", expanded=False):
            st.json(result)

    if running:
        time.sleep(2)
        st.rerun()

    return result


def download_file_button(path, label, mime, file_name=None, key=None):
    if not path:
        return
    p = Path(path)
    if not p.exists():
        return
    with open(p, "rb") as f:
        st.download_button(
            label=label,
            data=f,
            file_name=file_name or p.name,
            mime=mime,
            use_container_width=True,
            key=key,
        )


def python_cmd(script_name: str, *args: str):
    # -u keeps stdout/stderr unbuffered so the Streamlit log panel updates while crawling.
    return [sys.executable, "-u", script_name, *map(str, args)]
