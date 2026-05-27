# -*- coding: utf-8 -*-
from __future__ import annotations

import re
import time
from io import BytesIO
from pathlib import Path
from typing import Dict, Optional

import pandas as pd
from crawler import collect_file_data_from_url


def safe_name(value: str) -> str:
    return re.sub(r"[\\/:*?\"<>|]", "_", str(value or "기관")).strip("_") or "기관"


def run_stats_crawler(provider_filedata_url: str, provider_name: str = "기관", status_callback=None) -> Dict:
    if not provider_filedata_url:
        raise ValueError("provider_filedata_url이 비어 있습니다.")
    df = collect_file_data_from_url(provider_filedata_url, status_callback=status_callback)
    out_dir = Path("outputs") / "stats"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"공공데이터_{safe_name(provider_name)}_조회수_다운로드수_{time.strftime('%Y%m%d_%H%M%S')}.xlsx"
    with pd.ExcelWriter(path, engine="xlsxwriter", engine_kwargs={"options": {"strings_to_urls": False}}) as writer:
        df.to_excel(writer, index=False, sheet_name="FILE_집계")
    return {"dataframe": df, "path": str(path), "rows": len(df)}
