# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import re
import shutil
import time
from pathlib import Path
from typing import Dict, List, Set, Tuple
from urllib.parse import urljoin

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError


def clean_title(text: str) -> str:
    text = str(text or "").strip()
    text = re.sub(r"[\\/:*?\"<>|]", "_", text)
    text = re.sub(r"\s+", " ", text)
    return text or "unnamed"


def find_system_browser() -> str:
    candidates = [
        os.environ.get("CHROME_EXECUTABLE_PATH", ""),
        os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH", ""),
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        "/usr/bin/google-chrome",
        "/usr/bin/google-chrome-stable",
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
    ]
    for p in candidates:
        if p and Path(p).exists():
            return p
    return ""


def launch_browser(pw, headless=True):
    try:
        return pw.chromium.launch(headless=headless)
    except Exception as first:
        exe = find_system_browser()
        if exe:
            return pw.chromium.launch(headless=headless, executable_path=exe)
        raise RuntimeError(
            "Playwright 브라우저 실행 파일이 없습니다. 파일 다운로드 기능을 쓰려면 CMD에서 `python -m playwright install chromium`을 실행하세요.\n"
            f"원본 오류: {first}"
        )


def has_file_list(page) -> bool:
    try:
        return page.query_selector("div.result-list ul li, #fileDataList ul li") is not None
    except Exception:
        return False


def ensure_file_list_page(page, url: str):
    page.goto(url, wait_until="domcontentloaded", timeout=30000)
    try:
        page.wait_for_load_state("networkidle", timeout=10000)
    except Exception:
        pass
    time.sleep(1.0)
    if has_file_list(page):
        return
    # 기관 홈/전체 탭 형태로 들어간 경우만 파일데이터 탭 클릭
    for selector in ["a.dtype-tab[data-type='FILE']", "a:has-text('파일데이터')", "button:has-text('파일데이터')"]:
        try:
            el = page.query_selector(selector)
            if el:
                el.click()
                try:
                    page.wait_for_load_state("networkidle", timeout=10000)
                except Exception:
                    pass
                page.wait_for_selector("div.result-list ul li, #fileDataList ul li", timeout=15000)
                return
        except Exception:
            continue
    page.wait_for_selector("div.result-list ul li, #fileDataList ul li", timeout=15000)


def load_items(page) -> List[Dict[str, str]]:
    page.wait_for_selector("div.result-list ul li, #fileDataList ul li", timeout=20000)
    time.sleep(0.5)
    items = page.query_selector_all("div.result-list ul li, #fileDataList ul li")
    datasets = []
    seen = set()
    for li in items:
        a = None
        for cand in li.query_selector_all("a[href*='/data/'], a[href*='/dataset/']"):
            href = cand.get_attribute("href") or ""
            if "/data/" in href or "/dataset/" in href:
                a = cand
                break
        if not a:
            continue
        href = a.get_attribute("href") or ""
        href = urljoin("https://www.data.go.kr", href)
        if href in seen:
            continue
        seen.add(href)
        title_el = li.query_selector("span.title, .title")
        raw = title_el.inner_text().strip() if title_el else a.inner_text().strip()
        raw = raw.replace("미리보기", "")
        datasets.append({"title": clean_title(raw), "href": href})
    return datasets


def page_signature(datasets: List[Dict[str, str]]) -> str:
    return "|".join(d.get("href", "") for d in datasets[:5])


def get_first_key(page) -> str:
    try:
        data = load_items(page)
        return page_signature(data[:1])
    except Exception:
        return ""


def goto_next(page) -> bool:
    before = get_first_key(page)
    # 숫자 페이지 다음 요소 우선
    try:
        curr = page.query_selector("nav.pagination strong.active")
        if curr:
            next_el = curr.evaluate_handle("node => node.nextElementSibling")
            if next_el:
                tag = next_el.get_property("tagName").json_value().lower()
                if tag == "a":
                    page.evaluate("el => el.click()", next_el)
                    time.sleep(1.0)
                    after = get_first_key(page)
                    return bool(after and after != before)
    except Exception:
        pass
    # 그룹 다음 버튼 보조
    for selector in ["a.control.next", "a.next", "button.next"]:
        try:
            el = page.query_selector(selector)
            if el:
                el.click()
                time.sleep(1.2)
                after = get_first_key(page)
                return bool(after and after != before)
        except Exception:
            continue
    return False


def download_current_file(page, save_dir: str) -> Tuple[bool, str]:
    try:
        with page.expect_download(timeout=50000) as dl_info:
            # 상세페이지의 다운로드 버튼은 여러 형태가 있어 텍스트 기반으로 우선 클릭
            btn = page.query_selector("a:has-text('다운로드'), button:has-text('다운로드')")
            if not btn:
                raise RuntimeError("현재데이터 다운로드 버튼 없음")
            btn.click()
        dl = dl_info.value
        name = dl.suggested_filename
        dl.save_as(os.path.join(save_dir, name))
        return True, name
    except Exception as e:
        return False, repr(e)


