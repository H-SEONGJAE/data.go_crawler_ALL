# -*- coding: utf-8 -*-
"""공공데이터 포털 크롤링 통합 Streamlit 앱 - 왼쪽 탭 UI 버전."""
from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

from portal_common import (
    build_file_list_url,
    build_keyword_search_url,
    build_url_for_selected_org,
    clean_filename,
    clean_text,
    describe_org_resolution_strategy,
    discover_org_candidates_by_keyword,
    file_size_label,
)

APP_DIR = Path(__file__).resolve().parent
RUNS_DIR = APP_DIR / "runs"
RUNS_DIR.mkdir(exist_ok=True)

st.set_page_config(
    page_title="공공데이터 포털 크롤링 통합",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ==========================================================
# UI STYLE
# ==========================================================

st.markdown(
    """
    <style>
    .block-container {
        padding-top: 1.2rem;
        padding-bottom: 3rem;
        max-width: 100% !important;
    }
    section[data-testid="stSidebar"] {
        width: 360px !important;
        min-width: 360px !important;
    }
    section[data-testid="stSidebar"] * {
        white-space: normal !important;
        overflow-wrap: anywhere !important;
        word-break: keep-all !important;
    }
    div[role="radiogroup"] label {
        min-height: 54px !important;
        padding: 0.55rem 0.65rem !important;
        border-radius: 13px !important;
        border: 1px solid #eceff3 !important;
        margin-bottom: 0.35rem !important;
        background: #ffffff !important;
    }
    div[role="radiogroup"] label:hover {
        background: #f6f8fa !important;
        border-color: #d0d7de !important;
    }
    .main-title {
        font-size: 2.05rem;
        font-weight: 850;
        letter-spacing: -0.04em;
        margin-bottom: 0.15rem;
        line-height: 1.2;
    }
    .sub-title {
        color: #5f6368;
        font-size: 1.0rem;
        margin-bottom: 1.0rem;
        line-height: 1.55;
    }
    .section-title {
        font-size: 1.25rem;
        font-weight: 780;
        margin: 0.2rem 0 0.65rem 0;
        letter-spacing: -0.025em;
    }
    .hint-box {
        border-left: 4px solid #d0d7de;
        background: #f6f8fa;
        border-radius: 10px;
        padding: 0.75rem 0.9rem;
        color: #444;
        font-size: 0.92rem;
        line-height: 1.65;
        white-space: normal;
        overflow-wrap: anywhere;
    }
    .url-box {
        word-break: break-all;
        overflow-wrap: anywhere;
        white-space: normal;
        border: 1px dashed #d0d7de;
        border-radius: 12px;
        padding: 0.75rem 0.85rem;
        background: #ffffff;
        color: #3f4650;
        font-size: 0.86rem;
        line-height: 1.5;
    }
    .candidate-box {
        border: 1px solid #e7e9ef;
        border-radius: 16px;
        padding: 0.85rem 0.95rem;
        background: #fbfcff;
        margin-bottom: 0.8rem;
    }
    .small-caption {
        color: #6a737d;
        font-size: 0.86rem;
        line-height: 1.5;
    }
    div[data-testid="stMetric"] {
        background: #ffffff;
        border: 1px solid #eceff3;
        border-radius: 16px;
        padding: 0.6rem 0.75rem;
    }
    .stDataFrame, div[data-testid="stDataFrame"] {
        width: 100% !important;
    }
    button[kind="primary"], .stButton > button {
        white-space: normal !important;
        height: auto !important;
        min-height: 2.7rem !important;
        line-height: 1.35 !important;
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
    try:
        log_file = task.get("log_file")
        if log_file:
            log_file.close()
    except Exception:
        pass


def read_text(path: str | Path, limit_chars: int = 36000) -> str:
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


def parse_progress_from_log(log_text: str, *, running: bool, elapsed: int, default_message: str = "작업 준비 중") -> tuple[int, str]:
    """로그에서 진행률과 현재 단계 메시지를 추정한다."""
    if not log_text:
        return (5 if running else 0), default_message

    # runner가 직접 남기는 표준 진행 로그 우선
    matches = re.findall(r"PROGRESS\|(\d{1,3})\|([^\n\r]+)", log_text)
    if matches:
        pct, msg = matches[-1]
        return max(0, min(100, int(pct))), clean_text(msg)

    # 메타데이터 상세 수집 로그: [⭐️DETAIL] 50/200 ...
    detail_matches = re.findall(r"\[[^\]]*DETAIL[^\]]*\]\s*(\d+)\s*/\s*(\d+)", log_text)
    if detail_matches:
        done, total = map(int, detail_matches[-1])
        if total > 0:
            pct = 20 + int((done / total) * 75)
            return max(20, min(98, pct)), f"상세 메타데이터 수집 중 · {done:,}/{total:,}건"

    # 목록 수집 로그: [LIST] page 01/90
    list_matches = re.findall(r"\[LIST\]\s*page\s*(\d+)\s*/\s*(\d+)", log_text)
    if list_matches:
        page, total = map(int, list_matches[-1])
        if total > 0:
            pct = 5 + int((page / total) * 15)
            return max(5, min(25, pct)), f"목록 URL 수집 중 · {page}/{total}페이지"

    # 다운로드 로그: 목록 페이지 번호
    download_page = re.findall(r"목록\s*페이지\s*(\d+)\s*처리\s*시작", log_text)
    if download_page:
        page = int(download_page[-1])
        pseudo = min(90, 10 + page * 8)
        return pseudo, f"파일 다운로드 중 · 목록 {page}페이지 처리"

    if "수집 완료" in log_text or "전체 완료" in log_text or "ZIP 저장 완료" in log_text:
        return 100, "작업 완료"

    if running:
        # 숫자 진행률이 없는 작업도 바가 멈춰 보이지 않도록 경과시간 기준으로 완만히 증가
        pseudo = min(85, 7 + elapsed // 3)
        return pseudo, default_message
    return 100, "작업 종료"


def render_task_status(key: str, title: str, *, default_message: str = "작업 실행 중", auto_refresh: bool = True) -> dict[str, Any] | None:
    task = st.session_state.get(key)
    if not task:
        st.info("아직 실행된 작업이 없습니다. 설정을 확인한 뒤 수집 시작 버튼을 눌러주세요.")
        return None

    proc = task.get("proc")
    result_json = Path(task["result_json"])
    log_path = Path(task["log_path"])
    running = proc is not None and proc.poll() is None
    elapsed = int(time.time() - float(task.get("started_at", time.time())))
    log_text = read_text(log_path)
    pct, msg = parse_progress_from_log(log_text, running=running, elapsed=elapsed, default_message=default_message)

    st.markdown('<div class="section-title">크롤링 진행상황</div>', unsafe_allow_html=True)
    st.progress(pct / 100, text=f"{pct}% · {msg}")

    status_col, action_col = st.columns([0.76, 0.24])
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

    with st.expander("실행 로그 보기", expanded=running):
        st.code(log_text or "로그가 아직 없습니다.", language="text")

    result = read_result_json(result_json)
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


def render_result_files(result: dict[str, Any] | None, *, mode: str) -> None:
    if not result:
        return
    st.markdown('<div class="section-title">결과 다운로드</div>', unsafe_allow_html=True)
    if result.get("status") == "failed":
        st.error(result.get("error", "실패 원인을 result.json에서 확인하세요."))
        download_button(result.get("error_path"), "오류 로그 다운로드", f"{mode}_error")
        return
    if mode == "metadata":
        c1, c2 = st.columns(2)
        with c1:
            download_button(result.get("metadata_path"), "메타데이터.xlsx", f"{mode}_meta")
        with c2:
            download_button(result.get("fail_path"), "실패로그.xlsx", f"{mode}_fail")
    elif mode == "stats":
        download_button(result.get("excel_path"), "조회수/다운로드수 Excel", f"{mode}_excel")
        path = result.get("excel_path")
        if path and Path(path).exists():
            try:
                df = pd.read_excel(path)
                st.dataframe(df.head(100), use_container_width=True, hide_index=True)
            except Exception:
                pass
    elif mode == "download":
        download_button(result.get("zip_path"), "최신·과거 파일 ZIP", f"{mode}_zip")


# ==========================================================
# PROVIDER SELECTOR
# ==========================================================


def render_provider_selector(prefix: str, *, default_keyword: str = "", per_page: int = 1000) -> tuple[str, str] | tuple[None, None]:
    """기관명 입력 → 포털 제공기관 후보 조회 → 사용자가 직접 선택."""
    st.markdown('<div class="section-title">제공기관 선택</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="hint-box">기관명은 코드가 임의로 보정하지 않습니다. 입력한 키워드로 포털 파일데이터를 검색한 뒤, 실제 검색 결과에 표시된 제공기관 후보를 가져와 사용자가 직접 선택합니다.</div>',
        unsafe_allow_html=True,
    )

    q = st.text_input(
        "기관 검색어",
        value=st.session_state.get(f"{prefix}_keyword", default_keyword),
        placeholder="예: 강원도 고성군, 한국남동발전, 에스알, 중구청",
        key=f"{prefix}_keyword_input",
    )
    st.session_state[f"{prefix}_keyword"] = q

    with st.expander("후보 탐색 방식 보기", expanded=False):
        st.write(describe_org_resolution_strategy(q))
        st.markdown("검색 URL 미리보기")
        st.markdown(f'<div class="url-box">{build_keyword_search_url(q or "기관명", current_page=1, per_page=100)}</div>', unsafe_allow_html=True)

    c1, c2, c3 = st.columns([0.28, 0.28, 0.44])
    with c1:
        search_clicked = st.button("제공기관 후보 조회", key=f"{prefix}_search", type="primary", use_container_width=True)
    with c2:
        clear_clicked = st.button("후보 초기화", key=f"{prefix}_clear_candidates", use_container_width=True)
    with c3:
        st.caption("후보가 여러 개이면 자동 선택하지 않고 아래에서 직접 선택합니다.")

    if clear_clicked:
        st.session_state.pop(f"{prefix}_candidates", None)
        st.session_state.pop(f"{prefix}_selected_provider", None)
        st.rerun()

    if search_clicked:
        if not clean_text(q):
            st.warning("기관 검색어를 입력해주세요.")
        else:
            with st.spinner("포털 검색 결과에서 제공기관 후보를 찾는 중..."):
                rows = discover_org_candidates_by_keyword(q, max_pages=2, per_page=100)
            st.session_state[f"{prefix}_candidates"] = rows
            if rows:
                st.success(f"제공기관 후보 {len(rows)}개를 찾았습니다. 실제 수집할 기관을 선택해주세요.")
            else:
                st.warning("포털 검색 결과에서 제공기관 후보를 찾지 못했습니다. 검색어를 더 정확히 입력하거나 직접 URL 사용 옵션을 이용하세요.")

    rows = st.session_state.get(f"{prefix}_candidates") or []
    if rows:
        preview_df = pd.DataFrame(rows)
        show_cols = [c for c in ["provider", "count", "score", "samples", "sources"] if c in preview_df.columns]
        st.dataframe(
            preview_df[show_cols].rename(columns={
                "provider": "제공기관 후보",
                "count": "검색결과 내 발견수",
                "score": "정렬점수",
                "samples": "예시 데이터명",
                "sources": "추출위치",
            }),
            use_container_width=True,
            hide_index=True,
        )
        options = [clean_text(r["provider"]) for r in rows]
        selected = st.selectbox(
            "실제 수집할 제공기관 선택",
            options=options,
            key=f"{prefix}_selected_provider",
        )
        target_url = build_url_for_selected_org(selected, per_page=per_page, d_type="")
        st.markdown("선택 기관 기준 수집 URL")
        st.markdown(f'<div class="url-box">{target_url}</div>', unsafe_allow_html=True)
        return selected, target_url

    with st.expander("후보가 안 나올 때만: 입력값 그대로 기관 URL 생성", expanded=False):
        st.warning("이 옵션은 포털 검색 결과 후보를 선택하지 못할 때만 사용하세요. 동일/유사 기관명이 있으면 잘못된 결과가 나올 수 있습니다.")
        use_raw = st.checkbox("입력값 그대로 사용", key=f"{prefix}_use_raw")
        if use_raw and clean_text(q):
            target_url = build_url_for_selected_org(q, per_page=per_page, d_type="")
            st.markdown(f'<div class="url-box">{target_url}</div>', unsafe_allow_html=True)
            return clean_text(q), target_url

    return None, None


# ==========================================================
# SIDEBAR LEFT TABS
# ==========================================================

st.sidebar.markdown("## 📊 포털 크롤링 통합")
st.sidebar.caption("왼쪽 탭에서 기능을 선택하세요. 기관명은 먼저 후보 조회 후 실제 제공기관을 선택하는 방식입니다.")
PAGE_OPTIONS = [
    "① 전체 메타데이터",
    "② 기관 메타데이터",
    "③ 조회·다운로드 집계",
    "④ 최신·과거 다운로드",
    "⑤ 실행 이력",
]
page = st.sidebar.radio("기능 탭", PAGE_OPTIONS, label_visibility="collapsed")
st.sidebar.divider()
st.sidebar.markdown("### 진행 방식")
st.sidebar.write("1. 기관 검색어 입력")
st.sidebar.write("2. 제공기관 후보 조회")
st.sidebar.write("3. 실제 제공기관 선택")
st.sidebar.write("4. 수집 시작")
st.sidebar.write("5. 진행 바와 로그 확인")

st.markdown('<div class="main-title">공공데이터 포털 크롤링 통합 홈페이지</div>', unsafe_allow_html=True)
st.markdown(
    '<div class="sub-title">메타데이터 수집, 기관별 조회수·다운로드수 집계, 최신·과거 파일 다운로드를 한 화면에서 실행합니다.</div>',
    unsafe_allow_html=True,
)

# ==========================================================
# PAGE 1: ALL METADATA
# ==========================================================

if page == "① 전체 메타데이터":
    st.markdown('<div class="section-title">전체 파일데이터 메타데이터 수집</div>', unsafe_allow_html=True)
    st.markdown('<div class="hint-box">공공데이터포털의 전체 파일데이터 목록을 대상으로 상세 메타데이터를 수집합니다. 전체 수집은 시간이 오래 걸릴 수 있으므로 먼저 페이지/건수 제한으로 테스트하는 것을 권장합니다.</div>', unsafe_allow_html=True)

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        max_pages = st.number_input("최대 목록 페이지", min_value=0, value=1, step=1, help="0이면 제한 없음")
    with col2:
        max_items = st.number_input("최대 상세 건수", min_value=0, value=50, step=10, help="0이면 제한 없음")
    with col3:
        list_per_page = st.number_input("목록 perPage", min_value=10, max_value=1000, value=1000, step=10)
    with col4:
        detail_concurrency = st.number_input("상세 동시 처리", min_value=1, max_value=50, value=20, step=1)

    with st.expander("고급 옵션", expanded=False):
        run_mode = st.selectbox("실행 모드", ["MAIN", "BOTH", "RETRY_FAILED"], index=0)
        headless = st.checkbox("브라우저 숨김 실행", value=True)
        both_wait_sec = st.number_input("BOTH 모드 재수집 대기초", min_value=0, value=180, step=30)

    if st.button("전체 메타데이터 수집 시작", type="primary", use_container_width=True):
        task_dir = make_run_dir("metadata_all", "전체")
        cmd = [
            sys.executable, "-u", "metadata_runner.py",
            "--scope", "all",
            "--output-dir", str(task_dir),
            "--result-json", str(task_dir / "result.json"),
            "--max-pages", str(int(max_pages)),
            "--max-items", str(int(max_items)),
            "--list-per-page", str(int(list_per_page)),
            "--detail-concurrency", str(int(detail_concurrency)),
            "--run-mode", run_mode,
            "--headless", "true" if headless else "false",
            "--both-wait-sec", str(int(both_wait_sec)),
        ]
        start_task("task_meta_all", cmd, task_dir)
        st.rerun()

    result = render_task_status("task_meta_all", "전체 메타데이터 수집", default_message="전체 메타데이터 수집 중")
    render_result_files(result, mode="metadata")

# ==========================================================
# PAGE 2: ORG METADATA
# ==========================================================

elif page == "② 기관 메타데이터":
    selected_org, target_url = render_provider_selector("meta_org", per_page=1000)
    st.divider()
    st.markdown('<div class="section-title">기관별 메타데이터 수집 설정</div>', unsafe_allow_html=True)
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        max_pages = st.number_input("최대 목록 페이지", min_value=0, value=0, step=1, help="0이면 제한 없음", key="meta_org_pages")
    with col2:
        max_items = st.number_input("최대 상세 건수", min_value=0, value=0, step=10, help="0이면 제한 없음", key="meta_org_items")
    with col3:
        list_per_page = st.number_input("목록 perPage", min_value=10, max_value=1000, value=1000, step=10, key="meta_org_per")
    with col4:
        detail_concurrency = st.number_input("상세 동시 처리", min_value=1, max_value=50, value=20, step=1, key="meta_org_con")

    with st.expander("고급 옵션", expanded=False):
        run_mode = st.selectbox("실행 모드", ["MAIN", "BOTH", "RETRY_FAILED"], index=0, key="meta_org_mode")
        headless = st.checkbox("브라우저 숨김 실행", value=True, key="meta_org_headless")
        both_wait_sec = st.number_input("BOTH 모드 재수집 대기초", min_value=0, value=180, step=30, key="meta_org_wait")

    disabled = not (selected_org and target_url)
    if st.button("선택 기관 메타데이터 수집 시작", type="primary", disabled=disabled, use_container_width=True):
        task_dir = make_run_dir("metadata_org", selected_org or "기관")
        cmd = [
            sys.executable, "-u", "metadata_runner.py",
            "--scope", "org",
            "--org-name", selected_org,
            "--target-url", target_url,
            "--output-dir", str(task_dir),
            "--result-json", str(task_dir / "result.json"),
            "--max-pages", str(int(max_pages)),
            "--max-items", str(int(max_items)),
            "--list-per-page", str(int(list_per_page)),
            "--detail-concurrency", str(int(detail_concurrency)),
            "--run-mode", run_mode,
            "--headless", "true" if headless else "false",
            "--both-wait-sec", str(int(both_wait_sec)),
        ]
        start_task("task_meta_org", cmd, task_dir)
        st.rerun()

    result = render_task_status("task_meta_org", "기관별 메타데이터 수집", default_message="기관별 메타데이터 수집 중")
    render_result_files(result, mode="metadata")

# ==========================================================
# PAGE 3: STATS
# ==========================================================

elif page == "③ 조회·다운로드 집계":
    selected_org, target_url = render_provider_selector("stats_org", per_page=1000)
    st.divider()
    st.markdown('<div class="section-title">조회수·다운로드수 집계 설정</div>', unsafe_allow_html=True)
    col1, col2, col3 = st.columns(3)
    with col1:
        max_pages = st.number_input("최대 목록 페이지", min_value=0, value=0, step=1, key="stats_pages", help="0이면 제한 없음")
    with col2:
        max_items = st.number_input("최대 수집 건수", min_value=0, value=0, step=10, key="stats_items", help="0이면 제한 없음")
    with col3:
        list_per_page = st.number_input("목록 perPage", min_value=10, max_value=1000, value=1000, step=10, key="stats_per")
    headless = st.checkbox("브라우저 숨김 실행", value=True, key="stats_headless")

    disabled = not (selected_org and target_url)
    if st.button("조회수·다운로드수 수집 시작", type="primary", disabled=disabled, use_container_width=True):
        task_dir = make_run_dir("stats", selected_org or "기관")
        cmd = [
            sys.executable, "-u", "stats_runner.py",
            "--org-name", selected_org,
            "--target-url", target_url,
            "--output-dir", str(task_dir),
            "--result-json", str(task_dir / "result.json"),
            "--max-pages", str(int(max_pages)),
            "--max-items", str(int(max_items)),
            "--list-per-page", str(int(list_per_page)),
            "--headless", "true" if headless else "false",
        ]
        start_task("task_stats", cmd, task_dir)
        st.rerun()

    result = render_task_status("task_stats", "조회수·다운로드수 수집", default_message="목록 집계 수집 중")
    render_result_files(result, mode="stats")

# ==========================================================
# PAGE 4: DOWNLOAD
# ==========================================================

elif page == "④ 최신·과거 다운로드":
    selected_org, target_url = render_provider_selector("download_org", per_page=100)
    st.divider()
    st.markdown('<div class="section-title">최신·과거 파일 다운로드 설정</div>', unsafe_allow_html=True)
    col1, col2 = st.columns(2)
    with col1:
        max_pages = st.number_input("최대 목록 페이지", min_value=0, value=0, step=1, key="download_pages", help="0이면 제한 없음")
    with col2:
        per_page = st.number_input("목록 perPage", min_value=10, max_value=1000, value=100, step=10, key="download_per")
    col3, col4 = st.columns(2)
    with col3:
        headless = st.checkbox("브라우저 숨김 실행", value=True, key="download_headless")
    with col4:
        auto_shutdown = st.checkbox("수집 종료 후 실행 프로세스 자동 종료", value=True, key="download_shutdown")

    disabled = not (selected_org and target_url)
    if st.button("최신·과거 파일 다운로드 시작", type="primary", disabled=disabled, use_container_width=True):
        task_dir = make_run_dir("download", selected_org or "기관")
        # perPage를 바꿨다면 선택 URL도 그 perPage로 다시 생성
        selected_url = build_url_for_selected_org(selected_org, per_page=int(per_page), d_type="")
        cmd = [
            sys.executable, "-u", "download_runner.py",
            "--inst-name", selected_org,
            "--org-url", selected_url,
            "--output-dir", str(task_dir),
            "--result-json", str(task_dir / "result.json"),
            "--max-pages", str(int(max_pages)),
            "--per-page", str(int(per_page)),
            "--headless", "true" if headless else "false",
            "--auto-shutdown", "true" if auto_shutdown else "false",
        ]
        start_task("task_download", cmd, task_dir)
        st.rerun()

    result = render_task_status("task_download", "최신·과거 파일 다운로드", default_message="파일 다운로드 수집 중")
    render_result_files(result, mode="download")

# ==========================================================
# PAGE 5: HISTORY
# ==========================================================

else:
    st.markdown('<div class="section-title">실행 이력</div>', unsafe_allow_html=True)
    run_dirs = sorted([p for p in RUNS_DIR.glob("*") if p.is_dir()], key=lambda p: p.stat().st_mtime, reverse=True)
    if not run_dirs:
        st.info("아직 실행 이력이 없습니다.")
    else:
        rows = []
        for p in run_dirs:
            result = read_result_json(p / "result.json") or {}
            rows.append({
                "작업폴더": p.name,
                "상태": result.get("status", "실행중/결과없음"),
                "기관명": result.get("org_name") or result.get("inst_name") or "",
                "수정시각": datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
                "경로": str(p),
            })
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        selected = st.selectbox("상세 확인할 실행 폴더", [p.name for p in run_dirs])
        target = next(p for p in run_dirs if p.name == selected)
        st.markdown(f'<div class="url-box">{target}</div>', unsafe_allow_html=True)
        log_text = read_text(target / "run.log")
        st.code(log_text or "로그 없음", language="text")
        result = read_result_json(target / "result.json")
        if result:
            st.json(result)
            if result.get("metadata_path"):
                render_result_files(result, mode="metadata")
            elif result.get("excel_path"):
                render_result_files(result, mode="stats")
            elif result.get("zip_path"):
                render_result_files(result, mode="download")
