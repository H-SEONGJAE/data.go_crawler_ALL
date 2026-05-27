# -*- coding: utf-8 -*-
"""
공공데이터포털 통합 크롤러 - 기관 유사도 점수 기반 URL 수집 버전

실행:
    streamlit run main.py
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import streamlit as st

from org_url_resolver import (
    DEFAULT_SCORE_THRESHOLD,
    find_provider_candidates,
    resolve_provider_filedata_items,
    save_resolution,
)
from metadata_resolved_runner import run_metadata_from_resolution
from stats_resolved_runner import run_stats_from_resolution
from download_resolved_runner import run_download_from_resolution

APP_TITLE = "공공데이터포털 통합 크롤러"

st.set_page_config(page_title=APP_TITLE, page_icon="🗂️", layout="wide", initial_sidebar_state="expanded")

# ==========================================================
# CSS: Figma 스타일의 밝은 박스 + 검정 글씨 + 균일 카드
# ==========================================================
st.markdown(
    """
    <style>
    :root {
        --bg: #F4F6FA;
        --panel: #FFFFFF;
        --panel-soft: #F8FAFC;
        --ink: #111827;
        --ink-2: #374151;
        --muted: #6B7280;
        --line: #E5E7EB;
        --line-strong: #D1D5DB;
        --accent: #2563EB;
        --accent-soft: #EFF6FF;
        --green-soft: #F0FDF4;
        --orange-soft: #FFF7ED;
        --shadow: 0 6px 18px rgba(17, 24, 39, 0.05);
        --radius: 16px;
        --box-h: 132px;
    }

    .stApp {
        background: var(--bg) !important;
        color: var(--ink) !important;
    }

    .block-container {
        max-width: 1500px !important;
        padding-top: 1.2rem !important;
        padding-bottom: 3rem !important;
    }

    /* 모든 텍스트 검정 계열 고정 */
    html, body, div, span, p, label, li, h1, h2, h3, h4, h5, h6,
    .stMarkdown, .stText, .stCaption, .stAlert, .stAlert * {
        color: var(--ink) !important;
        word-break: keep-all !important;
        overflow-wrap: anywhere !important;
        text-overflow: clip !important;
    }

    /* Sidebar */
    section[data-testid="stSidebar"] {
        background: #FFFFFF !important;
        border-right: 1px solid var(--line) !important;
    }
    section[data-testid="stSidebar"] * {
        color: var(--ink) !important;
    }
    [role="radiogroup"] label {
        min-height: 48px !important;
        background: #FFFFFF !important;
        border: 1px solid var(--line) !important;
        border-radius: 12px !important;
        padding: 9px 11px !important;
        margin-bottom: 8px !important;
        display: flex !important;
        align-items: center !important;
    }
    [role="radiogroup"] label:hover {
        background: var(--accent-soft) !important;
        border-color: #BFDBFE !important;
    }
    [role="radiogroup"] label * {
        color: var(--ink) !important;
        white-space: normal !important;
        line-height: 1.35 !important;
    }

    /* Input / Select / Textarea */
    div[data-baseweb="input"],
    div[data-baseweb="select"],
    div[data-baseweb="textarea"],
    div[data-baseweb="base-input"] {
        background: #FFFFFF !important;
        color: var(--ink) !important;
        border: 1px solid var(--line-strong) !important;
        border-radius: 12px !important;
        min-height: 44px !important;
        box-shadow: none !important;
    }
    div[data-baseweb="input"]:focus-within,
    div[data-baseweb="select"]:focus-within,
    div[data-baseweb="textarea"]:focus-within {
        border-color: var(--accent) !important;
        box-shadow: 0 0 0 3px rgba(37, 99, 235, 0.12) !important;
    }
    input, textarea, [contenteditable="true"] {
        color: var(--ink) !important;
        caret-color: var(--ink) !important;
        background: #FFFFFF !important;
    }
    input::placeholder, textarea::placeholder {
        color: #6B7280 !important;
        opacity: 1 !important;
    }
    div[data-baseweb="select"] *, div[data-baseweb="popover"] * {
        color: var(--ink) !important;
        white-space: normal !important;
        overflow: visible !important;
        text-overflow: clip !important;
    }

    /* Button: 밝은 박스 + 검정 글씨 */
    div.stButton > button,
    div.stDownloadButton > button {
        background: #FFFFFF !important;
        color: var(--ink) !important;
        border: 1px solid var(--line-strong) !important;
        border-radius: 12px !important;
        min-height: 46px !important;
        height: auto !important;
        padding: 10px 14px !important;
        font-weight: 800 !important;
        white-space: normal !important;
        word-break: keep-all !important;
        line-height: 1.35 !important;
        box-shadow: none !important;
    }
    div.stButton > button:hover,
    div.stDownloadButton > button:hover {
        background: var(--accent-soft) !important;
        border-color: var(--accent) !important;
        color: var(--ink) !important;
    }
    div.stButton > button *, div.stDownloadButton > button * {
        color: var(--ink) !important;
    }
    div.stButton > button[kind="primary"] {
        background: var(--accent-soft) !important;
        border-color: #93C5FD !important;
        color: var(--ink) !important;
    }
    div.stButton > button[kind="primary"] * { color: var(--ink) !important; }

    /* Dataframe / tables */
    div[data-testid="stDataFrame"] *,
    div[data-testid="stTable"] * {
        color: var(--ink) !important;
    }

    /* Custom boxes */
    .hero {
        background: var(--panel);
        border: 1px solid var(--line);
        border-radius: var(--radius);
        padding: 24px 26px;
        box-shadow: var(--shadow);
        margin-bottom: 18px;
    }
    .hero-title {
        font-size: 1.9rem;
        line-height: 1.25;
        font-weight: 900;
        letter-spacing: -0.035em;
        color: var(--ink) !important;
        margin-bottom: 8px;
    }
    .hero-sub {
        color: var(--ink-2) !important;
        font-size: 1rem;
        line-height: 1.65;
    }
    .box {
        background: var(--panel);
        border: 1px solid var(--line);
        border-radius: var(--radius);
        padding: 18px 20px;
        box-shadow: var(--shadow);
        margin-bottom: 16px;
        min-height: var(--box-h);
        height: auto;
    }
    .box.compact {
        min-height: 92px;
    }
    .box-title {
        color: var(--ink) !important;
        font-weight: 900;
        font-size: 1.05rem;
        line-height: 1.35;
        margin-bottom: 8px;
    }
    .box-body {
        color: var(--ink-2) !important;
        font-size: 0.95rem;
        line-height: 1.65;
    }
    .step-grid {
        display: grid;
        grid-template-columns: repeat(3, minmax(0, 1fr));
        gap: 14px;
        margin-bottom: 16px;
    }
    .step-card {
        background: var(--panel);
        border: 1px solid var(--line);
        border-radius: var(--radius);
        padding: 18px 20px;
        min-height: var(--box-h);
        box-shadow: var(--shadow);
    }
    .step-no {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        height: 26px;
        min-width: 58px;
        padding: 0 10px;
        border-radius: 999px;
        background: var(--accent-soft);
        color: var(--ink) !important;
        font-weight: 900;
        font-size: 0.82rem;
        margin-bottom: 10px;
    }
    .step-title {
        color: var(--ink) !important;
        font-weight: 900;
        margin-bottom: 6px;
        line-height: 1.35;
    }
    .step-desc {
        color: var(--ink-2) !important;
        font-size: 0.93rem;
        line-height: 1.55;
    }
    .metric-grid {
        display: grid;
        grid-template-columns: repeat(4, minmax(0, 1fr));
        gap: 12px;
        margin: 12px 0 18px 0;
    }
    .metric-card {
        background: #FFFFFF;
        border: 1px solid var(--line);
        border-radius: 14px;
        padding: 16px 18px;
        min-height: 96px;
        box-shadow: var(--shadow);
    }
    .metric-label {
        color: var(--muted) !important;
        font-size: 0.82rem;
        line-height: 1.3;
        margin-bottom: 8px;
    }
    .metric-value {
        color: var(--ink) !important;
        font-size: 1.2rem;
        font-weight: 900;
        line-height: 1.35;
    }
    .urlbox {
        background: var(--panel-soft);
        border: 1px solid var(--line);
        border-radius: 12px;
        padding: 13px 15px;
        font-family: Consolas, 'Courier New', monospace;
        font-size: 0.88rem;
        color: var(--ink) !important;
        white-space: normal;
        overflow-wrap: anywhere;
        word-break: break-all;
        min-height: 48px;
        margin-bottom: 12px;
    }
    .infobox {
        background: var(--accent-soft);
        border: 1px solid #BFDBFE;
        border-radius: 14px;
        padding: 15px 17px;
        color: var(--ink) !important;
        line-height: 1.65;
        margin-bottom: 14px;
    }
    .warnbox {
        background: var(--orange-soft);
        border: 1px solid #FDBA74;
        color: var(--ink) !important;
        border-radius: 14px;
        padding: 15px 17px;
        line-height: 1.65;
        margin-bottom: 14px;
    }
    .okbox {
        background: var(--green-soft);
        border: 1px solid #86EFAC;
        color: var(--ink) !important;
        border-radius: 14px;
        padding: 15px 17px;
        line-height: 1.65;
        margin-bottom: 14px;
    }
    code {
        color: var(--ink) !important;
        background: #EEF2FF !important;
        border: 1px solid #E0E7FF;
        border-radius: 5px;
        padding: 1px 4px;
    }
    @media (max-width: 900px) {
        .step-grid, .metric-grid { grid-template-columns: 1fr; }
        .hero-title { font-size: 1.5rem; }
    }
    </style>
    """,
    unsafe_allow_html=True,
)


# ==========================================================
# 상태 초기화
# ==========================================================
def init_state():
    defaults = {
        "candidate_result": None,
        "selected_candidate": None,
        "resolution": None,
        "resolution_path": "",
        "last_logs": [],
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


init_state()


# ==========================================================
# UI helpers
# ==========================================================
def render_hero():
    st.markdown(
        f"""
        <div class="hero">
            <div class="hero-title">{APP_TITLE}</div>
            <div class="hero-sub">
                기관명을 URL 파라미터로 직접 조립하지 않고, 파일데이터 검색 결과의 상세페이지에서
                제공기관명을 추출한 뒤 유사도 점수 기준으로 수집 대상을 확정합니다.
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_steps():
    st.markdown(
        """
        <div class="step-grid">
            <div class="step-card">
                <div class="step-no">STEP 1</div>
                <div class="step-title">검색어 입력</div>
                <div class="step-desc">기관 풀네임이 아니어도 됩니다. 예: 중부발전, 서울특별시, 교통공사</div>
            </div>
            <div class="step-card">
                <div class="step-no">STEP 2</div>
                <div class="step-title">기관 후보 점수화</div>
                <div class="step-desc">상세페이지 제공기관명을 읽어 검색어와 비교하고 80점 이상 후보만 표시합니다.</div>
            </div>
            <div class="step-card">
                <div class="step-no">STEP 3</div>
                <div class="step-title">선택 후 URL 확정</div>
                <div class="step-desc">선택 기관과 유사도 80점 이상인 상세 URL만 다음 크롤링 기능에 전달합니다.</div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_metrics(items):
    st.markdown(
        f"""
        <div class="metric-grid">
            <div class="metric-card"><div class="metric-label">선택 기관</div><div class="metric-value">{items.get('provider','-')}</div></div>
            <div class="metric-card"><div class="metric-label">후보 URL</div><div class="metric-value">{items.get('candidate','0')}</div></div>
            <div class="metric-card"><div class="metric-label">확정 URL</div><div class="metric-value">{items.get('verified','0')}</div></div>
            <div class="metric-card"><div class="metric-label">점수 기준</div><div class="metric-value">{items.get('score','80')}점 이상</div></div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def show_resolution_summary(resolution: dict):
    render_metrics({
        "provider": resolution.get("selected_provider", "-"),
        "candidate": f"{resolution.get('candidate_detail_url_count', 0):,}",
        "verified": f"{resolution.get('verified_detail_url_count', 0):,}",
        "score": resolution.get("score_threshold", DEFAULT_SCORE_THRESHOLD),
    })
    items = resolution.get("detail_items", [])[:3]
    if items:
        st.markdown("<div class='box compact'><div class='box-title'>상세 URL 샘플</div>", unsafe_allow_html=True)
        for item in items:
            st.markdown(f"<div class='urlbox'>{item.get('detail_url', '')}</div>", unsafe_allow_html=True)
        st.markdown("</div>", unsafe_allow_html=True)


def require_resolution() -> bool:
    if not st.session_state.get("resolution"):
        st.markdown("<div class='warnbox'>먼저 왼쪽 메뉴의 <b>기관 검색 · URL 수집</b>에서 기관 후보 선택과 URL 확정을 완료하세요.</div>", unsafe_allow_html=True)
        return False
    return True


def download_file_button(path: str, label: str):
    p = Path(path)
    if not p.exists():
        return
    st.download_button(label=label, data=p.read_bytes(), file_name=p.name, use_container_width=True)


# ==========================================================
# Sidebar
# ==========================================================
with st.sidebar:
    st.markdown("### 🗂️ 메뉴")
    menu = st.radio(
        "기능 선택",
        [
            "1. 기관 검색 · URL 수집",
            "2. 메타데이터 크롤링",
            "3. 조회수 · 다운로드 수",
            "4. 파일데이터 다운로드",
            "5. 로그 · 결과 확인",
        ],
        label_visibility="collapsed",
    )
    st.divider()
    st.markdown("#### 현재 상태")
    if st.session_state.get("resolution"):
        st.markdown("<div class='okbox'><b>URL 확정 완료</b><br>다음 기능을 실행할 수 있습니다.</div>", unsafe_allow_html=True)
        st.caption(st.session_state["resolution"].get("selected_provider", ""))
    else:
        st.markdown("<div class='infobox'><b>URL 수집 전</b><br>1번 메뉴에서 기관을 먼저 선택하세요.</div>", unsafe_allow_html=True)


render_hero()


# ==========================================================
# 1. 기관 검색 / URL 수집
# ==========================================================
if menu.startswith("1."):
    st.markdown("## 1. 기관 검색 · 파일데이터 URL 수집")
    render_steps()
    st.markdown(
        """
        <div class='box'>
            <div class='box-title'>수집 방식</div>
            <div class='box-body'>
                기존처럼 <code>org=기관명</code> URL을 직접 만들지 않습니다.
                검색어로 파일데이터 목록을 찾고, 상세페이지의 제공기관명을 추출한 뒤
                유사도 점수 80점 이상인 기관 후보만 선택 목록에 표시합니다.
                네트워크 연결 리셋 방지를 위해 Edge 계열 User-Agent와 재시도 로직을 적용했습니다.
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    col_a, col_b, col_c, col_d = st.columns([4, 1.2, 1.2, 1.6])
    with col_a:
        keyword = st.text_input("기관명 일부 또는 관련 검색어", placeholder="예: 중부발전, 한국중부발전, 서울특별시", key="resolver_keyword")
    with col_b:
        candidate_pages = st.number_input("후보 검색 페이지", min_value=1, max_value=30, value=3, step=1)
    with col_c:
        score_threshold = st.number_input("점수 기준", min_value=50, max_value=100, value=80, step=5)
    with col_d:
        use_browser_search = st.checkbox("브라우저 기반 검색", value=False, help="기본은 requests 기반입니다. 파일 다운로드 외에는 보통 꺼두는 것을 권장합니다.")
        headless = st.checkbox("브라우저 숨김", value=True, disabled=not use_browser_search)

    col_btn1, col_btn2 = st.columns([1.3, 4])
    with col_btn1:
        search_clicked = st.button("기관 후보 검색", type="primary", use_container_width=True)
    with col_btn2:
        st.caption("Connection reset이 발생하면 자동 재시도합니다. 검색 페이지 수를 너무 크게 잡으면 차단 가능성이 올라갑니다.")

    if search_clicked:
        if not keyword.strip():
            st.warning("검색어를 입력하세요.")
        else:
            with st.spinner("공공데이터포털에서 상세 URL을 찾고 제공기관 후보를 점수화하는 중입니다..."):
                try:
                    result = find_provider_candidates(
                        keyword=keyword.strip(),
                        max_pages=int(candidate_pages),
                        max_detail_check=50,
                        headless=headless,
                        use_browser=use_browser_search,
                        score_threshold=int(score_threshold),
                    )
                    st.session_state.candidate_result = result
                    st.session_state.last_logs = result.get("log", [])
                    if result.get("candidates"):
                        st.success(f"기관 후보 {len(result['candidates'])}개를 찾았습니다. {int(score_threshold)}점 이상만 표시됩니다.")
                    else:
                        st.error("기관 후보를 찾지 못했습니다. 검색어를 넓히거나 후보 검색 페이지 수를 늘려보세요.")
                except Exception as e:
                    st.session_state.last_logs = [{"step": "find_provider_candidates", "error": repr(e)}]
                    st.error(f"기관 후보 검색 중 오류: {e}")

    candidate_result = st.session_state.get("candidate_result")
    if candidate_result and candidate_result.get("candidates"):
        st.markdown("### 기관 후보 목록")
        cand_df = pd.DataFrame(candidate_result["candidates"])
        show_cols = [c for c in ["provider_name", "max_score", "avg_score", "hit_count", "provider_url", "sample_title", "sample_detail_url"] if c in cand_df.columns]
        st.dataframe(cand_df[show_cols], use_container_width=True, hide_index=True)

        options = [f"{c['provider_name']}  ·  {c.get('max_score', 0)}점  ·  {c.get('hit_count', 0)}건" for c in candidate_result["candidates"]]
        selected_label = st.selectbox("수집할 제공기관 선택", options=options, key="selected_provider_name")
        selected_idx = options.index(selected_label)
        selected_info = candidate_result["candidates"][selected_idx]
        selected_provider = selected_info.get("provider_name", "")
        provider_url = selected_info.get("provider_url", "")
        if provider_url:
            st.markdown("**상세페이지에서 추출한 제공기관 링크 후보**")
            st.markdown(f"<div class='urlbox'>{provider_url}</div>", unsafe_allow_html=True)

        col1, col2, col3 = st.columns([1.2, 1.2, 3])
        with col1:
            collect_pages = st.number_input("URL 수집 페이지", min_value=1, max_value=100, value=10, step=1)
        with col2:
            max_items = st.number_input("최대 URL 수(0=전체)", min_value=0, max_value=100000, value=0, step=10)
        with col3:
            st.caption("선택 기관명과 상세페이지 제공기관명의 유사도 점수가 기준 이상인 URL만 확정합니다. 별도 URL 검증 단계는 두지 않습니다.")

        if st.button("선택 기관 URL 확정", type="primary", use_container_width=True):
            with st.spinner("선택 기관 기준으로 상세 URL을 수집하고 유사도 점수로 필터링하는 중입니다..."):
                try:
                    resolution = resolve_provider_filedata_items(
                        provider_name=selected_provider,
                        seed_keyword=candidate_result.get("input_keyword", keyword),
                        provider_url=provider_url,
                        max_pages=int(collect_pages),
                        max_items=int(max_items),
                        headless=headless,
                        use_browser=use_browser_search,
                        score_threshold=int(score_threshold),
                    )
                    st.session_state.resolution = resolution
                    st.session_state.last_logs = resolution.get("log", [])
                    st.session_state.resolution_path = save_resolution(resolution)
                    if resolution.get("verified_detail_url_count", 0) > 0:
                        st.success(f"URL 확정 완료: {resolution['verified_detail_url_count']:,}건")
                    else:
                        st.error("확정된 URL이 없습니다. 검색어를 바꾸거나 수집 페이지 수를 늘려보세요.")
                except Exception as e:
                    st.session_state.last_logs = [{"step": "resolve_provider_filedata_items", "error": repr(e)}]
                    st.error(f"URL 수집 중 오류: {e}")

    if st.session_state.get("resolution"):
        st.markdown("### 현재 확정된 URL 수집 결과")
        show_resolution_summary(st.session_state["resolution"])
        if st.session_state.get("resolution_path"):
            download_file_button(st.session_state["resolution_path"], "URL 수집 결과 JSON 다운로드")


# ==========================================================
# 2. 메타데이터 크롤링
# ==========================================================
elif menu.startswith("2."):
    st.markdown("## 2. 메타데이터 크롤링")
    if require_resolution():
        show_resolution_summary(st.session_state["resolution"])
        st.divider()
        col1, col2 = st.columns([1, 4])
        with col1:
            concurrency = st.number_input("상세 동시 처리 수", min_value=1, max_value=30, value=8, step=1)
        with col2:
            st.markdown("<div class='box compact'><div class='box-title'>처리 방식</div><div class='box-body'>기존 crawler_metadata.py의 상세 수집·파싱·실패로그 저장 엔진을 사용합니다. 목록 URL 생성 단계만 확정된 상세 URL 목록으로 대체합니다.</div></div>", unsafe_allow_html=True)
        if st.button("메타데이터 수집 실행", type="primary", use_container_width=True):
            with st.spinner("메타데이터 수집 중입니다. 대상 건수가 많으면 시간이 걸릴 수 있습니다..."):
                try:
                    result = run_metadata_from_resolution(st.session_state["resolution"], concurrency=int(concurrency))
                    st.success(f"수집 완료: 성공 {result['success_count']:,}건 / 실패 {result['fail_count']:,}건")
                    st.json(result)
                    for name, path in result.get("paths", {}).items():
                        download_file_button(path, f"{name} 파일 다운로드")
                except Exception as e:
                    st.error(f"메타데이터 수집 오류: {e}")


# ==========================================================
# 3. 조회수 / 다운로드 수
# ==========================================================
elif menu.startswith("3."):
    st.markdown("## 3. 조회수 · 다운로드 수")
    if require_resolution():
        show_resolution_summary(st.session_state["resolution"])
        st.markdown("<div class='box'><div class='box-title'>처리 방식</div><div class='box-body'>기관 URL 목록 페이지 접근이 불안정하므로, URL 수집 단계에서 확보한 목록 카드의 조회수/다운로드 수와 상세 URL, 제공기관 점수를 함께 저장합니다.</div></div>", unsafe_allow_html=True)
        if st.button("조회수/다운로드 수 엑셀 생성", type="primary", use_container_width=True):
            try:
                result = run_stats_from_resolution(st.session_state["resolution"])
                st.success(f"생성 완료: {result['row_count']:,}건")
                download_file_button(result["path"], "조회수/다운로드 수 엑셀 다운로드")
            except Exception as e:
                st.error(f"조회수/다운로드 수 생성 오류: {e}")


# ==========================================================
# 4. 파일데이터 다운로드
# ==========================================================
elif menu.startswith("4."):
    st.markdown("## 4. 파일데이터 다운로드")
    if require_resolution():
        show_resolution_summary(st.session_state["resolution"])
        st.markdown("<div class='box'><div class='box-title'>처리 방식</div><div class='box-body'>확정된 상세 URL 목록을 직접 순회하여 최신 파일과 과거데이터 파일을 다운로드합니다. 목록 페이지네이션을 사용하지 않으므로 같은 페이지 반복으로 인한 무한 루프를 피합니다.</div></div>", unsafe_allow_html=True)
        col1, col2, col3 = st.columns([1, 1, 3])
        with col1:
            dl_headless = st.checkbox("브라우저 숨김", value=True, key="dl_headless")
        with col2:
            dl_max = st.number_input("최대 다운로드 건수(0=전체)", min_value=0, max_value=100000, value=0, step=10)
        with col3:
            st.caption("테스트 시에는 최대 다운로드 건수를 1~3으로 두고 먼저 확인하는 것을 권장합니다.")
        if st.button("파일데이터 다운로드 실행", type="primary", use_container_width=True):
            with st.spinner("파일 다운로드 중입니다. 브라우저 다운로드가 진행되므로 시간이 걸릴 수 있습니다..."):
                try:
                    result = run_download_from_resolution(st.session_state["resolution"], headless=dl_headless, max_items=int(dl_max), make_zip=True)
                    st.success(f"다운로드 처리 완료: {result['processed_count']:,}건")
                    st.json({k: v for k, v in result.items() if k != "log"})
                    if result.get("zip_path"):
                        download_file_button(result["zip_path"], "다운로드 결과 ZIP 받기")
                    if result.get("log"):
                        st.dataframe(pd.DataFrame(result["log"]), use_container_width=True, hide_index=True)
                except Exception as e:
                    st.error(f"파일데이터 다운로드 오류: {e}")


# ==========================================================
# 5. 로그
# ==========================================================
elif menu.startswith("5."):
    st.markdown("## 5. 로그 · 결과 확인")
    if st.session_state.get("resolution"):
        st.markdown("### 확정 결과")
        show_resolution_summary(st.session_state["resolution"])
    else:
        st.info("아직 확정된 URL 수집 결과가 없습니다.")

    logs = st.session_state.get("last_logs", [])
    st.markdown("### 최근 실행 로그")
    if logs:
        st.dataframe(pd.DataFrame(logs), use_container_width=True, hide_index=True)
    else:
        st.caption("표시할 로그가 없습니다.")

    if st.session_state.get("resolution_path"):
        download_file_button(st.session_state["resolution_path"], "URL 수집 결과 JSON 다운로드")