def download_past_files(page, past_dir: str) -> List[str]:
    saved = []
    try:
        links = page.query_selector_all("a[onclick*='fileDataDetail']")
        for j, el in enumerate(links, start=1):
            try:
                onclick = el.get_attribute("onclick")
                if onclick:
                    page.evaluate(onclick)
                else:
                    page.evaluate("el => el.click()", el)
                page.wait_for_function("""
                    () => {
                        const m = document.querySelector('#layer_data_infomation .file-meta-table-mobile');
                        return m && window.getComputedStyle(m).display !== 'none';
                    }
                """, timeout=8000)
                modal = page.query_selector("#layer_data_infomation .file-meta-table-mobile")
                btns = modal.query_selector_all("a.button.white:has-text('CSV')") if modal else []
                target = btns[-1] if btns else None
                if not target and modal:
                    fallback = modal.query_selector_all("a.button.white")
                    target = fallback[0] if fallback else None
                if not target:
                    close = page.query_selector("#layer_data_infomation button.close")
                    if close:
                        close.click()
                    continue
                with page.expect_download(timeout=60000) as dlinfo:
                    page.evaluate("el => el.click()", target)
                f = dlinfo.value
                original = f.suggested_filename
                base, ext = os.path.splitext(original)
                new_name = f"{base}(과거{j}){ext}"
                f.save_as(os.path.join(past_dir, new_name))
                saved.append(new_name)
                close = page.query_selector("#layer_data_infomation button.close")
                if close:
                    close.click()
                time.sleep(0.3)
            except Exception:
                try:
                    close = page.query_selector("#layer_data_infomation button.close")
                    if close:
                        close.click()
                except Exception:
                    pass
                continue
    except Exception:
        pass
    return saved


def run_download_crawler(provider_filedata_url: str, provider_name: str, headless: bool = True, max_pages: int = 0, log_callback=None) -> Dict:
    if not provider_filedata_url:
        raise ValueError("provider_filedata_url이 비어 있습니다.")
    def log(msg):
        print(msg)
        if log_callback:
            log_callback(msg)

    root_dir = f"{clean_title(provider_name)}_포털데이터"
    os.makedirs(root_dir, exist_ok=True)
    processed_urls: Set[str] = set()
    visited_pages: Set[str] = set()
    total_files = 0
    page_no = 1

    with sync_playwright() as pw:
        browser = launch_browser(pw, headless=headless)
        context = browser.new_context(accept_downloads=True, locale="ko-KR", viewport={"width": 1440, "height": 950})
        page = context.new_page()
        try:
            ensure_file_list_page(page, provider_filedata_url)
            while True:
                if max_pages and page_no > int(max_pages):
                    log(f"최대 페이지 {max_pages} 도달 → 종료")
                    break
                list_page_url = page.url
                datasets = load_items(page)
                sig = page_signature(datasets)
                if not datasets:
                    log("목록 데이터가 없어 종료")
                    break
                if sig in visited_pages:
                    log("이미 처리한 목록 페이지가 다시 나타나 종료")
                    break
                visited_pages.add(sig)
                log(f"페이지 {page_no} 처리 시작: {len(datasets)}건")
                for idx, d in enumerate(datasets, start=1):
                    title = d["title"]
                    href = d["href"]
                    if href in processed_urls:
                        continue
                    processed_urls.add(href)
                    save_dir = os.path.join(root_dir, title)
                    past_dir = os.path.join(save_dir, "과거데이터")
                    os.makedirs(save_dir, exist_ok=True)
                    os.makedirs(past_dir, exist_ok=True)
                    log(f"[{page_no}-{idx}] {title}")
                    try:
                        page.goto(href, wait_until="domcontentloaded", timeout=30000)
                        try:
                            page.wait_for_load_state("networkidle", timeout=10000)
                        except Exception:
                            pass
                        ok, name = download_current_file(page, save_dir)
                        if ok:
                            total_files += 1
                            log(f"현재데이터 저장: {name}")
                        past_saved = download_past_files(page, past_dir)
                        total_files += len(past_saved)
                        if past_saved:
                            log(f"과거데이터 저장: {len(past_saved)}건")
                    except Exception as e:
                        log(f"다운로드 실패: {repr(e)}")
                    # 목록 복귀 안정화
                    try:
                        page.goto(list_page_url, wait_until="domcontentloaded", timeout=30000)
                        page.wait_for_selector("div.result-list ul li, #fileDataList ul li", timeout=15000)
                    except Exception:
                        ensure_file_list_page(page, list_page_url)
                if not goto_next(page):
                    log("다음 페이지 없음 또는 목록 변화 없음 → 종료")
                    break
                page_no += 1
        finally:
            context.close()
            browser.close()
    zip_path = shutil.make_archive(root_dir, "zip", root_dir)
    return {"root_dir": root_dir, "zip_path": zip_path, "processed_datasets": len(processed_urls), "downloaded_files": total_files}
