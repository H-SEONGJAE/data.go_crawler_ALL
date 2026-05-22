# -*- coding: utf-8 -*-
"""기관별 파일데이터 조회수/다운로드수 수집 모듈."""
from __future__ import annotations

import asyncio
from pathlib import Path
from urllib.parse import urlparse

import requests
import pandas as pd
from playwright.async_api import async_playwright

from portal_common import build_chromium_launch_kwargs, build_file_list_url, clean_text


def _to_int(value):
    s = clean_text(value).replace(",", "")
    try:
        return int(s)
    except Exception:
        return None




def _collect_items_fallback(url: str, *, max_pages: int = 0, max_items: int = 0, list_per_page: int = 1000) -> list[dict]:
    """Playwright 접근이 차단된 환경에서 HTML 직접 요청/로컬 파일로 목록을 파싱하는 보조 경로."""
    import crawler_metadata as cm

    all_items: list[dict] = []
    seen = set()
    page_no = 1
    while True:
        if max_pages and page_no > max_pages:
            break
        list_url = cm.optimize_list_url(url, per_page=list_per_page, current_page=page_no)
        parsed = urlparse(list_url)
        if parsed.scheme == "file":
            html = Path(parsed.path).read_text(encoding="utf-8", errors="replace")
            page_url = list_url
        else:
            from portal_common import HEADERS
            res = requests.get(list_url, headers=HEADERS, timeout=20)
            res.raise_for_status()
            html = res.text
            page_url = str(res.url)
        items = cm.collect_dataset_links_from_html(html, page_url)
        if not items:
            break
        for item in items:
            key = item.get("detail_url") or item.get("title")
            if key in seen:
                continue
            seen.add(key)
            all_items.append(item)
            if max_items and len(all_items) >= max_items:
                return all_items
        page_no += 1
    return all_items

async def _collect_file_data_from_url_async(
    url: str,
    *,
    max_pages: int = 0,
    max_items: int = 0,
    list_per_page: int = 1000,
    headless: bool = True,
    status_callback=None,
) -> pd.DataFrame:
    """crawler_metadata의 검증된 목록 파서를 재사용해 FILE 목록 집계만 생성."""
    import crawler_metadata as cm

    def update(msg: str):
        if status_callback:
            status_callback(msg)
        print(msg, flush=True)

    update(f"목록 URL 수집 시작: {url}")
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(**build_chromium_launch_kwargs(headless))
            try:
                items = await cm.collect_list_items(
                    browser=browser,
                    target_url=url,
                    max_pages=int(max_pages or 0),
                    max_detail_items=int(max_items or 0),
                    list_per_page=int(list_per_page or 1000),
                )
            finally:
                await cm.safe_close_browser(browser)
    except Exception as e:
        update(f"Playwright 목록 수집 실패 → HTML fallback 시도: {repr(e)}")
        try:
            items = _collect_items_fallback(
                url,
                max_pages=int(max_pages or 0),
                max_items=int(max_items or 0),
                list_per_page=int(list_per_page or 1000),
            )
        except Exception as fallback_e:
            raise RuntimeError(
                "목록 수집에 실패했습니다. 로컬 네트워크/DNS/포털 접근 가능 여부와 "
                "Playwright Chromium 설치 상태를 확인하세요. "
                f"Playwright 오류={repr(e)} / fallback 오류={repr(fallback_e)}"
            ) from fallback_e

    rows = []
    seen = set()
    for idx, item in enumerate(items, start=1):
        title = clean_text(item.get("title") or item.get("raw_title"))
        detail_url = clean_text(item.get("detail_url"))
        view = item.get("조회수", "")
        download = item.get("다운로드(바로가기)", "") or item.get("다운로드수", "")
        key = detail_url or (title, view, download)
        if key in seen:
            continue
        seen.add(key)
        rows.append({
            "순번": len(rows) + 1,
            "데이터명": title,
            "조회수": _to_int(view),
            "다운로드수": _to_int(download),
            "상세페이지 URL": detail_url,
        })

    update(f"수집 완료: {len(rows)}건")
    return pd.DataFrame(rows, columns=["순번", "데이터명", "조회수", "다운로드수", "상세페이지 URL"])


def collect_file_data_from_url(
    url: str,
    status_callback=None,
    *,
    max_pages: int = 0,
    max_items: int = 0,
    list_per_page: int = 1000,
    headless: bool = True,
) -> pd.DataFrame:
    return asyncio.run(_collect_file_data_from_url_async(
        url,
        max_pages=max_pages,
        max_items=max_items,
        list_per_page=list_per_page,
        headless=headless,
        status_callback=status_callback,
    ))


def collect_file_data_from_org(
    org_name: str,
    status_callback=None,
    *,
    max_pages: int = 0,
    max_items: int = 0,
    list_per_page: int = 1000,
    headless: bool = True,
) -> pd.DataFrame:
    url = build_file_list_url(org_name, current_page=1, per_page=list_per_page)
    return collect_file_data_from_url(
        url,
        status_callback=status_callback,
        max_pages=max_pages,
        max_items=max_items,
        list_per_page=list_per_page,
        headless=headless,
    )


def save_stats_excel(df: pd.DataFrame, output_path: str | Path) -> str:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(output_path, engine="xlsxwriter", engine_kwargs={"options": {"strings_to_urls": False}}) as writer:
        df.to_excel(writer, index=False, sheet_name="FILE_집계")
    return str(output_path)
