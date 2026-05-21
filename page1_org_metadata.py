import streamlit as st
import urllib.parse
import math
import time
import re
import asyncio
import httpx
import random
import pandas as pd
from bs4 import BeautifulSoup
from io import BytesIO
from pathlib import Path

from playwright.async_api import async_playwright
import crawler_metadata as cm

from streamlit_task_ui import start_task, request_stop, render_task_status, get_task_state, is_task_running, clear_task


def crawl_org_metadata(
    org,
    pages,
    target_columns,
    get_soup,
    format_tel_no,
    BASE_URL,
    HEADERS,
    TARGET_METADATA_KEYS,
    METADATA_KEY_MAP,
    status_callback=None,
    stop_event=None,
):
    """기관별 메타데이터 수집 작업.

    중요 수정사항:
    - 기존 page1_org_metadata.py의 단순 상세 파싱 대신 crawler_metadata.py의 목록/상세 파싱 엔진을 재사용합니다.
    - 파일데이터명 보정, 조회수/다운로드수, 상세 URL 후보, 전화번호, 컬럼목록, 비정상 상세페이지 방어 로직은
      crawler_metadata.py 기준으로 처리합니다.
    - Streamlit UI용 진행상황/중지 요청만 이 함수에서 감쌉니다.
    """

    def log(msg, current=None, total=None, level="info"):
        if status_callback:
            try:
                status_callback(msg, current=current, total=total, level=level)
            except TypeError:
                status_callback(msg)

    def should_stop():
        return bool(stop_event and stop_event.is_set())

    encoded_org = urllib.parse.quote(str(org).strip())
    target_url = (
        "https://www.data.go.kr/tcs/dss/selectDataSetList.do"
        f"?dType=FILE&sort=updtDt&currentPage=1&perPage=100&org={encoded_org}"
    )

    source_file_label = f"기관별수집_{org}"
    output_dir = Path("outputs") / "metadata" / cm.clean_filename(str(org))
    output_dir.mkdir(parents=True, exist_ok=True)

    # 기관별 수집은 검색 결과 페이지 수가 10페이지 단위로 잘리는 경우가 있어
    # 기존 pages 값에만 의존하지 않고 currentPage를 증가시키며 빈 페이지가 나올 때까지 수집합니다.
    list_per_page = 100
    max_pages = 0  # 0 = 빈 페이지가 나올 때까지
    max_detail_items = 0  # 0 = 제한 없음
    detail_concurrency = 12

    async def collect_org_list_items_stop_aware():
        all_items = []
        seen = set()

        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )
            context = await browser.new_context(
                locale="ko-KR",
                viewport={"width": 1400, "height": 900},
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                extra_http_headers={
                    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7"
                },
            )
            await cm.setup_route(context)
            page = await context.new_page()

            try:
                page_no = 1
                while True:
                    if should_stop():
                        log("⏹ 중지 요청 감지: 목록 URL 수집 중단", current=page_no - 1, total=len(all_items), level="warning")
                        break

                    if max_pages > 0 and page_no > max_pages:
                        break

                    list_url = cm.optimize_list_url(target_url, list_per_page, current_page=page_no)
                    log(f"목록 페이지 수집 중... page={page_no}, 누적 URL={len(all_items)}", current=page_no, total=max(page_no, int(pages or 0)))

                    await page.goto(list_url, wait_until="domcontentloaded", timeout=cm.PAGE_TIMEOUT_MS)
                    await cm.wait_list_ready(page)
                    await asyncio.sleep(random.uniform(cm.PAGE_JITTER_MIN_SEC, cm.PAGE_JITTER_MAX_SEC))

                    html = await page.content()
                    items = cm.collect_dataset_links_from_html(html, page.url)

                    if not items:
                        log(f"목록 페이지 종료 감지: page={page_no}에서 항목 없음", current=page_no, total=page_no, level="info")
                        break

                    added = 0
                    for item in items:
                        url = item.get("detail_url", "")
                        if url and url not in seen:
                            seen.add(url)
                            all_items.append(item)
                            added += 1
                            if max_detail_items > 0 and len(all_items) >= max_detail_items:
                                break

                    log(f"목록 page={page_no} 수집 완료: +{added}건, 누적 {len(all_items)}건", current=page_no, total=max(page_no, int(pages or 0)))

                    if max_detail_items > 0 and len(all_items) >= max_detail_items:
                        break

                    page_no += 1
            finally:
                await cm.safe_close_context(context)
                await cm.safe_close_browser(browser)

        return all_items

    async def collect_org_details_stop_aware(items):
        if cm.httpx is None:
            raise RuntimeError("httpx가 설치되어 있지 않습니다. pip install httpx 를 실행하세요.")

        queue = asyncio.Queue()
        for idx, item in enumerate(items, start=1):
            queue.put_nowait((idx, item))

        results = {
            "metadata_rows": [],
            "column_rows": [],
            "fail_rows": [],
        }

        lock = asyncio.Lock()
        block_lock = asyncio.Lock()
        block_state = {"count": 0, "cooldown_until": 0.0}
        total = len(items)
        completed = 0

        timeout = cm.httpx.Timeout(
            connect=8.0,
            read=max(8.0, cm.DETAIL_TIMEOUT_MS / 1000),
            write=8.0,
            pool=8.0,
        )
        limits = cm.httpx.Limits(
            max_connections=max(8, detail_concurrency * 2),
            max_keepalive_connections=max(4, detail_concurrency),
            keepalive_expiry=30.0,
        )

        async with cm.httpx.AsyncClient(
            headers=cm.build_http_headers(),
            timeout=timeout,
            limits=limits,
            follow_redirects=True,
            http2=False,
            verify=True,
        ) as client:
            async def worker(worker_id):
                nonlocal completed
                while not queue.empty():
                    if should_stop():
                        return
                    try:
                        seq, item = queue.get_nowait()
                    except asyncio.QueueEmpty:
                        return

                    result = await cm.fetch_detail_httpx_with_retry(
                        client=client,
                        item=item,
                        final_seq=seq,
                        source_file_label=source_file_label,
                        worker_id=worker_id,
                        block_state=block_state,
                        block_lock=block_lock,
                        defer_block=True,
                    )

                    async with lock:
                        if result.get("ok"):
                            results["metadata_rows"].append(result["metadata"])
                            results["column_rows"].extend(result.get("columns") or [])
                        else:
                            fail = result.get("fail")
                            if fail:
                                results["fail_rows"].append(fail)

                        completed += 1
                        if completed % 5 == 0 or completed == total:
                            log(
                                f"상세 메타데이터 수집 중... {completed}/{total}건 "
                                f"(성공 {len(results['metadata_rows'])} / 실패 {len(results['fail_rows'])})",
                                current=completed,
                                total=total,
                            )
                    queue.task_done()

            workers = [asyncio.create_task(worker(i + 1)) for i in range(max(1, detail_concurrency))]
            await asyncio.gather(*workers)

        return results

    async def run_async():
        log("기관별 상세 URL 수집을 시작합니다.", current=0, total=0)
        items = await collect_org_list_items_stop_aware()
        log(f"상세 URL 수집 완료: {len(items)}건", current=len(items), total=len(items), level="success")

        if not items:
            return {
                "df": pd.DataFrame(),
                "fail_df": pd.DataFrame(),
                "file_name": "",
                "rows": 0,
                "stopped": should_stop(),
            }

        if should_stop():
            return {
                "df": pd.DataFrame(),
                "fail_df": pd.DataFrame(),
                "file_name": "",
                "rows": 0,
                "stopped": True,
            }

        log(f"상세 메타데이터 수집을 시작합니다. 총 {len(items)}건", current=0, total=len(items))
        detail_results = await collect_org_details_stop_aware(items)

        metadata_df = pd.DataFrame(detail_results["metadata_rows"])
        fail_df = pd.DataFrame(detail_results["fail_rows"])

        if not metadata_df.empty and "최종순번" in metadata_df.columns:
            metadata_df = metadata_df.sort_values("최종순번", kind="stable")

        # 기존 UI의 선택 컬럼 순서를 유지하되, 누락 컬럼은 빈 컬럼으로 보강합니다.
        for col in target_columns:
            if col not in metadata_df.columns:
                metadata_df[col] = ""
        metadata_df = metadata_df[target_columns] if target_columns else metadata_df

        safe_org_name = str(org).replace("(", "_").replace(")", "")
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        file_name = f"공공데이터_{safe_org_name}_메타데이터_{timestamp}.xlsx"

        # Streamlit 다운로드 외에도 로컬 산출물을 남깁니다.
        try:
            out_path = output_dir / file_name
            cm.write_excel_no_url_warning(metadata_df, out_path, sheet_name="메타데이터")
            if not fail_df.empty:
                fail_path = output_dir / f"공공데이터_{safe_org_name}_메타데이터_실패로그_{timestamp}.xlsx"
                cm.write_excel_no_url_warning(fail_df, fail_path, sheet_name="실패로그")
        except Exception as e:
            log(f"로컬 결과 저장 실패: {e}", level="warning")

        return {
            "df": metadata_df,
            "fail_df": fail_df,
            "file_name": file_name,
            "rows": len(metadata_df),
            "fail_rows": len(fail_df),
            "stopped": should_stop(),
        }

    result = asyncio.run(run_async())

    if result.get("stopped"):
        log(
            f"⏹ 중지됨: 현재까지 {result.get('rows', 0)}건 수집",
            current=result.get("rows", 0),
            total=result.get("rows", 0),
            level="warning",
        )
    else:
        log(
            f"✅ 기관별 메타데이터 수집 완료: 성공 {result.get('rows', 0)}건 / 실패 {result.get('fail_rows', 0)}건",
            current=result.get("rows", 0),
            total=result.get("rows", 0),
            level="success",
        )

    return result

