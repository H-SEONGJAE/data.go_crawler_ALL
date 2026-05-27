# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import re
import time
from pathlib import Path

import pandas as pd
import streamlit as st

from org_provider_url_resolver import (
    find_provider_candidates,
    resolve_provider_filedata_url,
    save_provider_url_result,
)
from metadata_listurl_runner import run_metadata_crawler
from stats_listurl_runner import run_stats_crawler
from download_listurl_runner import run_download_crawler

st.set_page_config(page_title="공공데이터포털 통합 크롤러", page_icon="📁", layout="wide")

MENU_ITEMS = [
    "기관 검색",
    "메타데이터 크롤링",
    "조회수·다운로드 수",
    "파일데이터 다운로드",
    "실행 상태",
]

# UI: 밝은 톤 박스 + 검정 글씨, 동일한 규칙의 카드/버튼
st.markdown(
    """
<style>
:root {
    --bg: #F5F6FA;
    --panel: #FFFFFF;
    --panel-2: #F9FAFB;
    --line: #E5E7EB;
    --text: #111827;
    --muted: #4B5563;
    --accent: #EF4444;
    --accent-dark: #DC2626;
}
html, body, [data-testid="stAppViewContainer"] {
    background: var(--bg) !important;
    color: var(--text) !important;
}
[data-testid="stSidebar"] {
    background: #FFFFFF !important;
    border-right: 1px solid var(--line) !important;
}
[data-testid="stSidebar"] * {
    color: var(--text) !important;
}
.block-container {
    padding-top: 28px !important;
    padding-bottom: 48px !important;
    max-width: 1380px !important;
}
* {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Apple SD Gothic Neo", "Malgun Gothic", sans-serif !important;
}
h1, h2, h3, h4, h5, h6, p, span, label, div {
    color: var(--text) !important;
    word-break: keep-all !important;
    overflow-wrap: anywhere !important;
}
.hero {
    background: var(--panel);
    border: 1px solid var(--line);
    border-radius: 18px;
    padding: 28px 30px;
    margin-bottom: 22px;
    box-shadow: 0 12px 30px rgba(17,24,39,0.06);
}
.hero h1 {
    font-size: 34px;
    line-height: 1.25;
    margin: 0 0 10px 0;
    font-weight: 850;
    color: #111827 !important;
}
.hero p {
    font-size: 15px;
    line-height: 1.65;
    margin: 0;
    color: #374151 !important;
}
.card {
    background: var(--panel);
    border: 1px solid var(--line);
    border-radius: 16px;
    padding: 22px 22px;
    margin-bottom: 18px;
    min-height: 112px;
    box-shadow: 0 8px 24px rgba(17,24,39,0.05);
}
.card.compact { min-height: 80px; }
.card h3 {
    font-size: 20px;
    margin: 0 0 10px 0;
    font-weight: 800;
    color: #111827 !important;
}
.card p, .card li {
    font-size: 14px;
    color: #374151 !important;
    line-height: 1.65;
}
.step-grid {
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 14px;
    margin-bottom: 18px;
}
.step-card {
    background: #FFFFFF;
    border: 1px solid var(--line);
    border-radius: 14px;
    padding: 18px;
    min-height: 112px;
}
.step-card b {
    display: block;
    margin-bottom: 8px;
    color: #111827 !important;
}
.step-card span {
    color: #374151 !important;
    font-size: 14px;
    line-height: 1.55;
}
.info-box {
    background: #F9FAFB;
    border: 1px solid #E5E7EB;
    border-radius: 14px;
    padding: 16px 18px;
    margin: 12px 0;
    color: #111827 !important;
    min-height: 54px;
}
.url-box {
    background: #F3F4F6;
    border: 1px solid #E5E7EB;
    border-radius: 12px;
    padding: 14px;
    font-family: Consolas, monospace !important;
    font-size: 13px;
    line-height: 1.55;
    color: #111827 !important;
    white-space: normal;
    overflow-wrap: anywhere;
}
div[data-testid="stTextInput"] input,
div[data-testid="stNumberInput"] input,
div[data-baseweb="select"] > div,
div[data-baseweb="select"] * {
    background-color: #FFFFFF !important;
    color: #111827 !important;
    border-color: #D1D5DB !important;
    min-height: 44px !important;
    font-size: 14px !important;
}
input::placeholder {
    color: #6B7280 !important;
    opacity: 1 !important;
}
.stButton > button, .stDownloadButton > button {
    min-height: 46px !important;
    border-radius: 12px !important;
    border: 1px solid #D1D5DB !important;
    background: #FFFFFF !important;
    color: #111827 !important;
    font-weight: 750 !important;
    width: 100%;
}
.stButton > button:hover, .stDownloadButton > button:hover {
    border-color: var(--accent) !important;
    color: #111827 !important;
    background: #FFF7F7 !important;
}
button[kind="primary"] {
    background: var(--accent) !important;
    color: #FFFFFF !important;
    border-color: var(--accent) !important;
}
button[kind="primary"] * {
    color: #FFFFFF !important;
}
[data-testid="stAlert"] {
    background: #FFFFFF !important;
    color: #111827 !important;
    border: 1px solid var(--line) !important;
    border-radius: 14px !important;
}
[data-testid="stAlert"] * {
    color: #111827 !important;
}
@media (max-width: 900px) {
    .step-grid { grid-template-columns: 1fr; }
    .hero h1 { font-size: 28px; }
}
</style>
""",
    unsafe_allow_html=True,
)


