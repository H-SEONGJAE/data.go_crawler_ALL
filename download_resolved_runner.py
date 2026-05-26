# -*- coding: utf-8 -*-
"""
검증된 상세 URL 목록을 직접 순회해 파일데이터 최신/과거 파일 다운로드.

기존 crawler_data.py의 핵심 상세페이지 다운로드 방식은 유지하되,
문제가 많았던 기관 목록 URL/페이지네이션 수집 단계를 Resolver 결과로 대체한다.
"""

from __future__ import annotations

import os
import re
import shutil
import time
from pathlib import Path
from typing import Dict, List

from playwright.sync_api import sync_playwright
from org_url_resolver import launch_chromium_robust


def clean_title(text: str) -> str:
    text = str(text or "").strip()
    text = re.sub(r"[\\/:*?\"<>|]", "_", text)
    text = re.sub(r"\s+", " ", text)
    return text or "unnamed"


def _safe_name(value: str) -> str:
    return clean_title(value or "기관")


def _download_latest(page, save_dir: Path, log: List[Dict]) -> None:
    # 공공데이터포털 상세페이지의 대표 다운로드 버튼 대응
    selectors = [
        "a:has-text('다운로드')",
        "button:has-text('다운로드')",
        "a[title*='다운로드']",
    ]
    clicked = False
    last_err = ""
    for selector in selectors:
        try:
            btn = page.query_selector(selector)
            if not btn:
                continue
            with page.expect_download(timeout=45000) as dl_info:
                page.evaluate("(el)=>el.click()", btn)
            dl = dl_info.value
            original = clean_title(dl.suggested_filename)
            dl.save_as(str(save_dir / original))
            log.append({"step": "latest_download", "file": original, "ok": True})
            clicked = True
            break
        except Exception as e:
            last_err = repr(e)
            continue
    if not clicked:
        log.append({"step": "latest_download", "ok": False, "error": last_err or "NO_DOWNLOAD_BUTTON"})


def _download_past_files(page, past_dir: Path, log: List[Dict]) -> None:
    try:
        links = page.query_selector_all("a[onclick*='fileDataDetail']")
        log.append({"step": "past_links_found", "count": len(links), "ok": True})
        for j, el in enumerate(links, start=1):
            try:
                onclick = el.get_attribute("onclick")
                if not onclick:
                    continue
                page.evaluate(onclick)
                page.wait_for_function(
                    """
                    () => {
                        const m = document.querySelector('#layer_data_infomation .file-meta-table-mobile');
                        return m && window.getComputedStyle(m).display !== 'none';
                    }
                    """,
                    timeout=9000,
                )
                modal = page.query_selector("#layer_data_infomation .file-meta-table-mobile")
                if not modal:
                    raise RuntimeError("PAST_MODAL_NOT_FOUND")

                csv_btns = modal.query_selector_all("a.button.white:has-text('CSV')")
                if csv_btns:
                    target_btn = csv_btns[-1]
                else:
                    fallback = modal.query_selector_all("a.button.white")
                    if not fallback:
                        raise RuntimeError("PAST_DOWNLOAD_BUTTON_NOT_FOUND")
                    target_btn = fallback[0]

                with page.expect_download(timeout=60000) as d2:
                    page.evaluate("(el)=>el.click()", target_btn)
                file = d2.value
                original = clean_title(file.suggested_filename)
                base, ext = os.path.splitext(original)
                new_name = f"{base}(과거{j}){ext}"
                file.save_as(str(past_dir / new_name))
                log.append({"step": "past_download", "index": j, "file": new_name, "ok": True})

                close = page.query_selector("#layer_data_infomation button.close")
                if close:
                    close.click()
                    time.sleep(0.2)
            except Exception as e:
                log.append({"step": "past_download", "index": j, "ok": False, "error": repr(e)})
                try:
                    close = page.query_selector("#layer_data_infomation button.close")
                    if close:
                        close.click()
                except Exception:
                    pass
    except Exception as e:
        log.append({"step": "past_collect", "ok": False, "error": repr(e)})


def run_download_from_resolution(
    resolution: Dict,
    output_root: str = "outputs",
    headless: bool = True,
    max_items: int = 0,
    make_zip: bool = True,
) -> Dict:
    detail_items = resolution.get("detail_items", [])
    if not detail_items:
        raise RuntimeError("검증된 상세 URL 목록이 없습니다. 먼저 기관 URL 검증을 완료하세요.")

    provider = _safe_name(resolution.get("selected_provider", "기관"))
    root_dir = Path(output_root) / f"{provider}_포털데이터"
    root_dir.mkdir(parents=True, exist_ok=True)

    log: List[Dict] = []
    processed_urls = set()
    target_items = detail_items[: int(max_items)] if max_items else detail_items

    with sync_playwright() as p:
        browser = launch_chromium_robust(p, headless=headless, auto_install=True)
        context = browser.new_context(accept_downloads=True, locale="ko-KR", viewport={"width": 1440, "height": 950})
        page = context.new_page()
        try:
            for idx, item in enumerate(target_items, start=1):
                title = clean_title(item.get("title", "") or f"dataset_{idx}")
                url = item.get("detail_url", "")
                if not url or url in processed_urls:
                    continue
                processed_urls.add(url)
                save_dir = root_dir / title
                past_dir = save_dir / "과거데이터"
                save_dir.mkdir(parents=True, exist_ok=True)
                past_dir.mkdir(parents=True, exist_ok=True)

                row_log = {"step": "open_detail", "index": idx, "title": title, "url": url}
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=30000)
                    page.wait_for_load_state("networkidle", timeout=15000)
                    time.sleep(0.5)
                    row_log["ok"] = True
                    log.append(row_log)
                    _download_latest(page, save_dir, log)
                    _download_past_files(page, past_dir, log)
                except Exception as e:
                    row_log["ok"] = False
                    row_log["error"] = repr(e)
                    log.append(row_log)
        finally:
            context.close()
            browser.close()

    zip_path = ""
    if make_zip:
        zip_path = shutil.make_archive(str(root_dir), "zip", str(root_dir))

    return {
        "output_dir": str(root_dir),
        "zip_path": zip_path,
        "processed_count": len(processed_urls),
        "log": log,
    }