def render_tab2(get_soup, find_valid_org_name, format_tel_no, BASE_URL, HEADERS, ALL_SELECTABLE_COLUMNS, TARGET_METADATA_KEYS, METADATA_KEY_MAP):
    # '사용 방법' 안내 박스 (HTML/CSS 적용)
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
                <div style="font-size: 14px; color: #475569; line-height: 1.5;"><b>[추출 시작]</b> 후 진행상황을 확인하고, 필요 시 <b>[중지]</b>를 누릅니다.</div>
            </div>
        </div>
    </div>
    """
    st.markdown(guide_html, unsafe_allow_html=True)

    # 자동 교정 검색 로직
    st.markdown("**▪&nbsp; 제공기관명 입력** (예: 한국중부발전(주))")
    col_input, col_btn = st.columns([4, 1])

    with col_input:
        org_input1 = st.text_input(
            "제공기관(1번 탭)",
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
                st.success(f"✅ 검색 완료! 총 {total_pages}페이지(최대 {total_pages * 10}건)의 데이터가 발견되었습니다.")

    # 추출할 항목 선택
    if st.session_state.get("total_pages1", 0) > 0:
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

        col_multi, col_extract, col_stop = st.columns([3, 1, 1])

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

        task_key = "task_org_metadata"

        with col_extract:
            run_extract = st.button(
                "추출 시작",
                type="primary",
                use_container_width=True,
                key="run_extract1",
                disabled=is_task_running(task_key),
            )

        with col_stop:
            stop_clicked = st.button(
                "중지",
                use_container_width=True,
                key="stop_extract1",
                disabled=not is_task_running(task_key),
            )

        if stop_clicked:
            request_stop(task_key)
            st.warning("중지 요청을 보냈습니다. 현재 처리 중인 요청을 마친 뒤 종료합니다.")

        if run_extract:
            if not selected_columns:
                st.error("최소 1개 이상의 추출 항목을 선택해주세요!")
            else:
                clear_task(task_key)
                org = st.session_state.target_org1
                pages = st.session_state.total_pages1
                target_columns = ALL_SELECTABLE_COLUMNS if "모두 선택" in selected_columns else selected_columns
                start_task(
                    task_key,
                    crawl_org_metadata,
                    org,
                    pages,
                    target_columns,
                    get_soup,
                    format_tel_no,
                    BASE_URL,
                    HEADERS,
                    TARGET_METADATA_KEYS,
                    METADATA_KEY_MAP,
                    task_name=f"{org} 기관별 메타데이터 수집",
                )
                st.rerun()

        state = render_task_status(task_key, title="기관별 메타데이터 수집 진행상황")
        if state and state.get("status") in ["done", "stopped"] and state.get("result"):
            result = state["result"]
            df = result.get("df")
            if df is not None and not df.empty:
                st.success(f"수집 결과: 총 {len(df)}건")
                st.dataframe(df, use_container_width=True)

                output = BytesIO()
                with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
                    df.to_excel(writer, index=False, sheet_name="메타데이터")
                output.seek(0)

                st.download_button(
                    label="📥 엑셀(Excel) 파일 다운로드",
                    data=output,
                    file_name=result.get("file_name") or "기관별_메타데이터.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    use_container_width=True,
                )
            else:
                st.warning("수집된 데이터가 없습니다.")