def init_state():
    defaults = {
        "menu": "기관 검색",
        "search_keyword": "",
        "provider_candidates": [],
        "selected_provider": "",
        "provider_filedata_url": "",
        "provider_original_url": "",
        "provider_url_result": {},
        "provider_url_json_path": "",
        "last_message": "",
    }
    for k, v in defaults.items():
        st.session_state.setdefault(k, v)


def has_provider_url() -> bool:
    return bool(st.session_state.get("provider_filedata_url"))


def selected_provider_name() -> str:
    return st.session_state.get("selected_provider") or st.session_state.get("provider_url_result", {}).get("provider_name", "") or "기관"


def render_header():
    st.markdown(
        """
<div class="hero">
    <h1>공공데이터포털 통합 크롤러</h1>
    <p>기관명을 입력해 포털 상세페이지에 연결된 실제 제공기관 파일데이터 목록 URL을 확보한 뒤, 기존 크롤러 엔진에 해당 URL을 전달합니다. 상세 URL 전체를 먼저 수집하지 않고 기관 목록 URL만 확정합니다.</p>
</div>
""",
        unsafe_allow_html=True,
    )


def render_sidebar():
    with st.sidebar:
        st.markdown("### 메뉴")
        current = st.session_state.get("menu", MENU_ITEMS[0])
        idx = MENU_ITEMS.index(current) if current in MENU_ITEMS else 0
        menu = st.radio("기능 선택", MENU_ITEMS, index=idx, key="menu_radio", label_visibility="collapsed")
        st.session_state.menu = menu
        st.markdown("---")
        if has_provider_url():
            st.markdown("**선택 기관**")
            st.markdown(f"{selected_provider_name()}")
            st.caption("기관 파일데이터 목록 URL 확보 완료")
        else:
            st.caption("먼저 기관 검색에서 제공기관을 선택하세요.")


