# -*- coding: utf-8 -*-
"""
공공데이터포털 통합 크롤러 - URL Resolver 개선 버전

실행:
    streamlit run main.py
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pandas as pd
import streamlit as st

from org_url_resolver import (
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
# CSS: 글씨 안 보임/잘림 방지 + 좌측 메뉴 기반 구성
# ==========================================================
st.markdown(
    """
    <style>
    :root {
        --bg: #f5f6fa;
        --panel: #ffffff;
        --text: #111827;
        --subtext: #4b5563;
        --line: #e5e7eb;
        --red: #ef4444;
        --red-dark: #dc2626;
        --blue: #2563eb;
        --soft: #f9fafb;
    }
    html, body, [class*="css"] { color: var(--text) !important; }
    .stApp { background: var(--bg) !important; color: var(--text) !important; }
    section[data-testid="stSidebar"] { background: #ffffff !important; border-right: 1px solid var(--line); }
    section[data-testid="stSidebar"] * { color: var(--text) !important; }
    .block-container { padding-top: 1.8rem !important; padding-bottom: 3rem !important; max-width: 1500px !important; }

    h1, h2, h3, h4, h5, h6, p, span, label, div { color: var(--text); }
    p, li, label { line-height: 1.55 !important; }

    /* 입력 위젯 글씨 잘림/흰 글씨 방지 */
    div[data-baseweb="input"], div[data-baseweb="select"], div[data-baseweb="textarea"] {
        background-color: #ffffff !important;
        border-radius: 12px !important;
    }
    input, textarea, [contenteditable="true"] {
        color: #111827 !important;
        caret-color: #111827 !important;
    }
    input::placeholder, textarea::placeholder { color: #6b7280 !important; opacity: 1 !important; }
    div[data-baseweb="select"] * { color: #111827 !important; white-space: normal !important; }

    /* 버튼 */
    div.stButton > button {
        border-radius: 12px !important;
        min-height: 44px !important;
        height: auto !important;
        font-weight: 700 !important;
        white-space: normal !important;
        word-break: keep-all !important;
        color: #111827 !important;
    }
    div.stButton > button[kind="primary"] {
        background: var(--red) !important;
        border-color: var(--red) !important;
        color: #ffffff !important;
    }
    div.stButton > button[kind="primary"] * { color: #ffffff !important; }
    div.stDownloadButton > button {
        border-radius: 12px !important;
        min-height: 44px !important;
        color: #111827 !important;
        white-space: normal !important;
    }

    /* radio 메뉴 */
    [role="radiogroup"] label { padding: 6px 2px !important; }
    [role="radiogroup"] label * { color: #111827 !important; }

    .hero {
        background: linear-gradient(135deg, #ffffff 0%, #f7f7fb 100%);
        border: 1px solid #e5e7eb;
        border-radius: 22px;
        padding: 28px 32px;
        box-shadow: 0 14px 38px rgba(15, 23, 42, 0.08);
        margin-bottom: 18px;
    }
    .hero-title {
        font-size: 2.15rem;
        line-height: 1.25;
        font-weight: 900;
        letter-spacing: -0.04em;
        color: #111827 !important;
        margin-bottom: 8px;
        word-break: keep-all;
    }
    .hero-sub {
        color: #374151 !important;
        font-size: 1rem;
        line-height: 1.6;
        word-break: keep-all;
        overflow-wrap: anywhere;
    }
    .card {
        background: #ffffff;
        border: 1px solid #e5e7eb;
        border-radius: 18px;
        padding: 20px 22px;
        box-shadow: 0 8px 24px rgba(15, 23, 42, 0.06);
        margin-bottom: 16px;
    }
    .card-title {
        font-size: 1.12rem;
        font-weight: 850;
        color: #111827 !important;
        margin-bottom: 8px;
    }
    .muted { color: #4b5563 !important; font-size: 0.95rem; line-height: 1.55; }
    .urlbox {
        background: #f9fafb;
        border: 1px solid #e5e7eb;
        border-radius: 12px;
        padding: 12px 14px;
        font-family: Consolas, 'Courier New', monospace;
        font-size: 0.88rem;
        color: #111827 !important;
        white-space: normal;
        overflow-wrap: anywhere;
        word-break: break-all;
    }
    .metricbox {
        background: #ffffff;
        border: 1px solid #e5e7eb;
        border-radius: 16px;
        padding: 14px 16px;
    }
    .metric-label { color: #6b7280 !important; font-size: 0.82rem; margin-bottom: 4px; }
    .metric-value { color: #111827 !important; font-size: 1.35rem; font-weight: 850; }
    .warnbox {
        background: #fff7ed;
        border: 1px solid #fed7aa;
        color: #9a3412 !important;
        border-radius: 14px;
        padding: 14px 16px;
        line-height: 1.55;
    }
    .okbox {
        background: #ecfdf5;
        border: 1px solid #bbf7d0;
        color: #065f46 !important;
        border-radius: 14px;
        padding: 14px 16px;
        line-height: 1.55;
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
# 유틸
# ==========================================================
def render_hero():
    st.markdown(
        f"""
        <div class="hero">
            <div class="hero-title">{APP_TITLE}</div>
            <div class="hero-sub">
                기관명을 직접 URL 파라미터로 조립하지 않고, 파일데이터 검색 결과의 상세페이지에서
                제공기관명을 다시 확인한 뒤 검증된 상세 URL 목록으로 수집을 진행합니다.
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def require_resolution() -> bool:
    if not st.session_state.get("resolution"):
        st.markdown(
            "<div class='warnbox'>먼저 왼쪽 메뉴의 <b>기관 검색 · URL 검증</b>에서 기관 후보 검색과 URL 교차 검증을 완료하세요.</div>",
            unsafe_allow_html=True,
        )
        return False
    return True


def show_resolution_summary(resolution: dict):
    if not resolution:
        return
    cols = st.columns(4)
    with cols[0]:
        st.markdown(f"<div class='metricbox'><div class='metric-label'>선택 기관</div><div class='metric-value'>{resolution.get('selected_provider','')}</div></div>", unsafe_allow_html=True)
    with cols[1]:
        st.markdown(f"<div class='metricbox'><div class='metric-label'>후보 상세 URL</div><div class='metric-value'>{resolution.get('candidate_detail_url_count',0):,}</div></div>", unsafe_allow_html=True)
    with cols[2]:
        st.markdown(f"<div class='metricbox'><div class='metric-label'>검증 통과 URL</div><div class='metric-value'>{resolution.get('verified_detail_url_count',0):,}</div></div>", unsafe_allow_html=True)
    with cols[3]:
        st.markdown(f"<div class='metricbox'><div class='metric-label'>검색어</div><div class='metric-value'>{resolution.get('seed_keyword','')}</div></div>", unsafe_allow_html=True)

    if resolution.get("provider_url"):
        st.markdown("**제공기관 공식 링크 후보**")
        st.markdown(f"<div class='urlbox'>{resolution.get('provider_url')}</div>", unsafe_allow_html=True)

    items = resolution.get("detail_items", [])[:10]
    if items:
        df = pd.DataFrame(items)
        cols = [c for c in ["title", "provider_name", "view_count", "download_count", "detail_url", "source_keyword"] if c in df.columns]
        st.dataframe(df[cols], use_container_width=True, hide_index=True)


def download_file_button(path: str, label: str):
    p = Path(path)
    if not p.exists():
        st.warning(f"파일을 찾을 수 없습니다: {path}")
        return
    with open(p, "rb") as f:
        st.download_button(label=label, data=f.read(), file_name=p.name, use_container_width=True)


# ==========================================================
# 사이드바
# ==========================================================
with st.sidebar:
    st.markdown("### 🗂️ 메뉴")
    menu = st.radio(
        "기능 선택",
        [
            "1. 기관 검색 · URL 검증",
            "2. 메타데이터 크롤링",
            "3. 조회수 · 다운로드 수",
            "4. 파일데이터 다운로드",
            "5. 로그 · 검증 결과",
        ],
        label_visibility="collapsed",
    )
    st.divider()
    st.markdown("#### 현재 상태")
    if st.session_state.get("resolution"):
        st.success("URL 검증 완료")
        st.caption(st.session_state["resolution"].get("selected_provider", ""))
    else:
        st.info("URL 검증 전")


render_hero()


# ==========================================================
# 1. 기관 검색 / URL 검증
# ==========================================================
if menu.startswith("1."):
    st.markdown("## 1. 기관 검색 · 파일데이터 URL 교차 검증")
    st.markdown(
        "<div class='card'><div class='card-title'>검색 방식</div><div class='muted'>"
        "기존처럼 <code>org=기관명</code> URL을 직접 조립하지 않습니다. "
        "입력한 키워드로 파일데이터를 검색하고, 검색 결과 상세페이지의 <b>제공기관</b> 값을 읽어 후보 기관을 만듭니다. "
        "기관을 선택하면 상세페이지 제공기관 값으로 한 번 더 교차 검증해 최종 수집 URL 목록을 확정합니다."
        "</div></div>",
        unsafe_allow_html=True,
    )

    col_a, col_b, col_c = st.columns([4, 1.2, 1.2])
    with col_a:
        keyword = st.text_input("기관명 일부 또는 관련 검색어", placeholder="예: 중부발전, 한국중부발전, 서울특별시", key="resolver_keyword")
    with col_b:
        candidate_pages = st.number_input("후보 검색 페이지", min_value=1, max_value=20, value=3, step=1)
    with col_c:
        headless = st.checkbox("브라우저 숨김", value=True)

    col_btn1, col_btn2 = st.columns([1, 4])
    with col_btn1:
        search_clicked = st.button("기관 후보 검색", type="primary", use_container_width=True)
    with col_btn2:
        st.caption("풀네임이 아니어도 됩니다. 후보 검색 후 목록에서 실제 기관명을 선택하세요.")

    if search_clicked:
        if not keyword.strip():
            st.warning("검색어를 입력하세요.")
        else:
            with st.spinner("공공데이터포털에서 파일데이터 상세 URL을 찾고 제공기관 후보를 추출하는 중입니다..."):
                try:
                    result = find_provider_candidates(
                        keyword=keyword.strip(),
                        max_pages=int(candidate_pages),
                        max_detail_check=40,
                        headless=headless,
                    )
                    st.session_state.candidate_result = result
                    st.session_state.last_logs = result.get("log", [])
                    if result.get("candidates"):
                        st.success(f"기관 후보 {len(result['candidates'])}개를 찾았습니다.")
                    else:
                        st.error("기관 후보를 찾지 못했습니다. 검색어를 넓히거나 후보 검색 페이지 수를 늘려보세요.")
                except Exception as e:
                    st.session_state.last_logs = [{"step": "find_provider_candidates", "error": repr(e)}]
                    st.error(f"기관 후보 검색 중 오류: {e}")

    candidate_result = st.session_state.get("candidate_result")
    if candidate_result and candidate_result.get("candidates"):
        st.markdown("### 기관 후보 목록")
        cand_df = pd.DataFrame(candidate_result["candidates"])
        show_cols = [c for c in ["provider_name", "hit_count", "provider_url", "sample_title", "sample_detail_url"] if c in cand_df.columns]
        st.dataframe(cand_df[show_cols], use_container_width=True, hide_index=True)

        options = [c["provider_name"] for c in candidate_result["candidates"]]
        selected_provider = st.selectbox("수집할 제공기관 선택", options=options, key="selected_provider_name")
        selected_info = next((c for c in candidate_result["candidates"] if c["provider_name"] == selected_provider), {})
        provider_url = selected_info.get("provider_url", "")
        if provider_url:
            st.markdown("**상세페이지에서 추출한 제공기관 공식 링크 후보**")
            st.markdown(f"<div class='urlbox'>{provider_url}</div>", unsafe_allow_html=True)

        col1, col2, col3 = st.columns([1.4, 1.2, 2.4])
        with col1:
            verify_pages = st.number_input("검증 수집 페이지", min_value=1, max_value=100, value=10, step=1)
        with col2:
            max_items = st.number_input("최대 URL 수(0=전체)", min_value=0, max_value=100000, value=0, step=10)
        with col3:
            st.caption("선택 기관명과 상세페이지 제공기관명이 일치하는 URL만 최종 수집 대상으로 저장합니다.")

        if st.button("선택 기관 URL 교차 검증", type="primary", use_container_width=True):
            with st.spinner("선택 기관 기준으로 상세 URL을 재수집하고 제공기관명을 교차 검증하는 중입니다..."):
                try:
                    resolution = resolve_provider_filedata_items(
                        provider_name=selected_provider,
                        seed_keyword=candidate_result.get("input_keyword", keyword),
                        provider_url=provider_url,
                        max_pages=int(verify_pages),
                        max_items=int(max_items),
                        headless=headless,
                    )
                    st.session_state.resolution = resolution
                    st.session_state.last_logs = resolution.get("log", [])
                    st.session_state.resolution_path = save_resolution(resolution)
                    if resolution.get("verified_detail_url_count", 0) > 0:
                        st.success(f"URL 검증 완료: {resolution['verified_detail_url_count']:,}건")
                    else:
                        st.error("검증 통과 URL이 없습니다. 검색어를 바꾸거나 검증 페이지 수를 늘려보세요.")
                except Exception as e:
                    st.session_state.last_logs = [{"step": "resolve_provider_filedata_items", "error": repr(e)}]
                    st.error(f"URL 검증 중 오류: {e}")

    if st.session_state.get("resolution"):
        st.markdown("### 현재 확정된 URL 검증 결과")
        show_resolution_summary(st.session_state["resolution"])
        if st.session_state.get("resolution_path"):
            download_file_button(st.session_state["resolution_path"], "검증 결과 JSON 다운로드")


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
            st.caption("기존 crawler_metadata.py의 상세 수집/파싱/실패로그 저장 엔진을 사용합니다. 목록 URL 수집 단계만 검증된 상세 URL 목록으로 대체합니다.")
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
        st.markdown(
            "<div class='card'><div class='card-title'>처리 방식</div><div class='muted'>"
            "기존 기관 URL 목록 페이지 접근이 계속 실패하므로, URL Resolver가 이미 확보한 목록 카드의 조회수/다운로드 수 값을 저장합니다. "
            "상세 URL과 제공기관 검증 결과가 함께 들어가므로 이후 검토가 가능합니다."
            "</div></div>",
            unsafe_allow_html=True,
        )
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
        st.markdown(
            "<div class='card'><div class='card-title'>처리 방식</div><div class='muted'>"
            "검증된 상세 URL 목록을 직접 순회하여 최신 파일과 과거데이터 파일을 다운로드합니다. "
            "기관 목록 URL 페이지네이션을 사용하지 않으므로, 같은 페이지 반복으로 인한 무한 루프를 피합니다."
            "</div></div>",
            unsafe_allow_html=True,
        )
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
                    result = run_download_from_resolution(
                        st.session_state["resolution"],
                        headless=dl_headless,
                        max_items=int(dl_max),
                        make_zip=True,
                    )
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
    st.markdown("## 5. 로그 · 검증 결과")
    if st.session_state.get("resolution"):
        st.markdown("### 확정 결과")
        show_resolution_summary(st.session_state["resolution"])
    else:
        st.info("아직 확정된 URL 검증 결과가 없습니다.")

    logs = st.session_state.get("last_logs", [])
    st.markdown("### 최근 실행 로그")
    if logs:
        st.dataframe(pd.DataFrame(logs), use_container_width=True, hide_index=True)
    else:
        st.caption("표시할 로그가 없습니다.")

    if st.session_state.get("resolution_path"):
        download_file_button(st.session_state["resolution_path"], "검증 결과 JSON 다운로드")
