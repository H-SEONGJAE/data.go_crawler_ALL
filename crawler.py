import pandas as pd
import time

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    StaleElementReferenceException,
    TimeoutException,
)


def collect_file_data_from_url(url: str, status_callback=None, stop_event=None) -> pd.DataFrame:
    results = []
    seen_keys = set()

    def update(msg, current=None, total=None, level="info"):
        if status_callback:
            try:
                status_callback(msg, current=current, total=total, level=level)
            except TypeError:
                status_callback(msg)

    def should_stop():
        return bool(stop_event and stop_event.is_set())

    chrome_options = Options()
    chrome_options.add_argument("--headless=new")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--window-size=1920,1080")

    driver = webdriver.Chrome(options=chrome_options)
    wait = WebDriverWait(driver, 15)

    try:
        # =========================
        # URL 접속
        # =========================
        update("🔗 URL 접속 중...")
        driver.get(url)

        # =========================
        # FILE 탭 진입
        # =========================
        update("📂 FILE 탭 진입 중...")
        file_tab = wait.until(
            EC.element_to_be_clickable(
                (By.CSS_SELECTOR, 'a.dtype-tab[data-type="FILE"]')
            )
        )
        driver.execute_script("arguments[0].click();", file_tab)

        wait.until(
            EC.presence_of_element_located(
                (By.CSS_SELECTOR, 'li#dTypeFILE.on')
            )
        )

        # =========================
        # ✅ 1번 페이지 선수집 (이미 로드됨)
        # =========================
        if should_stop():
            update("⏹ 중지 요청 감지: URL 접속 후 종료")
            return pd.DataFrame(results)

        update("📄 페이지 1 수집 중 (초기 페이지)")

        wait.until(
            EC.presence_of_element_located(
                (By.CSS_SELECTOR, "#fileDataList ul li")
            )
        )

        first_page_items = driver.find_elements(
            By.CSS_SELECTOR, "#fileDataList ul li"
        )

        for li in first_page_items:
            if should_stop():
                update("⏹ 중지 요청 감지: 1페이지 수집 중단", current=len(results), total=0)
                return pd.DataFrame(results)
            try:
                title = (
                    li.find_element(By.TAG_NAME, "a")
                    .text.replace("미리보기", "")
                    .strip()
                )

                view = li.find_element(
                    By.XPATH,
                    './/span[text()="조회수"]/following-sibling::span'
                ).text
                download = li.find_element(
                    By.XPATH,
                    './/span[text()="다운로드"]/following-sibling::span'
                ).text

                key = (title, view, download)
                if key in seen_keys:
                    continue
                seen_keys.add(key)

                results.append({
                    "데이터명": title,
                    "조회수": int(view.replace(",", "")),
                    "다운로드수": int(download.replace(",", ""))
                })
            except Exception:
                continue

        # =========================
        # 페이지 그룹 순회 (2페이지부터)
        # =========================
        page_group = 1
        prev_page_numbers = None
        prev_total_count = len(results)

        while True:
            if should_stop():
                update("⏹ 중지 요청 감지: 페이지 그룹 순회 중단", current=len(results), total=0)
                break

            update(f"📄 페이지 그룹 {page_group} 수집 중...", current=len(results), total=0)

            try:
                page_links = wait.until(
                    EC.presence_of_all_elements_located(
                        (By.CSS_SELECTOR, 'nav.pagination a[onclick^="updatePage"]')
                    )
                )
            except TimeoutException:
                break

            page_numbers = [
                a.text.strip() for a in page_links
                if a.text and a.text.strip().isdigit()
            ]

            if page_numbers == prev_page_numbers:
                break
            prev_page_numbers = page_numbers.copy()

            for page_txt in page_numbers:
                if should_stop():
                    update("⏹ 중지 요청 감지: 페이지 순회 중단", current=len(results), total=0)
                    break
                # 1번 페이지는 이미 수집했으므로 제외
                if page_txt == "1":
                    continue

                update(f"📄 페이지 {page_txt} 수집 중 (누적 {len(results)}건)")

                # 클릭 전 첫 제목
                try:
                    prev_first_title = driver.find_element(
                        By.CSS_SELECTOR, "#fileDataList ul li a"
                    ).text.strip()
                except Exception:
                    prev_first_title = None

                # 페이지 클릭
                for _ in range(3):
                    try:
                        links = driver.find_elements(
                            By.CSS_SELECTOR,
                            'nav.pagination a[onclick^="updatePage"]'
                        )
                        target = next(a for a in links if a.text.strip() == page_txt)
                        driver.execute_script("arguments[0].click();", target)
                        break
                    except (StaleElementReferenceException, StopIteration):
                        time.sleep(0.4)

                # 페이지 전환 대기 (이전 DOM 제거)
                try:
                    wait.until(
                        lambda d: (
                            prev_first_title is None or
                            d.find_element(
                                By.CSS_SELECTOR,
                                "#fileDataList ul li a"
                            ).text.strip() != prev_first_title
                        )
                    )
                except TimeoutException:
                    continue

                time.sleep(0.3)

                items = driver.find_elements(
                    By.CSS_SELECTOR, "#fileDataList ul li"
                )

                for li in items:
                    if should_stop():
                        update("⏹ 중지 요청 감지: 항목 수집 중단", current=len(results), total=0)
                        return pd.DataFrame(results)
                    try:
                        title = (
                            li.find_element(By.TAG_NAME, "a")
                            .text.replace("미리보기", "")
                            .strip()
                        )

                        view = li.find_element(
                            By.XPATH,
                            './/span[text()="조회수"]/following-sibling::span'
                        ).text
                        download = li.find_element(
                            By.XPATH,
                            './/span[text()="다운로드"]/following-sibling::span'
                        ).text

                        key = (title, view, download)
                        if key in seen_keys:
                            continue
                        seen_keys.add(key)

                        results.append({
                            "데이터명": title,
                            "조회수": int(view.replace(",", "")),
                            "다운로드수": int(download.replace(",", ""))
                        })
                    except Exception:
                        continue

            if should_stop():
                break

            # 종료 조건 (원본 유지)
            if len(results) == prev_total_count:
                break
            prev_total_count = len(results)

            # 다음 페이지 그룹
            try:
                driver.find_element(By.CSS_SELECTOR, "a.control.next").click()
                page_group += 1
                time.sleep(1.2)
            except Exception:
                break

    finally:
        driver.quit()

    if should_stop():
        update(f"⏹ 중지됨: 현재까지 {len(results)}건 수집", current=len(results), total=0, level="warning")
    else:
        update(f"✅ 수집 완료: 총 {len(results)}건", current=len(results), total=len(results), level="success")
    return pd.DataFrame(results)