def render_search_page():
    st.markdown('<div class="card">', unsafe_allow_html=True)
    st.markdown("### 1. 기관 검색")
    st.write("기관명 일부만 입력하면 후보 제공기관명을 찾습니다. 후보에는 기관명만 표시됩니다.")
    col1, col2 = st.columns([5, 1])
    with col1:
        keyword = st.text_input(
            "기관명",
            value=st.session_state.get("search_keyword", ""),
            placeholder="예: 중부발전, 한국중부발전, 서울특별시",
            label_visibility="collapsed",
        )
    with col2:
        search = st.button("검색", type="primary")
    st.markdown("</div>", unsafe_allow_html=True)

    if search:
        st.session_state.search_keyword = keyword.strip()
        st.session_state.provider_candidates = []
        st.session_state.selected_provider = ""
        if not keyword.strip():
            st.warning("기관명을 입력하세요.")
        else:
            with st.spinner("제공기관 후보를 검색하는 중입니다..."):
                try:
                    result = find_provider_candidates(keyword.strip())
                    candidates = [x.get("provider_name", "") for x in result.get("candidates", []) if x.get("provider_name")]
                    # 기관명만 중복 제거
                    candidates = list(dict.fromkeys(candidates))
                    st.session_state.provider_candidates = candidates
                    if not candidates:
                        st.error("제공기관 후보를 찾지 못했습니다. 기관명을 조금 더 정확히 입력해 주세요.")
                    else:
                        st.success(f"제공기관 후보 {len(candidates)}개를 찾았습니다.")
                except Exception as e:
                    st.error(f"기관 후보 검색 중 오류: {e}")

    candidates = st.session_state.get("provider_candidates", [])
    if candidates:
        st.markdown('<div class="card">', unsafe_allow_html=True)
        st.markdown("### 2. 제공기관 선택")
        provider = st.selectbox("제공기관", candidates, label_visibility="collapsed")
        confirm = st.button("제공기관 확인 및 URL 확보", type="primary")
        st.markdown("</div>", unsafe_allow_html=True)
        if confirm:
            with st.spinner("선택한 제공기관의 파일데이터 목록 URL을 가져오는 중입니다..."):
                try:
                    result = resolve_provider_filedata_url(provider, st.session_state.get("search_keyword", provider))
                    st.session_state.selected_provider = result.get("provider_name") or provider
                    st.session_state.provider_filedata_url = result.get("provider_filedata_url", "")
                    st.session_state.provider_original_url = result.get("provider_original_url", "")
                    st.session_state.provider_url_result = result
                    st.session_state.provider_url_json_path = save_provider_url_result(result)
                    st.success("기관 파일데이터 목록 URL 확보 완료. 메타데이터 크롤링 화면으로 이동합니다.")
                    time.sleep(0.7)
                    st.session_state.menu = "메타데이터 크롤링"
                    st.rerun()
                except Exception as e:
                    st.error(f"기관 파일데이터 목록 URL 확보 중 오류: {e}")


def guard_provider_url() -> bool:
    if not has_provider_url():
        st.warning("먼저 '기관 검색' 메뉴에서 제공기관을 선택하고 URL을 확보하세요.")
        return False
    return True


def render_metadata_page():
    st.markdown('<div class="card">', unsafe_allow_html=True)
    st.markdown("### 메타데이터 크롤링")
    st.write("기존 `crawler_metadata.py` 엔진에 선택 기관의 파일데이터 목록 URL을 전달해 실행합니다.")
    st.markdown("</div>", unsafe_allow_html=True)
    if not guard_provider_url():
        return
    if st.button("메타데이터 크롤링 실행", type="primary"):
        with st.spinner("메타데이터 크롤링 실행 중입니다. 데이터 수가 많으면 시간이 오래 걸립니다..."):
            try:
                result = run_metadata_crawler(st.session_state.provider_filedata_url, selected_provider_name())
                st.success("메타데이터 크롤링 완료")
                paths = result.get("paths", {}) if isinstance(result, dict) else {}
                for label, path in paths.items():
                    if path and Path(path).exists():
                        with open(path, "rb") as f:
                            st.download_button(f"{label} 다운로드", f, file_name=Path(path).name)
            except Exception as e:
                st.error(f"메타데이터 크롤링 오류: {e}")


