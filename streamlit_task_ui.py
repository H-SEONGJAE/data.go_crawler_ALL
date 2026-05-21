# -*- coding: utf-8 -*-
"""Streamlit 백그라운드 크롤링 작업 공통 UI/상태 관리 유틸.

- 크롤링 로직 자체는 변경하지 않고, 시작/중지/진행상황 표시를 붙이기 위한 보조 모듈입니다.
- 각 크롤러 함수는 status_callback, stop_event를 선택적으로 받아 진행상황과 중지 요청을 처리합니다.
"""

from __future__ import annotations

import queue
import threading
import time
import traceback
from typing import Any, Callable

import streamlit as st


MAX_LOG_LINES = 300


def _now() -> str:
    return time.strftime("%H:%M:%S")


def get_task_state(task_key: str) -> dict | None:
    return st.session_state.get(task_key)


def is_task_running(task_key: str) -> bool:
    state = get_task_state(task_key)
    return bool(state and state.get("running"))


def clear_task(task_key: str) -> None:
    state = get_task_state(task_key)
    if state and state.get("running"):
        return
    if task_key in st.session_state:
        del st.session_state[task_key]


def start_task(
    task_key: str,
    target: Callable[..., Any],
    *args: Any,
    task_name: str = "크롤링 작업",
    **kwargs: Any,
) -> bool:
    """백그라운드 작업 시작.

    target 함수에는 status_callback, stop_event 키워드 인자를 주입합니다.
    """
    if is_task_running(task_key):
        return False

    msg_queue: queue.Queue = queue.Queue()
    stop_event = threading.Event()

    state = {
        "task_name": task_name,
        "running": True,
        "status": "running",
        "started_at": time.time(),
        "finished_at": None,
        "queue": msg_queue,
        "stop_event": stop_event,
        "thread": None,
        "logs": [],
        "last_message": "작업 대기 중",
        "current": 0,
        "total": 0,
        "result": None,
        "error": None,
        "traceback": None,
    }

    def status_callback(msg: str, current: int | None = None, total: int | None = None, level: str = "info") -> None:
        msg_queue.put({
            "ts": _now(),
            "msg": str(msg),
            "current": current,
            "total": total,
            "level": level,
        })

    def runner() -> None:
        try:
            status_callback(f"▶ {task_name} 시작")
            result = target(*args, status_callback=status_callback, stop_event=stop_event, **kwargs)
            state["result"] = result
            if stop_event.is_set():
                state["status"] = "stopped"
                status_callback("⏹ 중지 요청에 따라 작업을 종료했습니다.", level="warning")
            else:
                state["status"] = "done"
                status_callback(f"✅ {task_name} 완료", level="success")
        except Exception as exc:  # noqa: BLE001
            state["status"] = "error"
            state["error"] = repr(exc)
            state["traceback"] = traceback.format_exc()
            status_callback(f"🚨 오류 발생: {exc}", level="error")
        finally:
            state["running"] = False
            state["finished_at"] = time.time()

    thread = threading.Thread(target=runner, daemon=True)
    state["thread"] = thread
    st.session_state[task_key] = state
    thread.start()
    return True


def request_stop(task_key: str) -> bool:
    state = get_task_state(task_key)
    if not state:
        return False
    stop_event = state.get("stop_event")
    if stop_event:
        stop_event.set()
    state["last_message"] = "중지 요청됨. 현재 처리 중인 단계를 마친 뒤 종료합니다."
    state["status"] = "stopping"
    return True


def _drain_queue(state: dict) -> None:
    q = state.get("queue")
    if not q:
        return
    while True:
        try:
            item = q.get_nowait()
        except queue.Empty:
            break

        line = f"[{item.get('ts', _now())}] {item.get('msg', '')}"
        state.setdefault("logs", []).append(line)
        state["logs"] = state["logs"][-MAX_LOG_LINES:]
        state["last_message"] = item.get("msg", "")

        current = item.get("current")
        total = item.get("total")
        if current is not None:
            try:
                state["current"] = int(current)
            except Exception:
                pass
        if total is not None:
            try:
                state["total"] = int(total)
            except Exception:
                pass


def render_task_status(task_key: str, title: str = "진행상황", auto_refresh: bool = True) -> dict | None:
    """작업 상태 패널 렌더링. 작업 중이면 1초 단위 자동 새로고침."""
    state = get_task_state(task_key)
    if not state:
        st.info("대기 중입니다. [시작] 버튼을 누르면 진행상황이 표시됩니다.")
        return None

    _drain_queue(state)

    status = state.get("status", "idle")
    running = bool(state.get("running"))
    current = int(state.get("current") or 0)
    total = int(state.get("total") or 0)
    last_message = state.get("last_message", "")

    st.markdown(f"### {title}")

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        label = {
            "running": "실행 중",
            "stopping": "중지 요청됨",
            "stopped": "중지됨",
            "done": "완료",
            "error": "오류",
        }.get(status, status)
        st.metric("상태", label)
    with c2:
        st.metric("처리", f"{current:,}" if not total else f"{current:,}/{total:,}")
    with c3:
        elapsed = 0
        if state.get("started_at"):
            end = time.time() if running else (state.get("finished_at") or time.time())
            elapsed = int(end - state["started_at"])
        st.metric("경과", f"{elapsed//60:02d}:{elapsed%60:02d}")
    with c4:
        st.metric("로그", f"{len(state.get('logs', [])):,}줄")

    if total > 0:
        ratio = min(max(current / total, 0), 1)
        st.progress(ratio, text=f"{current:,}/{total:,} 처리 중")
    else:
        if running:
            st.progress(0, text="총 건수 확인 중 또는 단계형 작업 진행 중")

    if last_message:
        if status == "error":
            st.error(last_message)
        elif status in ["stopping", "stopped"]:
            st.warning(last_message)
        elif status == "done":
            st.success(last_message)
        else:
            st.info(last_message)

    logs = state.get("logs", [])
    if logs:
        with st.expander("실행 로그 보기", expanded=running):
            st.text("\n".join(logs[-80:]))

    if state.get("error"):
        with st.expander("오류 상세 보기"):
            st.code(state.get("traceback") or state.get("error") or "", language="text")

    if running and auto_refresh:
        time.sleep(1)
        st.rerun()

    return state
