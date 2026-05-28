# -*- coding: utf-8 -*-
"""
page1_org_metadata.py

메타데이터 크롤링 > 기관별 수집 탭.

중요 원칙
- 기관별 기능은 이 파일에서 담당합니다. 기관명 입력, 기관별 FILE 목록 URL 생성, 컬럼 선택, 엑셀 다운로드만 처리합니다.
- 실제 목록 URL 수집, 상세 HTML 수집, 상세 메타데이터 파싱은 crawler_metadata.py의 검증된 함수만 호출합니다.
- 이 파일 안에서 별도의 상세페이지 httpx/Playwright 수집 로직을 구현하지 않습니다.
"""

import contextlib
import io
import re
import time
from io import BytesIO

import pandas as pd
import streamlit as st

import crawler_metadata


def _clean(value) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value).replace("\xa0", " ")).strip()


def _safe_int(value, default=0):
    try:
        return int(value)
    except Exception:
        return default


def render_tab2(
    get_soup,
    find_valid_org_name,
    format_tel_no,
    BASE_URL,
    HEADERS,
    ALL_SELECTABLE_COLUMNS,
    TARGET_METADATA_KEYS,
    METADATA_KEY_MAP,
    collect_detail_urls_by_org=None,
    build_org_file_list_url=None,
    collect_detail_items_by_org=None,
):
    guide_html = """
    <div style="background-color: #F0F4F8; padding: 25px; border-radius: 12px; margin-bottom: 30px; border: 1px solid #E1E8F0;">
        <h4 style="margin-top: 0px; margin-bottom: 20px; color: #1E3A8A;">사용 방법</h4>
        <div style="display: flex; gap: 15px;">
            <div style="flex: 1; background-color: white; padding: 15px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.02);">
                <div style="font-weight: bold; color: #2563EB; margin-bottom: 8px; font-size: 15px;">STEP 1</div>
                <div style="font-size: 14px; color: #475569; line-height: 1.5;">검색창에 <b>제공기관명</b>을 입력하고 [검색]을 누릅니다.</div>
            </div>
            <div style="flex: 1; background-color: white; padding: 15px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.02);">
                <div style="font-weight: bold; color: #2563EB; margin-bottom: 8px; font-size: 15px;">STEP 2</div>
                <div style="font-size: 14px; color: #475569; line-height: 1.5;">추출을 원하는 <b>데이터 항목</b>을 선택합니다.</div>
            </div>
            <div style="flex: 1; background-color: white; padding: 15px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.02);">
                <div style="font-weight: bold; color: #2563EB; margin-bottom: 8px; font-size: 15px;">STEP 3</div>
                <div style="font-size: 14px; color: #475569; line-height: 1.5;"><b>[추출]</b> 버튼을 누르고 완료되면 엑셀을 다운로드합니다.</div>
            </div>
        </div>
    </div>
    """
    st.markdown(guide_html, unsafe_allow_html=True)

    st.markdown("**▪&nbsp; 제공기관명 입력** (예: 한국중부발전(주), (재)한국저작권보호원)")
    col_input, col_btn = st.columns([4, 1])

    with col_input:
        org_input1 = st.text_input(
            "제공기관(기관별 메타데이터)",
            label_visibility="collapsed",
            placeholder="기관명을 입력하면 해당 기관의 메타데이터만 추출합니다.",
            key="org_input1",
        )

    with col_btn:
        search_clicked1 = st.button("검색", icon=":material/search:", use_container_width=True, key="search_btn1")

    if search_clicked1:
        if not org_input1.strip():
            st.warning("제공기관명을 입력해주세요!")
        else:
            with st.spinner(f"'{org_input1}' 검색 결과를 확인 중입니다..."):
                exact_org_name, total_pages = find_valid_org_name(org_input1.strip())
                st.session_state.total_pages1 = total_pages
                st.session_state.target_org1 = exact_org_name

            if total_pages == 0:
                st.error("❌ 검색 결과가 없습니다. 기관명을 다시 확인하고, 2~3번 재시도 해주세요.")
            else:
                if exact_org_name != org_input1.strip():
                    st.info(f"💡 '{exact_org_name}'(으)로 자동 변환하여 검색했습니다.")
                st.success("✅ URL검색이 완료되었습니다. 수집을 진행해주세요.")

    if st.session_state.get("total_pages1", 0) <= 0:
        return

    st.markdown("---")
    st.markdown("**▪&nbsp; 추출할 항목 선택**")

    options_with_all = ["모두 선택"] + ALL_SELECTABLE_COLUMNS

    if "warning_msg1" not in st.session_state:
        st.session_state.warning_msg1 = ""
    if "selected_cols1" not in st.session_state:
        st.session_state.selected_cols1 = []
    if "prev_selected_cols1" not in st.session_state:
        st.session_state.prev_selected_cols1 = []

    def check_selection1():
        current = st.session_state.selected_cols1
        prev = st.session_state.prev_selected_cols1

        if "모두 선택" in current and len(current) > 1:
            if "모두 선택" in prev:
                st.session_state.selected_cols1 = ["모두 선택"]
                st.session_state.warning_msg1 = "⚠️ '모두 선택' 상태에서는 개별 항목을 추가할 수 없습니다."
            else:
                st.session_state.selected_cols1 = prev
                st.session_state.warning_msg1 = "⚠️ 개별 항목이 선택된 상태에서는 '모두 선택'을 추가할 수 없습니다."
        else:
            st.session_state.warning_msg1 = ""

        st.session_state.prev_selected_cols1 = st.session_state.selected_cols1

    col_multi, col_extract = st.columns([4, 1])

    with col_multi:
        selected_columns = st.multiselect(
            "항목 선택",
            options=options_with_all,
            placeholder="원하는 항목을 골라주세요",
            label_visibility="collapsed",
            key="selected_cols1",
            on_change=check_selection1,
        )
        if st.session_state.warning_msg1:
            st.warning(st.session_state.warning_msg1)

    with col_extract:
        run_extract = st.button("추출", type="primary", use_container_width=True, key="run_extract1")

    if not run_extract:
        return

    if not selected_columns:
        st.error("최소 1개 이상의 추출 항목을 선택해주세요!")
        return

    my_bar = st.progress(0, text="데이터 추출을 시작합니다...")

    try:
        org = st.session_state.target_org1
        target_columns = ALL_SELECTABLE_COLUMNS if "모두 선택" in selected_columns else selected_columns

        # 기관별 필터 기능만 이 파일에서 적용합니다.
        # 실제 URL 수집/상세 HTML 수집/상세 파싱 하이퍼파라미터는 crawler_metadata.py의 검증값을 그대로 사용합니다.
        list_per_page = _safe_int(getattr(crawler_metadata, "LIST_PER_PAGE", 1000), 1000)
        engine_max_pages = _safe_int(getattr(crawler_metadata, "MAX_PAGES", 0), 0)
        engine_max_detail_items = _safe_int(getattr(crawler_metadata, "MAX_DETAIL_ITEMS", 0), 0)
        engine_detail_concurrency = _safe_int(getattr(crawler_metadata, "DETAIL_CONCURRENCY", 20), 20)
        headless = bool(getattr(crawler_metadata, "HEADLESS", True))

        if build_org_file_list_url is None:
            raise RuntimeError("기관별 FILE 목록 URL 생성 함수가 전달되지 않았습니다.")

        target_url = build_org_file_list_url(org, current_page=1, per_page=list_per_page)
        safe_org_name = org.replace("(", "_").replace(")", "")

        st.caption(f"수집 URL: {target_url}")
        my_bar.progress(5, text="기관별 수집을 시작합니다...")

        log_box = st.empty()
        status_box = st.empty()
        log_buffer = io.StringIO()

        def update_status(msg):
            status_box.info(msg)

        with contextlib.redirect_stdout(log_buffer):
            result = crawler_metadata.run_metadata_crawler_for_url(
                target_url=target_url,
                job_name=f"공공데이터_{safe_org_name}_기관별_메타데이터",
                max_pages=engine_max_pages,
                max_detail_items=engine_max_detail_items,
                list_per_page=list_per_page,
                detail_concurrency=engine_detail_concurrency,
                source_file_label="기관별수집",
                output_dir=None,
                save_outputs_to_disk=False,
                headless=headless,
                status_callback=update_status,
            )

        status_box.empty()
        log_text = log_buffer.getvalue()
        if log_text:
            log_box.text_area("수집 로그", log_text[-12000:], height=260)

        my_bar.progress(98, text="엑셀 파일 생성 중...")

        result_df = result.get("metadata_df", pd.DataFrame())
        fail_df = result.get("fail_df", pd.DataFrame())
        total_urls = len(result.get("items", []))

        if not result_df.empty and "최종순번" in result_df.columns:
            result_df = result_df.sort_values("최종순번", kind="stable")

        final_cols = [c for c in target_columns if c in result_df.columns]
        result_df = result_df[final_cols] if final_cols else pd.DataFrame(columns=target_columns)

        fail_cols = getattr(
            crawler_metadata,
            "FAIL_COLUMNS",
            ["수집시각", "단계", "파일데이터명", "URL", "최종순번", "조회수", "다운로드(바로가기)", "오류", "Traceback"],
        )
        if fail_df.empty:
            fail_df = pd.DataFrame(columns=fail_cols)
        else:
            fail_df = fail_df.reindex(columns=fail_cols)

        output = BytesIO()
        with pd.ExcelWriter(output, engine="xlsxwriter", engine_kwargs={"options": {"strings_to_urls": False}}) as writer:
            result_df.to_excel(writer, index=False, sheet_name="메타데이터")
            fail_df.to_excel(writer, index=False, sheet_name="실패로그")
        output.seek(0)

        timestamp = time.strftime("%Y%m%d_%H%M%S")
        file_name = f"공공데이터_{safe_org_name}_메타데이터_{timestamp}.xlsx"

        my_bar.empty()
        st.success(f"수집 완료! 상세 URL {total_urls}건 중 메타데이터 {len(result_df)}건, 실패 {len(fail_df)}건입니다.")
        if len(fail_df) > 0:
            st.warning("일부 상세페이지는 수집 실패했습니다. 다운로드한 엑셀의 [실패로그] 시트에서 URL과 오류 원인을 확인하세요.")
            preview_cols = [c for c in ["파일데이터명", "URL", "오류"] if c in fail_df.columns]
            if preview_cols:
                st.dataframe(fail_df[preview_cols], use_container_width=True)

        st.download_button(
            label="🌟 엑셀(Excel) 파일 다운로드",
            data=output,
            file_name=file_name,
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

    except Exception as e:
        my_bar.empty()
        st.error(f"🚨 오류 발생: {e}")