def render_stats_page():
    st.markdown('<div class="card">', unsafe_allow_html=True)
    st.markdown("### 조회수·다운로드 수")
    st.write("기존 `crawler.py` 수집 함수에 선택 기관의 파일데이터 목록 URL을 전달해 실행합니다.")
    st.markdown("</div>", unsafe_allow_html=True)
    if not guard_provider_url():
        return
    status_box = st.empty()
    if st.button("조회수·다운로드 수 수집 실행", type="primary"):
        def cb(msg):
            status_box.info(msg)
        with st.spinner("조회수·다운로드 수 수집 중입니다..."):
            try:
                result = run_stats_crawler(st.session_state.provider_filedata_url, selected_provider_name(), status_callback=cb)
                status_box.empty()
                st.success(f"수집 완료: {result['rows']}건")
                st.dataframe(result["dataframe"], use_container_width=True)
                path = result.get("path")
                if path and Path(path).exists():
                    with open(path, "rb") as f:
                        st.download_button("엑셀 다운로드", f, file_name=Path(path).name)
            except Exception as e:
                st.error(f"조회수·다운로드 수 수집 오류: {e}")


def render_download_page():
    st.markdown('<div class="card">', unsafe_allow_html=True)
    st.markdown("### 파일데이터 다운로드")
    st.write("선택 기관의 파일데이터 목록 URL에서 현재데이터와 과거데이터를 다운로드합니다.")
    st.markdown("</div>", unsafe_allow_html=True)
    if not guard_provider_url():
        return
    col1, col2 = st.columns([1, 1])
    with col1:
        headless = st.checkbox("브라우저 숨김", value=True)
    with col2:
        max_pages = st.number_input("최대 페이지 수(0=전체)", min_value=0, value=0, step=1)
    log_area = st.empty()
    logs = []
    if st.button("파일데이터 다운로드 실행", type="primary"):
        def logger(msg):
            logs.append(str(msg))
            log_area.text_area("진행 로그", "\n".join(logs[-20:]), height=260)
        with st.spinner("파일 다운로드 중입니다..."):
            try:
                result = run_download_crawler(
                    st.session_state.provider_filedata_url,
                    selected_provider_name(),
                    headless=headless,
                    max_pages=int(max_pages),
                    log_callback=logger,
                )
                st.success(f"다운로드 완료: 데이터셋 {result['processed_datasets']}개, 파일 {result['downloaded_files']}개")
                zip_path = result.get("zip_path")
                if zip_path and Path(zip_path).exists():
                    with open(zip_path, "rb") as f:
                        st.download_button("ZIP 다운로드", f, file_name=Path(zip_path).name)
            except Exception as e:
                st.error(f"파일 다운로드 오류: {e}")


def render_status_page():
    st.markdown('<div class="card">', unsafe_allow_html=True)
    st.markdown("### 실행 상태")
    if not has_provider_url():
        st.write("아직 선택된 기관 URL이 없습니다.")
    else:
        st.write(f"선택 기관: {selected_provider_name()}")
        st.write("기관 파일데이터 목록 URL이 확보되어 있습니다.")
        # 사용자가 원하면 확인할 수 있도록 접기 형태로만 제공
        with st.expander("URL 확인"):
            st.markdown(f'<div class="url-box">{st.session_state.provider_filedata_url}</div>', unsafe_allow_html=True)
            if st.session_state.get("provider_url_json_path"):
                st.write(f"저장 JSON: {st.session_state.provider_url_json_path}")
    st.markdown("</div>", unsafe_allow_html=True)


def main():
    init_state()
    render_sidebar()
    render_header()
    menu = st.session_state.get("menu", "기관 검색")
    if menu == "기관 검색":
        render_search_page()
    elif menu == "메타데이터 크롤링":
        render_metadata_page()
    elif menu == "조회수·다운로드 수":
        render_stats_page()
    elif menu == "파일데이터 다운로드":
        render_download_page()
    else:
        render_status_page()


if __name__ == "__main__":
    main()
