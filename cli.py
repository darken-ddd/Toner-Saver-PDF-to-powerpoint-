from __future__ import annotations

import argparse
from pathlib import Path

from processor import ProcessorSettings, make_output_path, process_pdf


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Remove dark PowerPoint backgrounds from PDF files for toner-saving printing.")
    parser.add_argument("pdf", nargs="+", help="Input PDF file(s)")
    parser.add_argument("-o", "--output", default="TonerSaver_Output", help="Output directory")
    parser.add_argument("--dpi", type=int, choices=(200, 300, 400), default=300)
    parser.add_argument("--mode", choices=("balanced", "economy"), default="balanced")
    parser.add_argument("--dark-threshold", type=int, default=125)
    parser.add_argument("--coverage", type=float, default=0.42)
    parser.add_argument("--no-preserve-images", action="store_true")
    parser.add_argument("--force-all", action="store_true")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)
    settings = ProcessorSettings(
        dpi=args.dpi,
        mode=args.mode,
        dark_threshold=args.dark_threshold,
        min_background_coverage=args.coverage,
        preserve_images=not args.no_preserve_images,
        force_all_pages=args.force_all,
    )

    for item in args.pdf:
        source = Path(item)
        destination = make_output_path(source, output_dir)

        def progress(index: int, total: int, message: str) -> None:
            print(f"[{index}/{total}] {message}", end="\r", flush=True)

        result = process_pdf(source, destination, settings, progress)
        print(
            f"\nOK: {source.name} -> {destination} | modified pages: "
            f"{', '.join(map(str, result.modified_pages)) or 'none'}"
        )


if __name__ == "__main__":
    main()
