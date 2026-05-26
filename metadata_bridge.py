# -*- coding: utf-8 -*-
"""
metadata_bridge.py

기존 crawler_metadata.py의 상세 수집/파싱/저장 엔진을 그대로 재사용하기 위한 브리지.
- URL Resolver가 확보한 detail_items manifest를 직접 넣어 collect_list_items 단계를 건너뛴다.
- 기존 상세 수집 함수 collect_details_httpx_concurrent(), save_outputs()를 그대로 호출한다.
"""
from __future__ import annotations

import asyncio
import json
import shutil
import time
from pathlib import Path
from typing import Any, Dict, List

import crawler_metadata as cm


def _safe_job_name(value: str) -> str:
    value = str(value or "공공데이터포털_메타데이터").strip()
    for ch in '\\/:*?"<>|':
        value = value.replace(ch, "_")
    return value or "공공데이터포털_메타데이터"


def normalize_items(raw_items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    items = []
    seen = set()
    for idx, item in enumerate(raw_items or [], start=1):
        url = str(item.get("detail_url") or item.get("상세페이지 URL") or "").strip()
        if not url or url in seen:
            continue
        seen.add(url)
        title = str(item.get("title") or item.get("raw_title") or item.get("파일데이터명") or "").strip()
        download = str(item.get("다운로드(바로가기)") or item.get("다운로드수") or item.get("다운로드_바로가기") or "").strip()
        items.append({
            "raw_title": title,
            "title": title,
            "title_source": item.get("title_source", "resolver_manifest"),
            "확장자": item.get("확장자", ""),
            "조회수": str(item.get("조회수", "")).strip(),
            "다운로드(바로가기)": download,
            "다운로드수": download,
            "detail_url": url,
            "source_list_url": item.get("source_list_url", ""),
        })
    return items


async def _run_async(items: List[Dict[str, Any]], source_file_label: str, output_dir: str, concurrency: int):
    return await cm.collect_details_httpx_concurrent(
        items=items,
        source_file_label=source_file_label,
        concurrency=concurrency,
        output_dir=output_dir,
        defer_block=True,
    )


def run_metadata_from_items(
    items: List[Dict[str, Any]],
    job_name: str,
    output_dir: str,
    detail_concurrency: int = 20,
    source_file_label: str = "resolver_manifest",
    make_zip: bool = False,
) -> Dict[str, Any]:
    """Resolver가 만든 상세 URL 목록으로 메타데이터 수집을 실행한다."""
    normalized = normalize_items(items)
    if not normalized:
        raise RuntimeError("metadata 수집 대상 detail_items가 비어 있습니다.")

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    # crawler_metadata.py의 저장 경로/동시성 전역값만 조정한다.
    cm.JOB_NAME = _safe_job_name(job_name)
    cm.OUTPUT_DIR = str(output_path)
    cm.DETAIL_CONCURRENCY = int(detail_concurrency or cm.DETAIL_CONCURRENCY)
    cm.MAKE_ZIP = bool(make_zip)

    start = time.perf_counter()
    print("=" * 80, flush=True)
    print("[metadata_bridge] Resolver manifest 기반 메타데이터 수집", flush=True)
    print(f"- job_name: {cm.JOB_NAME}", flush=True)
    print(f"- output_dir: {output_path}", flush=True)
    print(f"- detail_items: {len(normalized)}", flush=True)
    print(f"- detail_concurrency: {cm.DETAIL_CONCURRENCY}", flush=True)
    print("※ crawler_metadata.py의 상세 수집/파싱/저장 로직을 그대로 호출합니다.", flush=True)
    print("=" * 80, flush=True)

    results = asyncio.run(_run_async(
        items=normalized,
        source_file_label=source_file_label,
        output_dir=str(output_path),
        concurrency=cm.DETAIL_CONCURRENCY,
    ))

    saved = cm.save_outputs(
        output_dir=str(output_path),
        metadata_rows=results["metadata_rows"],
        column_rows=results["column_rows"],
        fail_rows=results["fail_rows"],
    )

    zip_path = ""
    if make_zip:
        zip_path = shutil.make_archive(str(output_path), "zip", str(output_path))
        print(f"[ZIP 생성] {zip_path}", flush=True)

    elapsed = time.perf_counter() - start
    result = {
        "status": "completed",
        "job_name": cm.JOB_NAME,
        "output_dir": str(output_path),
        "metadata_path": saved["paths"].get("metadata", ""),
        "fail_path": saved["paths"].get("fail", ""),
        "zip_path": zip_path,
        "total_items": len(normalized),
        "success_rows": len(results["metadata_rows"]),
        "fail_rows": len(results["fail_rows"]),
        "elapsed_sec": round(elapsed, 2),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
    return result


def run_metadata_from_resolution_file(
    resolution_json: str,
    output_dir: str,
    detail_concurrency: int = 20,
    make_zip: bool = False,
) -> Dict[str, Any]:
    data = json.loads(Path(resolution_json).read_text(encoding="utf-8"))
    org = data.get("selected_provider") or data.get("input_keyword") or "기관"
    items = data.get("detail_items", [])
    return run_metadata_from_items(
        items=items,
        job_name=f"{org}_메타데이터",
        output_dir=output_dir,
        detail_concurrency=detail_concurrency,
        source_file_label="resolver_manifest",
        make_zip=make_zip,
    )


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Resolver manifest 기반 메타데이터 수집")
    parser.add_argument("--resolution-json", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--detail-concurrency", type=int, default=20)
    parser.add_argument("--make-zip", choices=["true", "false"], default="false")
    parser.add_argument("--result-json", default="")
    args = parser.parse_args()

    result = run_metadata_from_resolution_file(
        resolution_json=args.resolution_json,
        output_dir=args.output_dir,
        detail_concurrency=args.detail_concurrency,
        make_zip=args.make_zip.lower() == "true",
    )
    if args.result_json:
        Path(args.result_json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.result_json).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
