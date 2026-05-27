# -*- coding: utf-8 -*-
"""
page1_org_metadata.py

메타데이터 크롤링 > 기관별 수집 탭.
기관명 검색 URL 생성 및 상세 URL 수집은 main.py에서 전달받은 공통 함수를 사용합니다.
"""

import asyncio
import random
import re
import time
from io import BytesIO

import httpx
import pandas as pd
import streamlit as st
from bs4 import BeautifulSoup


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

    st.markdown("**▪&nbsp; 제공기관명 입력** (예: 한국중부발전(주))")
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
                st.error("❌ 검색 결과가 없습니다. 기관명을 다시 확인해주세요.")
            else:
                if exact_org_name != org_input1.strip():
                    st.info(f"💡 '{exact_org_name}'(으)로 자동 변환하여 검색했습니다.")
                st.success(f"✅ 검색 완료! 총 {total_pages}페이지의 파일데이터가 발견되었습니다.")

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
        pages = int(st.session_state.total_pages1 or 0)

        target_columns = ALL_SELECTABLE_COLUMNS if "모두 선택" in selected_columns else selected_columns

        my_bar.progress(5, text="기관별 목록 URL 수집 중...")

        if collect_detail_urls_by_org is not None:
            detail_urls = collect_detail_urls_by_org(
                org_name=org,
                total_pages=pages,
                progress_callback=lambda done, total, msg: my_bar.progress(
                    min(9, 5 + int((done / max(total, 1)) * 4)), text=msg
                ),
            )
        else:
            # 비상 fallback: main.py 공통 함수가 전달되지 않은 경우 기존 방식으로 수집
            detail_urls = []
            per_page = 100
            fast_total_pages = max(1, (pages * 10 + per_page - 1) // per_page)
            for page_no in range(1, fast_total_pages + 1):
                if build_org_file_list_url:
                    list_url = build_org_file_list_url(org, page_no, per_page)
                else:
                    raise RuntimeError("build_org_file_list_url 함수가 전달되지 않았습니다.")
                soup = get_soup(list_url)
                for a in soup.select("a[href*='/data/'], a[href*='/dataset/']"):
                    href = a.get("href", "")
                    if "fileData.do" in href or re.search(r"/(?:data|dataset)/\d+", href):
                        import urllib.parse
                        full_url = urllib.parse.urljoin(BASE_URL, href)
                        if full_url not in detail_urls:
                            detail_urls.append(full_url)

        total_urls = len(detail_urls)
        if total_urls == 0:
            st.error("수집할 상세 URL이 없습니다. 기관명이나 사이트 상태를 확인해주세요.")
            my_bar.empty()
            return

        async def fetch_and_parse(client, url):
            metadata = {key: "" for key in TARGET_METADATA_KEYS}
            metadata["상세페이지 URL"] = url
            metadata["컬럼목록"] = ""
            try:
                await asyncio.sleep(random.uniform(0.1, 0.3))
                res = await client.get(url)
                res.raise_for_status()
                soup = BeautifulSoup(res.text, "lxml")

                target_table = next((table for table in soup.select("table") if "파일데이터명" in str(table)), None)
                if target_table:
                    for tr in target_table.select("tr"):
                        cells = tr.find_all(["th", "td"], recursive=False)
                        if not cells:
                            cells = tr.find_all(["th", "td"])
                        i = 0
                        while i < len(cells) - 1:
                            key = re.sub(r"\s+", "", cells[i].get_text()).replace(":", "").replace("*", "")
                            value = re.sub(r"\s+", " ", cells[i + 1].get_text()).strip()
                            mapped_key = METADATA_KEY_MAP.get(key)
                            if mapped_key in metadata:
                                metadata[mapped_key] = value
                            i += 2

                if not metadata.get("관리부서 전화번호"):
                    tel_tag = soup.select_one("#telNo, #telNo1")
                    if tel_tag:
                        tel_text = tel_tag.get_text(strip=True)
                        if tel_text:
                            metadata["관리부서 전화번호"] = tel_text

                if not metadata.get("관리부서 전화번호"):
                    html_text = res.text
                    tel_match = re.search(r"var\s+telNo\s*=\s*['\"]([^'\"]+)['\"]", html_text)
                    if tel_match:
                        metadata["관리부서 전화번호"] = format_tel_no(tel_match.group(1))

                wrap = soup.select_one("#column-def-table-wrap")
                if wrap:
                    for table in wrap.select("table"):
                        if "항목명" not in str(table):
                            continue
                        trs = table.select("tr")
                        if len(trs) > 1:
                            headers = [re.sub(r"\s+", "", th.get_text()) for th in trs[0].select("th, td")]
                            item_idx = next((i for i, h in enumerate(headers) if "항목명" in h), -1)
                            if item_idx != -1:
                                cols = [
                                    re.sub(r"\s+", " ", tr.select("th, td")[item_idx].get_text()).strip()
                                    for tr in trs[1:]
                                    if len(tr.select("th, td")) > item_idx
                                ]
                                metadata["컬럼목록"] = ", ".join(list(dict.fromkeys([
                                    c for c in cols if c and c not in ["정보시스템명", "DB명", "Table명", "코드"]
                                ])))
                                break
            except Exception:
                pass
            return metadata

        async def run_concurrent_scraping(urls):
            queue = asyncio.Queue()
            for u in urls:
                queue.put_nowait(u)

            results = []
            total = len(urls)
            completed = 0

            limits = httpx.Limits(max_connections=20, max_keepalive_connections=10)
            timeout = httpx.Timeout(20.0)
            concurrency = 15

            async with httpx.AsyncClient(headers=HEADERS, limits=limits, timeout=timeout, verify=False, follow_redirects=True) as client:
                async def worker():
                    nonlocal completed
                    while not queue.empty():
                        try:
                            url = queue.get_nowait()
                        except asyncio.QueueEmpty:
                            return

                        data = await fetch_and_parse(client, url)
                        if data:
                            results.append(data)

                        completed += 1
                        progress_percent = 10 + int((completed / total) * 85)
                        my_bar.progress(progress_percent, text=f"데이터 추출 중... ({completed}/{total} 완료)")
                        queue.task_done()

                workers = [asyncio.create_task(worker()) for _ in range(concurrency)]
                await asyncio.gather(*workers)

            return results

        my_bar.progress(10, text=f"상세페이지 파싱 준비 중... (총 {total_urls}건)")
        rows = asyncio.run(run_concurrent_scraping(detail_urls))
        my_bar.progress(98, text="엑셀 파일 생성 중...")

        result_df = pd.DataFrame(rows)
        final_cols = [c for c in target_columns if c in result_df.columns]
        result_df = result_df[final_cols]

        output = BytesIO()
        with pd.ExcelWriter(output, engine="xlsxwriter", engine_kwargs={"options": {"strings_to_urls": False}}) as writer:
            result_df.to_excel(writer, index=False, sheet_name="메타데이터")
        output.seek(0)

        safe_org_name = org.replace("(", "_").replace(")", "")
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        file_name = f"공공데이터_{safe_org_name}_메타데이터_{timestamp}.xlsx"

        my_bar.empty()
        st.success(f"수집 완료! 총 {len(rows)}건의 데이터를 추출했습니다.")

        st.download_button(
            label="📥 엑셀(Excel) 파일 다운로드",
            data=output,
            file_name=file_name,
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

    except Exception as e:
        my_bar.empty()
        st.error(f"🚨 오류 발생: {e}")
