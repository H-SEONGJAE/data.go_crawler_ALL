# -*- coding: utf-8 -*-
from __future__ import annotations

import time
from pathlib import Path
from typing import Dict

import crawler_metadata


def run_metadata_crawler(provider_filedata_url: str, provider_name: str = "기관") -> Dict:
    """기존 crawler_metadata.py 엔진에 기관 파일데이터 목록 URL만 주입해 실행한다."""
    if not provider_filedata_url:
        raise ValueError("provider_filedata_url이 비어 있습니다.")

    crawler_metadata.TARGET_URL = provider_filedata_url
    crawler_metadata.JOB_NAME = f"공공데이터포털_{provider_name}_메타데이터"
    crawler_metadata.RUN_MODE = "MAIN"
    # 기존 전체 수집과 같은 내부 목록/상세 수집 흐름을 사용한다.
    result = crawler_metadata.run_crawler()
    return result
