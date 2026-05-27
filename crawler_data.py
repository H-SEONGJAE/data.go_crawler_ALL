# -*- coding: utf-8 -*-
"""
crawler_data.py

기존 EXE용 기관별 파일데이터 다운로드 엔진을 Streamlit import/호출용으로 변경한 버전입니다.
- Chromium 고정 경로(get_chromium_path) 제거
- 기관명 + 기관 검색 URL만으로 실행
- 목록 페이지는 직접 currentPage를 증가시키며 수집하므로 끝 페이지 무한반복 방지
- 수집 완료 후 ZIP 파일 생성
"""

import os
import re
import time
import shutil
import urllib.parse
from pathlib import Path
from typing import Callable, Optional

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

BASE_URL = "https://www.data.go.kr"
BASE_LIST_URL = "https://www.data.go.kr/tcs/dss/selectDataSetList.do"


def clean_text(value) -> str:
    if value is None:
        return ""
    text = str(value).replace("\xa0", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def clean_title(text: str) -> str:
    text = clean_text(text)
    text = re.sub(r"[\\/:*?\"<>|]", "_", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip() or "unnamed"


def build_org_file_list_url_default(org_name: str, current_page: int = 1, per_page: int = 100) -> str:
    params = {
        "dType": "FILE",
        "keyword": "",
        "detailKeyword": "",
        "publicDataPk": "",
        "recmSe": "N",
        "detailText": "",
        "relatedKeyword": "",
        "commaNotInData": "",
        "commaAndData": "",
        "commaOrData": "",
        "must_not": "",
        "tabId": "",
        "dataSetCoreTf": "",
        "coreDataNm": "",
        "sort": "updtDt",
        "relRadio": "",
        "orgFullName": org_name,
        "orgFilter": org_name,
        "org": org_name,
        "orgSearch": "",
        "currentPage": str(current_page),
        "perPage": str(per_page),
        "brm": "",
        "instt": "",
        "svcType": "",
        "kwrdArray": "",
        "extsn": "",
        "coreDataNmArray": "",
        "conditionType": "search",
        "operator": "AND",
        "pblonsipScopeCode": "PBDE07",
    }
    return BASE_LIST_URL + "?" + urllib.parse.urlencode(params, doseq=True)


def make_unique_path(path: str) -> str:
    p = Path(path)
    if not p.exists():
        return str(p)
    stem = p.stem
    suffix = p.suffix
    parent = p.parent
    n = 1
    while True:
        candidate = parent / f"{stem}({n}){suffix}"
        if not candidate.exists():
            return str(candidate)
        n += 1


def update_status(callback, message: str):
    if callback:
        callback(message)


# ==========================================================
# 1. 목록 URL 수집
# ==========================================================

def extract_datasets_from_current_page(page, page_url: str):
    try:
        page.wait_for_selector("div.result-list ul li", timeout=8000)
    except PlaywrightTimeoutError:
        return []

    # 렌더링 안정화
    last_count = -1
    stable_round = 0
    for _ in range(12):
        items = page.query_selector_all("div.result-list ul li")
        count = len(items)
        if count == last_count:
            stable_round += 1
            if stable_round >= 3:
                break
        else:
            stable_round = 0
        last_count = count
        time.sleep(0.1)

    datasets = []
    for li in page.query_selector_all("div.result-list ul li"):
        a = li.query_selector("a[href*='/data/'], a[href*='/dataset/']")
        if not a:
            continue

        href = clean_text(a.get_attribute("href"))
        if not href:
            continue
        href = urllib.parse.urljoin(BASE_URL, href)
        if "fileData.do" not in href and not re.search(r"/(?:data|dataset)/\d+", href):
            continue

        title_el = li.query_selector("span.title") or li.query_selector(".title")
        raw_title = title_el.inner_text().strip() if title_el else a.inner_text().strip()
        title = clean_title(raw_title)

        datasets.append({
            "title": title,
            "href": href,
            "source_list_url": page_url,
        })

    return datasets


def collect_dataset_links(
    page,
    inst_name: str,
    list_url_builder: Optional[Callable[[str, int, int], str]] = None,
    per_page: int = 100,
    max_pages: int = 1000,
    status_callback=None,
):
    builder = list_url_builder or build_org_file_list_url_default
    datasets = []
    seen_urls = set()
    seen_page_signatures = set()

    for page_no in range(1, max_pages + 1):
        list_url = builder(inst_name, page_no, per_page)
        update_status(status_callback, f"📄 파일데이터 목록 {page_no}페이지 수집 중...")

        page.goto(list_url, wait_until="domcontentloaded", timeout=30000)
        try:
            page.wait_for_load_state("networkidle", timeout=10000)
        except Exception:
            pass
        time.sleep(0.5)

        page_items = extract_datasets_from_current_page(page, list_url)
        signature = tuple(d["href"] for d in page_items)

        if signature and signature in seen_page_signatures:
            update_status(status_callback, "📌 같은 목록 페이지가 반복되어 끝 페이지로 판단하고 종료합니다.")
            break
        if signature:
            seen_page_signatures.add(signature)

        if not page_items:
            update_status(status_callback, "📌 더 이상 파일데이터 목록이 없어 종료합니다.")
            break

        new_count = 0
        for d in page_items:
            if d["href"] in seen_urls:
                continue
            seen_urls.add(d["href"])
            datasets.append(d)
            new_count += 1

        update_status(status_callback, f"✅ {page_no}페이지 완료: 신규 {new_count}건 / 누적 {len(datasets)}건")

        if new_count == 0:
            update_status(status_callback, "📌 신규 상세 URL이 없어 끝 페이지로 판단하고 종료합니다.")
            break

    return datasets


# ==========================================================
# 2. 상세페이지 다운로드
# ==========================================================

def click_first_download_button(page, save_dir: str, status_callback=None):
    try:
        # 공공데이터포털 상세페이지의 현재 데이터 다운로드 버튼
        candidates = [
            "a:has-text('다운로드')",
            "button:has-text('다운로드')",
        ]
        target_selector = None
        for sel in candidates:
            try:
                if page.locator(sel).count() > 0:
                    target_selector = sel
                    break
            except Exception:
                continue

        if not target_selector:
            update_status(status_callback, "   ⚠ 현재데이터 다운로드 버튼 없음")
            return 0

        with page.expect_download(timeout=60000) as dl_info:
            page.locator(target_selector).first.click()
        dl = dl_info.value
        original = clean_title(dl.suggested_filename)
        out_path = make_unique_path(os.path.join(save_dir, original))
        dl.save_as(out_path)
        update_status(status_callback, f"   ✅ 현재데이터 저장 → {os.path.basename(out_path)}")
        return 1
    except Exception as e:
        update_status(status_callback, f"   ⚠ 현재데이터 실패: {e}")
        return 0


def download_past_files(page, past_dir: str, status_callback=None):
    saved = 0
    try:
        links = page.query_selector_all("a[onclick*='fileDataDetail']")
        update_status(status_callback, f"   📂 과거데이터 {len(links)}건 확인")

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
                        return m && window.getComputedStyle(m).display === 'block';
                    }
                    """,
                    timeout=10000,
                )

                modal = page.query_selector("#layer_data_infomation .file-meta-table-mobile")
                if not modal:
                    continue

                csv_btns = modal.query_selector_all("a.button.white:has-text('CSV')")
                if csv_btns:
                    target_btn = csv_btns[-1]
                else:
                    buttons = modal.query_selector_all("a.button.white")
                    if not buttons:
                        update_status(status_callback, "   ⚠ 과거데이터 다운로드 버튼 없음 → 패스")
                        close = page.query_selector("#layer_data_infomation button.close")
                        if close:
                            close.click()
                        continue
                    target_btn = buttons[0]

                with page.expect_download(timeout=60000) as d2:
                    page.evaluate("(el) => el.click()", target_btn)
                file = d2.value

                original = clean_title(file.suggested_filename)
                base, ext = os.path.splitext(original)
                new_name = f"{base}(과거{j}){ext}"
                out_path = make_unique_path(os.path.join(past_dir, new_name))
                file.save_as(out_path)
                saved += 1
                update_status(status_callback, f"   ✅ 과거데이터[{j}] 저장 → {os.path.basename(out_path)}")

                close = page.query_selector("#layer_data_infomation button.close")
                if close:
                    close.click()
                time.sleep(0.2)

            except Exception as e:
                update_status(status_callback, f"   ⚠ 과거데이터[{j}] 실패: {e}")
                try:
                    close = page.query_selector("#layer_data_infomation button.close")
                    if close:
                        close.click()
                except Exception:
                    pass
                continue

    except Exception as e:
        update_status(status_callback, f"   ⚠ 과거데이터 처리 오류: {e}")

    return saved


# ==========================================================
# 3. Streamlit 호출용 메인 함수
# ==========================================================

def collect_portal_files(
    inst_name: str,
    org_url: Optional[str] = None,
    list_url_builder: Optional[Callable[[str, int, int], str]] = None,
    include_past: bool = True,
    output_root: str = ".",
    status_callback=None,
    per_page: int = 100,
    max_pages: int = 1000,
    headless: bool = True,
) -> str:
    """
    기관명 기준으로 파일데이터와 과거데이터를 다운로드하고 ZIP 경로를 반환합니다.
    org_url은 화면 표시/호환용이며, 실제 목록 수집은 기관명 기반 FILE URL을 직접 생성합니다.
    """
    safe_inst = clean_title(inst_name)
    root_dir = os.path.abspath(os.path.join(output_root, f"{safe_inst}_포털데이터"))

    if os.path.exists(root_dir):
        shutil.rmtree(root_dir, ignore_errors=True)
    os.makedirs(root_dir, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context(accept_downloads=True, locale="ko-KR")
        page = context.new_page()

        try:
            if org_url:
                update_status(status_callback, "🔗 기관 검색 URL 접속 확인 중...")
                page.goto(org_url, wait_until="domcontentloaded", timeout=30000)
                try:
                    page.wait_for_load_state("networkidle", timeout=10000)
                except Exception:
                    pass
                time.sleep(0.5)

            datasets = collect_dataset_links(
                page=page,
                inst_name=inst_name,
                list_url_builder=list_url_builder,
                per_page=per_page,
                max_pages=max_pages,
                status_callback=status_callback,
            )

            update_status(status_callback, f"📑 다운로드 대상 데이터셋 {len(datasets)}건 수집 완료")

            for idx, d in enumerate(datasets, start=1):
                title = clean_title(d["title"])
                href = d["href"]
                save_dir = os.path.join(root_dir, title)
                past_dir = os.path.join(save_dir, "과거데이터")
                os.makedirs(save_dir, exist_ok=True)
                if include_past:
                    os.makedirs(past_dir, exist_ok=True)

                update_status(status_callback, f"\n📂 [{idx}/{len(datasets)}] {title}")
                update_status(status_callback, f"🔗 {href}")

                try:
                    page.goto(href, wait_until="domcontentloaded", timeout=30000)
                    try:
                        page.wait_for_load_state("networkidle", timeout=12000)
                    except Exception:
                        pass
                    time.sleep(0.4)

                    click_first_download_button(page, save_dir, status_callback=status_callback)
                    if include_past:
                        download_past_files(page, past_dir, status_callback=status_callback)

                except Exception as e:
                    update_status(status_callback, f"   ⚠ 상세페이지 처리 실패: {e}")
                    continue

        finally:
            try:
                context.close()
            except Exception:
                pass
            try:
                browser.close()
            except Exception:
                pass

    zip_path = shutil.make_archive(root_dir, "zip", root_dir)
    update_status(status_callback, f"🎉 전체 다운로드 완료 → {zip_path}")
    return zip_path


# 기존 EXE config 호출 호환용

def main(inst_name, org_url):
    return collect_portal_files(inst_name=inst_name, org_url=org_url, include_past=True)


if __name__ == "__main__":
    import json

    with open("config.json", "r", encoding="utf-8") as f:
        config = json.load(f)

    main(config["inst_name"], config["org_url"])
