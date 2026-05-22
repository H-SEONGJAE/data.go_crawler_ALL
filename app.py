# -*- coding: utf-8 -*-
"""공공데이터 포털 크롤링 통합 Streamlit 앱 - Tabs UI 버전."""
from __future__ import annotations

import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

from portal_common import build_file_list_url, clean_filename, file_size_label, resolve_org_name

APP_DIR = Path(__file__).resolve().parent
RUNS_DIR = APP_DIR / "runs"
RUNS_DIR.mkdir(exist_ok=True)

st.set_page_config(
    page_title="공공데이터 포털 크롤링 통합",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ==========================================================
# UI STYLE
# ==========================================================

st.markdown(
    """
    <style>
    .block-container { padding-top: 1.4rem; padding-bottom: 3rem; }
    .main-title {
        font-size: 2.05rem;
        font-weight: 800;
        letter-spacing: -0.04em;
        margin-bottom: 0.25rem;
    }
    .sub-title {
        color: #5f6368;
        font-size: 1.02rem;
        margin-bottom: 1.0rem;
    }
    .guide-card {
        border: 1px solid #e7e9ef;
        border-radius: 18px;
        padding: 1.0rem 1.1rem;
        background: #fbfcff;
        min-height: 118px;
    }
    .guide-card h4 {
        margin: 0 0 0.35rem 0;
        font-size: 1.0rem;
    }
    .guide-card p {
        margin: 0;
        color: #5f6368;
        line-height: 1.55;
        font-size: 0.92rem;
    }
    .section-title {
        font-size: 1.22rem;
        font-weight: 750;
        margin: 0.35rem 0 0.55rem 0;
        letter-spacing: -0.025em;
    }
    .hint-box {
        border-left: 4px solid #d0d7de;
        background: #f6f8fa;
        border-radius: 10px;
        padding: 0.75rem 0.9rem;
        color: #444;
        font-size: 0.92rem;
        line-height: 1.55;
    }
    .url-box {
        word-break: break-all;
        border: 1px dashed #d0d7de;
        border-radius: 12px;
        padding: 0.75rem 0.85rem;
        background: #ffffff;
        color: #3f4650;
        font-size: 0.86rem;
        line-height: 1.45;
    }
    div[data-testid="stMetric"] {
        background: #ffffff;
        border: 1px solid #eceff3;
        border-radius: 16px;
        padding: 0.6rem 0.75rem;
    }
    .footer-note {
        color: #6a737d;
        font-size: 0.86rem;
        margin-top: 2rem;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# ==========================================================
# TASK HELPERS
# ==========================================================


def make_run_dir(kind: str, name: str) -> Path:
    safe = clean_filename(name, fallback=kind)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    task_dir = RUNS_DIR / f"{kind}_{safe}_{ts}"
    task_dir.mkdir(parents=True, exist_ok=True)
    return task_dir


def start_task(key: str, cmd: list[str], task_dir: Path) -> None:
    log_path = task_dir / "run.log"
    result_json = task_dir / "result.json"

    log_file = open(log_path, "w", encoding="utf-8", buffering=1)
    proc = subprocess.Popen(
        cmd,
        cwd=str(APP_DIR),
        stdout=log_file,
        stderr=subprocess.STDOUT,
        text=True,
    )
    st.session_state[key] = {
        "proc": proc,
        "cmd": cmd,
        "task_dir": str(task_dir),
        "log_path": str(log_path),
        "result_json": str(result_json),
        "started_at": time.time(),
        "log_file": log_file,
    }


def stop_task(key: str) -> None:
    task = st.session_state.get(key)
    if not task:
        return
    proc = task.get("proc")
    if proc is not None and proc.poll() is None:
        proc.terminate()
        time.sleep(0.5)
        if proc.poll() is None:
            proc.kill()
    log_file = task.get("log_file")
    try:
        if log_file:
            log_file.close()
    except Exception:
        pass


def read_text(path: str | Path, limit_chars: int = 24000) -> str:
    p = Path(path)
    if not p.exists():
        return ""
    text = p.read_text(encoding="utf-8", errors="replace")
    return text[-limit_chars:]


def read_result_json(path: str | Path) -> dict[str, Any] | None:
    p = Path(path)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def render_task_status(key: str, title: str, *, auto_refresh: bool = True) -> dict[str, Any] | None:
    task = st.session_state.get(key)
    if not task:
        st.info("아직 실행된 작업이 없습니다. 설정을 확인한 뒤 수집 시작 버튼을 눌러주세요.")
        return None

    proc = task.get("proc")
    result_json = Path(task["result_json"])
    log_path = Path(task["log_path"])
    running = proc is not None and proc.poll() is None
    elapsed = int(time.time() - float(task.get("started_at", time.time())))

    status_col, action_col = st.columns([0.78, 0.22])
    with status_col:
        if running:
            st.info(f"{title} 실행 중 · 경과 {elapsed}초")
        else:
            code = proc.returncode if proc is not None else None
            if code == 0:
                st.success(f"{title} 완료")
            else:
                st.error(f"{title} 실패 또는 중단 · returncode={code}")
    with action_col:
        if running:
            if st.button("작업 중단", key=f"{key}_stop", use_container_width=True):
                stop_task(key)
                st.rerun()
        else:
            if st.button("상태 초기화", key=f"{key}_clear", use_container_width=True):
                st.session_state.pop(key, None)
                st.rerun()

    result = read_result_json(result_json)

    log_text = read_text(log_path)
    with st.expander("실행 로그 보기", expanded=running):
        st.code(log_text or "로그가 아직 없습니다.", language="text")

    if running and auto_refresh:
        time.sleep(1.0)
        st.rerun()

    return result


def download_button(path: str | None, label: str, key: str) -> None:
    if not path:
        return
    p = Path(path)
    if not p.exists():
        st.warning(f"파일을 찾을 수 없습니다: {p}")
        return
    st.download_button(
        label=f"{label} · {file_size_label(p)}",
        data=p.read_bytes(),
        file_name=p.name,
        mime="application/octet-stream",
        key=key,
        use_container_width=True,
    )


def render_result_files(result: dict[str, Any], *, mode: str) -> None:
    if not result:
        return

    st.markdown('<div class="section-title">결과 다운로드</div>', unsafe_allow_html=True)

    if mode in {"all_meta", "org_meta"}:
        c1, c2 = st.columns(2)
        with c1:
            download_button(result.get("metadata_path"), "메타데이터.xlsx", f"{mode}_metadata")
        with c2:
            download_button(result.get("fail_path"), "실패로그.xlsx", f"{mode}_fail")

    elif mode == "stats":
        if result.get("status") == "failed":
            st.error("조회수/다운로드수 수집에 실패했습니다. 오류 파일과 실행 로그를 확인해주세요.")
            download_button(result.get("error_path"), "오류 로그", "stats_error_file")
            return

        c1, c2, c3 = st.columns(3)
        c1.metric("수집 건수", f"{result.get('rows', 0):,}건")
        c2.metric("결과 상태", result.get("status", "-") or "-")
        c3.metric("기관명", result.get("org_name", "-") or "-")
        download_button(result.get("excel_path"), "조회수/다운로드수 엑셀", "stats_excel_file")

        excel_path = result.get("excel_path")
        if excel_path and Path(excel_path).exists():
            with st.expander("결과 미리보기", expanded=True):
                try:
                    df = pd.read_excel(excel_path)
                    st.dataframe(df.head(100), use_container_width=True, hide_index=True)
                except Exception as exc:
                    st.warning(f"미리보기 로드 실패: {exc}")

    elif mode == "download":
        c1, c2 = st.columns(2)
        c1.metric("결과 상태", result.get("status", "-") or "-")
        c2.metric("기관명", result.get("inst_name", "-") or "-")
        download_button(result.get("zip_path"), "최신/과거 다운로드 ZIP", "download_zip_file")


def render_common_options(prefix: str, *, default_pages: int, default_items: int = 0, default_per_page: int = 1000) -> tuple[int, int, int]:
    with st.expander("고급 수집 옵션", expanded=False):
        c1, c2, c3 = st.columns(3)
        with c1:
            max_pages = st.number_input(
                "최대 목록 페이지",
                min_value=0,
                value=default_pages,
                step=1,
                key=f"{prefix}_max_pages",
                help="0이면 빈 페이지가 나올 때까지 진행합니다.",
            )
        with c2:
            max_items = st.number_input(
                "최대 상세/목록 건수",
                min_value=0,
                value=default_items,
                step=100,
                key=f"{prefix}_max_items",
                help="0이면 건수 제한 없이 수집합니다.",
            )
        with c3:
            per_page = st.number_input(
                "페이지당 요청 건수",
                min_value=10,
                max_value=1000,
                value=default_per_page,
                step=10,
                key=f"{prefix}_per_page",
            )
    return int(max_pages), int(max_items), int(per_page)


def render_org_box(prefix: str, *, title: str = "제공기관명") -> tuple[str, str]:
    st.markdown('<div class="section-title">기관 입력</div>', unsafe_allow_html=True)
    c1, c2 = st.columns([0.74, 0.26])
    with c1:
        org_input = st.text_input(
            title,
            placeholder="예: 한국중부발전(주)",
            key=f"{prefix}_org_input",
        )
    with c2:
        st.write("")
        st.write("")
        check_clicked = st.button("기관 URL 확인", key=f"{prefix}_check", use_container_width=True)

    if check_clicked:
        if not org_input.strip():
            st.warning("기관명을 입력해주세요.")
        else:
            with st.spinner("기관 목록 URL 확인 중..."):
                name, url, ok = resolve_org_name(org_input)
            st.session_state[f"{prefix}_resolved_name"] = name
            st.session_state[f"{prefix}_resolved_url"] = url
            st.session_state[f"{prefix}_resolved_ok"] = ok

    org_name = st.session_state.get(f"{prefix}_resolved_name", org_input.strip())
    org_url = st.session_state.get(
        f"{prefix}_resolved_url",
        build_file_list_url(org_input.strip(), current_page=1, per_page=1000) if org_input.strip() else "",
    )
    resolved_ok = st.session_state.get(f"{prefix}_resolved_ok")

    if org_name:
        if resolved_ok is True:
            st.success(f"기관 확인 완료: {org_name}")
        elif resolved_ok is False:
            st.warning(f"목록 1페이지 확인은 실패했지만 입력 기관명 기준으로 실행 가능합니다: {org_name}")
        else:
            st.caption("기관 URL 확인을 누르면 1페이지 목록 존재 여부를 먼저 점검합니다.")

        with st.expander("생성된 파일데이터 목록 URL", expanded=False):
            st.markdown(f'<div class="url-box">{org_url}</div>', unsafe_allow_html=True)

    return org_name, org_url


def render_header() -> None:
    st.markdown('<div class="main-title">📊 공공데이터 포털 크롤링 통합 홈페이지</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="sub-title">기관명만 입력해 메타데이터, 조회수/다운로드수, 최신·과거 파일 다운로드를 한 화면에서 실행합니다.</div>',
        unsafe_allow_html=True,
    )

    g1, g2, g3, g4 = st.columns(4)
    cards = [
        ("① 전체 메타데이터", "공공데이터포털 파일데이터 전체 목록의 상세 메타데이터를 수집합니다."),
        ("② 기관별 메타데이터", "기관명 기반 URL을 자동 생성하고 해당 기관의 메타데이터만 수집합니다."),
        ("③ 조회/다운로드 집계", "파일데이터 목록의 조회수와 다운로드수를 엑셀로 집계합니다."),
        ("④ 최신/과거 다운로드", "기관별 파일데이터의 최신 파일과 과거데이터를 ZIP으로 저장합니다."),
    ]
    for col, (h, p) in zip([g1, g2, g3, g4], cards):
        with col:
            st.markdown(f'<div class="guide-card"><h4>{h}</h4><p>{p}</p></div>', unsafe_allow_html=True)


def render_all_metadata_tab() -> None:
    st.markdown('<div class="section-title">전체 파일데이터 메타데이터 수집</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="hint-box">전체 수집은 대상 건수가 많으므로 처음에는 최대 페이지/건수를 제한해 테스트한 뒤 전체 실행을 권장합니다.</div>',
        unsafe_allow_html=True,
    )

    c1, c2, c3 = st.columns(3)
    with c1:
        run_mode = st.selectbox("실행 모드", ["MAIN", "BOTH"], index=0, key="all_run_mode")
    with c2:
        detail_concurrency = st.number_input("상세 병렬 처리 수", min_value=1, max_value=50, value=20, step=1, key="all_concurrency")
    with c3:
        headless = st.selectbox("브라우저", ["숨김(headless)", "표시"], index=0, key="all_headless")

    max_pages, max_items, per_page = render_common_options("all_meta", default_pages=0, default_items=0, default_per_page=1000)

    st.divider()
    b1, b2 = st.columns([0.28, 0.72])
    with b1:
        start = st.button("전체 메타데이터 수집 시작", type="primary", key="start_all_meta", use_container_width=True)
    with b2:
        st.caption("결과 파일: 메타데이터.xlsx, 실패로그.xlsx")

    if start:
        task_dir = make_run_dir("metadata_all", "all")
        cmd = [
            sys.executable,
            "metadata_runner.py",
            "--scope",
            "all",
            "--run-mode",
            run_mode,
            "--output-dir",
            str(task_dir / "result"),
            "--result-json",
            str(task_dir / "result.json"),
            "--max-pages",
            str(max_pages),
            "--max-items",
            str(max_items),
            "--list-per-page",
            str(per_page),
            "--detail-concurrency",
            str(detail_concurrency),
            "--headless",
            "true" if headless.startswith("숨김") else "false",
        ]
        start_task("task_all_meta", cmd, task_dir)
        st.rerun()

    st.divider()
    result = render_task_status("task_all_meta", "전체 메타데이터 수집")
    if result:
        render_result_files(result, mode="all_meta")


def render_org_metadata_tab() -> None:
    st.markdown('<div class="section-title">기관별 파일데이터 메타데이터 수집</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="hint-box">기관명을 입력하면 포털 검색 URL을 자동 생성합니다. URL 복사 과정 없이 기관별 메타데이터를 수집합니다.</div>',
        unsafe_allow_html=True,
    )

    org_name, _org_url = render_org_box("org_meta")

    c1, c2, c3 = st.columns(3)
    with c1:
        run_mode = st.selectbox("실행 모드", ["MAIN", "BOTH"], index=0, key="org_run_mode")
    with c2:
        detail_concurrency = st.number_input("상세 병렬 처리 수", min_value=1, max_value=50, value=20, step=1, key="org_concurrency")
    with c3:
        headless = st.selectbox("브라우저", ["숨김(headless)", "표시"], index=0, key="org_headless")

    max_pages, max_items, per_page = render_common_options("org_meta", default_pages=0, default_items=0, default_per_page=1000)

    st.divider()
    b1, b2 = st.columns([0.28, 0.72])
    with b1:
        start = st.button("기관별 메타데이터 수집 시작", type="primary", key="start_org_meta", use_container_width=True)
    with b2:
        st.caption("결과 파일: 메타데이터.xlsx, 실패로그.xlsx")

    if start:
        if not org_name:
            st.error("기관명을 입력해주세요.")
        else:
            task_dir = make_run_dir("metadata_org", org_name)
            cmd = [
                sys.executable,
                "metadata_runner.py",
                "--scope",
                "org",
                "--org-name",
                org_name,
                "--run-mode",
                run_mode,
                "--output-dir",
                str(task_dir / "result"),
                "--result-json",
                str(task_dir / "result.json"),
                "--max-pages",
                str(max_pages),
                "--max-items",
                str(max_items),
                "--list-per-page",
                str(per_page),
                "--detail-concurrency",
                str(detail_concurrency),
                "--headless",
                "true" if headless.startswith("숨김") else "false",
            ]
            start_task("task_org_meta", cmd, task_dir)
            st.rerun()

    st.divider()
    result = render_task_status("task_org_meta", "기관별 메타데이터 수집")
    if result:
        render_result_files(result, mode="org_meta")


def render_stats_tab() -> None:
    st.markdown('<div class="section-title">기관별 조회수/다운로드수 수집</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="hint-box">기관별 파일데이터 목록에서 데이터명, 조회수, 다운로드수, 상세 URL을 수집해 엑셀로 저장합니다.</div>',
        unsafe_allow_html=True,
    )

    org_name, _org_url = render_org_box("stats")
    max_pages, max_items, per_page = render_common_options("stats", default_pages=0, default_items=0, default_per_page=1000)

    st.divider()
    b1, b2 = st.columns([0.28, 0.72])
    with b1:
        start = st.button("조회수/다운로드수 수집 시작", type="primary", key="start_stats", use_container_width=True)
    with b2:
        st.caption("결과 파일: 공공데이터_FILE_조회수_다운로드.xlsx")

    if start:
        if not org_name:
            st.error("기관명을 입력해주세요.")
        else:
            task_dir = make_run_dir("stats", org_name)
            cmd = [
                sys.executable,
                "stats_runner.py",
                "--org-name",
                org_name,
                "--output-dir",
                str(task_dir / "result"),
                "--result-json",
                str(task_dir / "result.json"),
                "--max-pages",
                str(max_pages),
                "--max-items",
                str(max_items),
                "--list-per-page",
                str(per_page),
            ]
            start_task("task_stats", cmd, task_dir)
            st.rerun()

    st.divider()
    result = render_task_status("task_stats", "조회수/다운로드수 수집")
    if result:
        render_result_files(result, mode="stats")


def render_download_tab() -> None:
    st.markdown('<div class="section-title">기관별 최신/과거 파일데이터 다운로드</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="hint-box">기관명 기반으로 파일데이터 목록에 접근한 뒤, 상세페이지의 최신 다운로드와 과거데이터 모달 다운로드를 수행합니다.</div>',
        unsafe_allow_html=True,
    )

    org_name, org_url = render_org_box("download")

    with st.expander("다운로드 옵션", expanded=True):
        c1, c2, c3, c4 = st.columns(4)
        with c1:
            max_pages = st.number_input("최대 목록 페이지", min_value=0, value=0, step=1, key="download_pages", help="0이면 다음 페이지가 없을 때까지 진행합니다.")
        with c2:
            per_page = st.number_input("페이지당 요청 건수", min_value=10, max_value=1000, value=100, step=10, key="download_per_page")
        with c3:
            headless = st.selectbox("브라우저", ["숨김(headless)", "표시"], index=0, key="download_headless")
        with c4:
            auto_shutdown = st.checkbox("실행 프로세스 자동 종료", value=True, key="download_auto_shutdown", help="Streamlit 서버가 아니라 다운로드 runner subprocess만 종료됩니다.")

    st.divider()
    b1, b2 = st.columns([0.28, 0.72])
    with b1:
        start = st.button("최신/과거 파일 다운로드 시작", type="primary", key="start_download", use_container_width=True)
    with b2:
        st.caption("결과 파일: 기관별 다운로드 폴더 ZIP")

    if start:
        if not org_name:
            st.error("기관명을 입력해주세요.")
        else:
            task_dir = make_run_dir("download", org_name)
            cmd = [
                sys.executable,
                "download_runner.py",
                "--inst-name",
                org_name,
                "--org-url",
                org_url,
                "--output-dir",
                str(task_dir / "result"),
                "--result-json",
                str(task_dir / "result.json"),
                "--max-pages",
                str(int(max_pages)),
                "--per-page",
                str(int(per_page)),
                "--headless",
                "true" if headless.startswith("숨김") else "false",
                "--auto-shutdown",
                "true" if auto_shutdown else "false",
            ]
            start_task("task_download", cmd, task_dir)
            st.rerun()

    st.divider()
    result = render_task_status("task_download", "최신/과거 파일 다운로드")
    if result:
        render_result_files(result, mode="download")


def render_history_tab() -> None:
    st.markdown('<div class="section-title">실행 이력</div>', unsafe_allow_html=True)
    st.caption("최근 runs 폴더 기준으로 생성된 작업 결과를 확인합니다.")

    run_dirs = sorted([p for p in RUNS_DIR.glob("*") if p.is_dir()], key=lambda x: x.stat().st_mtime, reverse=True)[:30]
    if not run_dirs:
        st.info("실행 이력이 없습니다.")
        return

    rows = []
    for p in run_dirs:
        result_path = p / "result.json"
        result = read_result_json(result_path) or {}
        rows.append({
            "작업폴더": p.name,
            "상태": result.get("status", "실행중/미완료"),
            "기관명": result.get("org_name") or result.get("inst_name") or "-",
            "생성시각": datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
            "경로": str(p),
        })

    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    selected = st.selectbox("로그를 확인할 작업", [p.name for p in run_dirs], key="history_selected")
    selected_dir = next((p for p in run_dirs if p.name == selected), None)
    if selected_dir:
        with st.expander("선택 작업 로그", expanded=False):
            st.code(read_text(selected_dir / "run.log"), language="text")


# ==========================================================
# APP MAIN
# ==========================================================

render_header()

st.divider()

tab_all, tab_org_meta, tab_stats, tab_download, tab_history = st.tabs([
    "전체 메타데이터",
    "기관 메타데이터",
    "조회/다운로드 집계",
    "최신·과거 다운로드",
    "실행 이력",
])

with tab_all:
    render_all_metadata_tab()

with tab_org_meta:
    render_org_metadata_tab()

with tab_stats:
    render_stats_tab()

with tab_download:
    render_download_tab()

with tab_history:
    render_history_tab()

st.markdown(
    '<div class="footer-note">실행 전 로컬에서는 <code>pip install -r requirements.txt</code> 후 '
    '<code>python -m playwright install chromium</code>을 1회 실행하세요.</div>',
    unsafe_allow_html=True,
)
