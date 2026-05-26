# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import json
from pathlib import Path

from metadata_bridge import run_metadata_from_resolution_file


def main():
    parser = argparse.ArgumentParser(description="Resolver manifest 기반 메타데이터 수집 runner")
    parser.add_argument("--resolution-json", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--result-json", required=True)
    parser.add_argument("--detail-concurrency", type=int, default=20)
    parser.add_argument("--make-zip", choices=["true", "false"], default="false")
    args = parser.parse_args()

    result = run_metadata_from_resolution_file(
        resolution_json=args.resolution_json,
        output_dir=args.output_dir,
        detail_concurrency=args.detail_concurrency,
        make_zip=args.make_zip.lower() == "true",
    )
    Path(args.result_json).parent.mkdir(parents=True, exist_ok=True)
    Path(args.result_json).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
