# -*- coding: utf-8 -*-
import json
import os
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

    popen_kwargs = {
        "cwd": str(Path(__file__).resolve().parent),
        "stdout": log_f,
        "stderr": subprocess.STDOUT,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
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

    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("상태", "실행 중" if running else state.get("status", "대기"))
    with col2:
        started_at = state.get("started_at")
        elapsed = int(time.time() - started_at) if started_at else 0
        st.metric("경과 시간", f"{elapsed//60:02d}:{elapsed%60:02d}")
    with col3:
        st.metric("PID", state.get("pid", "-"))

    if running:
        if st.button("⛔ 중지", key=f"{task_key}_stop", use_container_width=True):
            stop_process_task(task_key)
            st.rerun()

    log_text = read_tail(state.get("log_path"))
    with st.expander("실행 로그", expanded=True):
        st.code(log_text[-12000:] if log_text else "로그가 아직 없습니다.", language="text")

    result = load_result(state.get("result_json"))
    if result:
        with st.expander("결과 정보", expanded=False):
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
    return [sys.executable, script_name, *map(str, args)]
