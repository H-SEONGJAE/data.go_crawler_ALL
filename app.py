# -*- coding: utf-8 -*-
"""
app.py

공공데이터포털 통합 크롤러 Streamlit UI
- 기관명 부분 검색 → 기관 후보 선택 → URL/상세URL 교차 검증
- 검증된 resolution_json을 기준으로 메타데이터/조회수/파일다운로드 실행
- Streamlit UI는 macOS/Figma 스타일을 참고하되 글씨 잘림 방지를 최우선으로 구성
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, List

import pandas as pd
import streamlit as st

from org_resolver import resolve_org_filedata, search_org_candidates

APP_DIR = Path(__file__).resolve().parent
OUTPUT_ROOT = APP_DIR / "outputs"
TASK_ROOT = OUTPUT_ROOT / "tasks"
RESOLUTION_DIR = OUTPUT_ROOT / "resolutions"
for p in [OUTPUT_ROOT, TASK_ROOT, RESOLUTION_DIR]:
    p.mkdir(parents=True, exist_ok=True)


# ==========================================================
# UI 스타일: macOS/Figma 톤 + 글자 잘림 방지
# ==========================================================
st.set_page_config(page_title="공공데이터 통합 크롤러", page_icon="🗂️", layout="wide")

st.markdown(
    """
    <style>
    :root {
        --app-bg: #F5F5F7;
        --card-bg: rgba(255,255,255,0.92);
        --text-main: #1D1D1F;
        --text-sub: #52525B;
        --border: rgba(0,0,0,0.08);
        --blue: #0A84FF;
        --green: #34C759;
        --red: #FF453A;
        --orange: #FF9F0A;
    }

    .stApp {
        background: radial-gradient(circle at top left, #FFFFFF 0, #F5F5F7 36%, #ECECF1 100%);
        color: var(--text-main);
    }

    /* 모든 텍스트 잘림 방지 */
    .stMarkdown, .stMarkdown *, .stAlert, .stAlert *, label, p, span, div {
        white-space: normal !important;
        overflow-wrap: anywhere !important;
        word-break: keep-all !important;
        text-overflow: clip !important;
    }

    .main .block-container {
        padding-top: 1.6rem;
        padding-bottom: 3rem;
        max-width: 1440px;
    }

    .hero-card {
        background: linear-gradient(135deg, rgba(255,255,255,0.96), rgba(245,245,247,0.88));
        border: 1px solid var(--border);
        border-radius: 28px;
        padding: 28px 32px;
        box-shadow: 0 22px 70px rgba(0,0,0,0.08);
        margin-bottom: 18px;
    }

    .hero-title {
        font-size: clamp(28px, 4vw, 42px);
        line-height: 1.15;
        font-weight: 800;
        letter-spacing: -0.04em;
        color: var(--text-main);
        margin: 0 0 10px 0;
    }

    .hero-subtitle {
        font-size: clamp(14px, 1.8vw, 17px);
        line-height: 1.65;
        color: var(--text-sub);
        margin: 0;
    }

    .mac-card {
        background: var(--card-bg);
        border: 1px solid var(--border);
        border-radius: 22px;
        padding: 20px 22px;
        box-shadow: 0 12px 36px rgba(0,0,0,0.06);
        margin-bottom: 16px;
        overflow: visible !important;
        min-height: auto !important;
    }

    .section-title {
        font-size: 20px;
        font-weight: 760;
        letter-spacing: -0.02em;
        color: var(--text-main);
        margin-bottom: 8px;
    }

    .section-desc {
        font-size: 14.5px;
        line-height: 1.65;
        color: var(--text-sub);
        margin-bottom: 12px;
    }

    .status-pill {
        display: inline-flex;
        align-items: center;
        gap: 6px;
        border-radius: 999px;
        padding: 6px 10px;
        font-size: 13px;
        line-height: 1.3;
        font-weight: 650;
        border: 1px solid var(--border);
        margin: 3px 4px 3px 0;
        max-width: 100%;
    }
    .pill-ok { background: rgba(52,199,89,0.12); color: #177D35; }
    .pill-warn { background: rgba(255,159,10,0.13); color: #9A5A00; }
    .pill-info { background: rgba(10,132,255,0.12); color: #0057B8; }

    div.stButton > button, div.stDownloadButton > button {
        min-height: 42px !important;
        height: auto !important;
        white-space: normal !important;
        border-radius: 12px !important;
        font-weight: 700 !important;
        padding: 0.65rem 0.9rem !important;
    }

    div[data-baseweb="input"], div[data-baseweb="select"] {
        min-height: 42px !important;
    }

    textarea, input {
        font-size: 15px !important;
    }

    .stTabs [data-baseweb="tab-list"] {
        gap: 8px;
        flex-wrap: wrap;
    }
    .stTabs [data-baseweb="tab"] {
        min-height: 42px;
        height: auto;
        border-radius: 12px;
        padding: 8px 12px;
        white-space: normal;
    }

    .code-wrap {
        font-size: 13px;
        line-height: 1.5;
        background: #111827;
        color: #E5E7EB;
        border-radius: 16px;
        padding: 14px 16px;
        overflow-x: auto;
        white-space: pre-wrap;
        word-break: break-all;
    }

    @media (max-width: 900px) {
        .hero-card { padding: 22px 20px; border-radius: 22px; }
        .mac-card { padding: 18px 16px; border-radius: 18px; }
    }
    </style>
    """,
    unsafe_allow_html=True,
)


# ==========================================================
# 공통 함수
# ==========================================================
def safe_name(value: str) -> str:
    value = str(value or "unnamed").strip()
    for ch in '\\/:*?"<>|':
        value = value.replace(ch, "_")
    return value or "unnamed"


def python_cmd(script: str, *args: str) -> List[str]:
    return [sys.executable, str(APP_DIR / script), *map(str, args)]


def create_task_dir(prefix: str, org: str) -> Path:
    ts = time.strftime("%Y%m%d_%H%M%S")
    path = TASK_ROOT / f"{prefix}_{safe_name(org)}_{ts}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def start_task(state_key: str, cmd: List[str], task_dir: Path):
    log_path = task_dir / "run.log"
    result_json = task_dir / "result.json"
    log_fp = open(log_path, "w", encoding="utf-8", buffering=1)
    proc = subprocess.Popen(
        cmd,
        cwd=str(APP_DIR),
        stdout=log_fp,
        stderr=subprocess.STDOUT,
        text=True,
    )
    st.session_state[state_key] = {
        "pid": proc.pid,
        "cmd": cmd,
        "task_dir": str(task_dir),
        "log_path": str(log_path),
        "result_json": str(result_json),
        "started_at": time.time(),
    }


def process_running(pid: int) -> bool:
    if not pid:
        return False
    if os.name == "nt":
        import ctypes
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, int(pid))
        if handle == 0:
            return False
        exit_code = ctypes.c_ulong()
        kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
        kernel32.CloseHandle(handle)
        return exit_code.value == 259
    else:
        try:
            os.kill(int(pid), 0)
            return True
        except OSError:
            return False


def read_text_tail(path: str | Path, max_chars: int = 12000) -> str:
    p = Path(path)
    if not p.exists():
        return ""
    text = p.read_text(encoding="utf-8", errors="replace")
    return text[-max_chars:]


def load_json_if_exists(path: str | Path) -> Dict:
    p = Path(path)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def render_task_panel(state_key: str, title: str) -> Dict:
    task = st.session_state.get(state_key)
    if not task:
        return {}

    running = process_running(task.get("pid"))
    result = load_json_if_exists(task.get("result_json"))
    log_text = read_text_tail(task.get("log_path"), max_chars=16000)

    st.markdown(f"<div class='mac-card'><div class='section-title'>{title}</div>", unsafe_allow_html=True)
    if running:
        st.info("실행 중입니다. 새로고침 없이 아래 로그가 갱신됩니다. 필요하면 [진행상황 새로고침]을 누르세요.")
    elif result:
        st.success("실행이 완료되었습니다.")
    else:
        st.warning("프로세스는 종료되었지만 result.json이 없습니다. 로그를 확인해주세요.")

    col_a, col_b = st.columns([1, 4])
    with col_a:
        if st.button("진행상황 새로고침", use_container_width=True, key=f"refresh_{state_key}"):
            st.rerun()
    with col_b:
        st.caption(f"PID: {task.get('pid')} · 작업 폴더: {task.get('task_dir')}")

    if log_text:
        with st.expander("실행 로그 보기", expanded=running):
            st.code(log_text, language="text")
    st.markdown("</div>", unsafe_allow_html=True)
    return result


def download_file_button(path: str, label: str, mime: str, key: str):
    if not path:
        return
    p = Path(path)
    if not p.exists():
        st.warning(f"파일을 찾지 못했습니다: {p}")
        return
    st.download_button(
        label=label,
        data=p.read_bytes(),
        file_name=p.name,
        mime=mime,
        use_container_width=True,
        key=key,
    )


def current_resolution_path() -> str:
    return st.session_state.get("resolution_json", "")


def current_resolution() -> Dict:
    path = current_resolution_path()
    return load_json_if_exists(path) if path else {}


# ==========================================================
# 헤더
# ==========================================================
st.markdown(
    """
    <div class="hero-card">
      <div class="hero-title">공공데이터포털 통합 크롤러</div>
      <p class="hero-subtitle">
        기관명을 직접 URL로 조립하지 않고, 포털 화면에서 실제 검색·기관 선택·파일데이터 목록 검증·상세페이지 메타테이블 검증을 거친 뒤 수집을 실행합니다.
        Figma/macOS 스타일의 넓은 카드형 UI를 적용했고, 긴 문구와 URL이 잘리지 않도록 구성했습니다.
      </p>
    </div>
    """,
    unsafe_allow_html=True,
)


# ==========================================================
# 1. 기관 검색/URL 검증 공통 영역
# ==========================================================
st.markdown("<div class='mac-card'>", unsafe_allow_html=True)
st.markdown("<div class='section-title'>1. 기관 검색 및 파일데이터 URL 교차 검증</div>", unsafe_allow_html=True)
st.markdown(
    "<div class='section-desc'>풀네임을 몰라도 일부 기관명을 입력하면 포털 검색 결과에서 제공기관 후보를 수집합니다. 후보를 선택하면 상세페이지 제공기관 링크와 파일데이터 목록, 상세 URL 샘플을 교차 검증합니다.</div>",
    unsafe_allow_html=True,
)

col1, col2, col3 = st.columns([4, 1.2, 1.2], vertical_alignment="bottom")
with col1:
    keyword = st.text_input("기관명 또는 기관명 일부", placeholder="예: 중부발전, 한국중부, 서울특별시", key="resolver_keyword")
with col2:
    scan_pages = st.number_input("후보 검색 페이지", min_value=1, max_value=10, value=3, step=1)
with col3:
    headless = st.checkbox("브라우저 숨김", value=True, key="resolver_headless")

if st.button("기관 후보 검색", type="primary", use_container_width=True):
    if not keyword.strip():
        st.error("기관명 또는 일부 키워드를 입력해주세요.")
    else:
        with st.spinner("포털 화면에서 실제 검색을 수행하고 기관 후보를 수집 중입니다..."):
            try:
                result = search_org_candidates(keyword.strip(), max_scan_pages=int(scan_pages), headless=headless)
                st.session_state["candidate_result"] = result
                st.session_state.pop("resolution_json", None)
                st.success(f"후보 검색 완료: {len(result.get('candidates', []))}개 기관 후보")
            except Exception as e:
                st.error(f"기관 후보 검색 실패: {e}")

candidate_result = st.session_state.get("candidate_result", {})
candidates = candidate_result.get("candidates", [])

if candidate_result:
    for msg in candidate_result.get("messages", []):
        st.markdown(f"<span class='status-pill pill-info'>ℹ {msg}</span>", unsafe_allow_html=True)

if candidates:
    option_labels = [
        f"{c.get('provider')} · 검색페이지 확인 {c.get('count_on_scanned_pages', 0)}건 · 예시: {c.get('sample_title', '')[:80]}"
        for c in candidates
    ]
    selected_idx = st.selectbox("검색된 기관 후보 선택", options=list(range(len(option_labels))), format_func=lambda i: option_labels[i])
    selected_candidate = candidates[selected_idx]

    colr1, colr2, colr3 = st.columns([2, 1, 1], vertical_alignment="bottom")
    with colr1:
        max_pages = st.number_input("선택 기관 전체 순회 페이지 수", min_value=1, max_value=500, value=90, step=1)
    with colr2:
        st.caption("상세 URL/제공기관명/메타테이블을 검증합니다.")
    with colr3:
        resolve_clicked = st.button("선택 기관 URL 검증", type="primary", use_container_width=True)

    if resolve_clicked:
        with st.spinner("선택 기관의 실제 파일데이터 목록 URL과 상세 URL 샘플을 교차 검증 중입니다..."):
            try:
                resolution = resolve_org_filedata(
                    keyword=keyword.strip(),
                    selected_provider=selected_candidate.get("provider", ""),
                    max_pages=int(max_pages),
                    headless=headless,
                    seed_detail_url=selected_candidate.get("sample_detail_url", ""),
                    provider_url_hint=selected_candidate.get("provider_url_from_detail", ""),
                )
                safe_org = safe_name(resolution.selected_provider)
                res_path = RESOLUTION_DIR / f"{safe_org}_{time.strftime('%Y%m%d_%H%M%S')}.json"
                resolution.save(res_path)
                st.session_state["resolution_json"] = str(res_path)
                st.success("기관 URL 교차 검증 완료")
            except Exception as e:
                st.error(f"기관 URL 검증 실패: {e}")
elif candidate_result:
    st.warning("기관 후보를 찾지 못했습니다. 검색어를 조금 더 넓게 입력하거나 포털 화면 구조 변경 여부를 확인해주세요.")

resolution = current_resolution()
if resolution:
    st.markdown("---")
    st.markdown("<div class='section-title'>검증 완료 결과</div>", unsafe_allow_html=True)
    st.markdown(f"<span class='status-pill pill-ok'>✅ 상태: {resolution.get('validation_status')}</span>", unsafe_allow_html=True)
    st.markdown(f"<span class='status-pill pill-info'>기관: {resolution.get('selected_provider')}</span>", unsafe_allow_html=True)
    st.markdown(f"<span class='status-pill pill-info'>상세 URL 수: {resolution.get('total_items_collected')}</span>", unsafe_allow_html=True)

    for msg in resolution.get("validation_messages", []):
        st.markdown(f"<span class='status-pill pill-info'>ℹ {msg}</span>", unsafe_allow_html=True)

    with st.expander("검증된 URL/상세 URL 샘플 보기", expanded=False):
        st.markdown("**resolved_url**")
        st.code(resolution.get("resolved_url", ""), language="text")
        if resolution.get("provider_url_from_detail"):
            st.markdown("**provider_url_from_detail**")
            st.code(resolution.get("provider_url_from_detail", ""), language="text")
        st.markdown("**sample_detail_urls**")
        st.code("\n".join(resolution.get("sample_detail_urls", [])), language="text")
        st.markdown("**resolution_json**")
        st.code(current_resolution_path(), language="text")

st.markdown("</div>", unsafe_allow_html=True)


# ==========================================================
# 2. 기능 실행 영역
# ==========================================================
st.markdown("<div class='mac-card'>", unsafe_allow_html=True)
st.markdown("<div class='section-title'>2. 수집 기능 실행</div>", unsafe_allow_html=True)
st.markdown("<div class='section-desc'>아래 기능은 모두 위에서 교차 검증한 resolution_json을 기준으로 실행됩니다. URL 입력창을 별도로 사용하지 않습니다.</div>", unsafe_allow_html=True)

if not resolution:
    st.warning("먼저 기관 후보 검색과 URL 검증을 완료해주세요.")

meta_tab, stats_tab, download_tab = st.tabs(["메타데이터 크롤링", "조회수/다운로드 수", "파일데이터 다운로드"])

with meta_tab:
    st.markdown("<div class='section-title'>메타데이터 크롤링</div>", unsafe_allow_html=True)
    st.caption("Resolver가 확보한 상세 URL 목록을 기존 crawler_metadata.py의 상세 수집 엔진에 직접 전달합니다.")
    colm1, colm2 = st.columns([1, 1])
    with colm1:
        detail_concurrency = st.number_input("상세 수집 동시 처리 수", min_value=1, max_value=50, value=20, step=1)
    with colm2:
        make_zip = st.checkbox("결과 ZIP 생성", value=False, key="meta_make_zip")

    if st.button("메타데이터 수집 시작", type="primary", use_container_width=True, disabled=not bool(resolution)):
        org = resolution.get("selected_provider") or "기관"
        task_dir = create_task_dir("metadata", org)
        result_json = task_dir / "result.json"
        cmd = python_cmd(
            "metadata_runner.py",
            "--resolution-json", current_resolution_path(),
            "--output-dir", str(task_dir / "result"),
            "--result-json", str(result_json),
            "--detail-concurrency", str(int(detail_concurrency)),
            "--make-zip", "true" if make_zip else "false",
        )
        start_task("task_metadata", cmd, task_dir)
        st.rerun()

    meta_result = render_task_panel("task_metadata", "메타데이터 수집 진행상황")
    if meta_result:
        download_file_button(meta_result.get("metadata_path", ""), "📥 메타데이터.xlsx 다운로드", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "meta_xlsx")
        download_file_button(meta_result.get("fail_path", ""), "📥 실패로그.xlsx 다운로드", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "meta_fail")
        download_file_button(meta_result.get("zip_path", ""), "📥 메타데이터 ZIP 다운로드", "application/zip", "meta_zip")

with stats_tab:
    st.markdown("<div class='section-title'>조회수 및 다운로드 수</div>", unsafe_allow_html=True)
    st.caption("1순위로 기존 crawler.py에 검증 URL을 전달하고, 실패 시 Resolver의 목록 카드 값으로 fallback 저장합니다.")

    if st.button("조회수/다운로드 수 수집 시작", type="primary", use_container_width=True, disabled=not bool(resolution)):
        org = resolution.get("selected_provider") or "기관"
        task_dir = create_task_dir("stats", org)
        result_json = task_dir / "result.json"
        cmd = python_cmd(
            "stats_runner.py",
            "--resolution-json", current_resolution_path(),
            "--output-dir", str(task_dir / "result"),
            "--result-json", str(result_json),
        )
        start_task("task_stats", cmd, task_dir)
        st.rerun()

    stats_result = render_task_panel("task_stats", "조회수/다운로드 수 수집 진행상황")
    if stats_result:
        st.info(f"수집 방식: {stats_result.get('mode')} · rows: {stats_result.get('rows')}")
        download_file_button(stats_result.get("excel_path", ""), "📥 조회수_다운로드수.xlsx 다운로드", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "stats_xlsx")

with download_tab:
    st.markdown("<div class='section-title'>기관별 파일데이터 다운로드</div>", unsafe_allow_html=True)
    st.caption("crawler_data_integrated.py가 검증 URL로 진입합니다. 이미 파일데이터 목록이면 탭 클릭을 생략하고, 기관 페이지이면 파일데이터 탭을 클릭합니다.")
    cdl1, cdl2 = st.columns([1, 1])
    with cdl1:
        dl_headless = st.checkbox("다운로드 브라우저 숨김 실행", value=True, key="download_headless")
    with cdl2:
        download_max_pages = st.number_input("최대 페이지 수(0=전체)", min_value=0, max_value=10000, value=0, step=1, key="download_max_pages")
    browser_path = st.text_input("브라우저 실행 파일 경로 선택 입력", placeholder="비워두면 Playwright 기본 Chromium 사용", key="download_browser_path")

    if st.button("파일데이터 다운로드 시작", type="primary", use_container_width=True, disabled=not bool(resolution)):
        org = resolution.get("selected_provider") or "기관"
        task_dir = create_task_dir("download", org)
        result_json = task_dir / "result.json"
        cmd = python_cmd(
            "download_runner.py",
            "--resolution-json", current_resolution_path(),
            "--output-dir", str(task_dir / "result"),
            "--result-json", str(result_json),
            "--headless", "true" if dl_headless else "false",
            "--max-pages", str(int(download_max_pages)),
        )
        if browser_path.strip():
            cmd += ["--browser-executable-path", browser_path.strip()]
        start_task("task_download", cmd, task_dir)
        st.rerun()

    dl_result = render_task_panel("task_download", "파일데이터 다운로드 진행상황")
    if dl_result:
        download_file_button(dl_result.get("zip_path", ""), "📥 파일데이터 ZIP 다운로드", "application/zip", "download_zip")

st.markdown("</div>", unsafe_allow_html=True)


# ==========================================================
# 하단 안내
# ==========================================================
with st.expander("구현 방식 요약", expanded=False):
    st.markdown(
        """
        - `org_resolver.py`: 기관명 일부 검색 → 기관 후보 표시 → 선택 기관의 실제 파일데이터 목록과 상세 URL 샘플 검증
        - `metadata_bridge.py`: 기존 `crawler_metadata.py`의 상세 수집/파싱/저장 엔진 재사용
        - `stats_runner.py`: 기존 `crawler.py` URL 방식 우선, 실패 시 Resolver manifest fallback
        - `crawler_data_integrated.py`: 기존 현재/과거 파일 다운로드 흐름 유지, EXE 의존 제거, 파일데이터 목록 진입 안정화
        """
    )
