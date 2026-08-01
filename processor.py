from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Callable, Iterable

import fitz  # PyMuPDF
import numpy as np
from PIL import Image, ImageFilter

ProgressCallback = Callable[[int, int, str], None]


@dataclass(slots=True)
class ProcessorSettings:
    dpi: int = 300
    mode: str = "balanced"  # balanced | economy
    dark_threshold: int = 125
    min_background_coverage: float = 0.42
    preserve_images: bool = True
    force_all_pages: bool = False
    jpeg_quality: int = 93
    economy_threshold: int = 216
    foreground_gamma: float = 0.72


@dataclass(slots=True)
class PageAnalysis:
    is_dark: bool
    background_rgb: tuple[int, int, int]
    background_luma: float
    border_coverage: float
    page_coverage: float
    dark_pixel_fraction: float


@dataclass(slots=True)
class ProcessResult:
    input_path: Path
    output_path: Path
    total_pages: int
    modified_pages: list[int]
    skipped_pages: list[int]


class TonerSaverError(RuntimeError):
    pass


def _luminance(rgb: np.ndarray) -> np.ndarray:
    """Return ITU-R BT.601 luminance for an RGB array."""
    return (
        rgb[..., 0].astype(np.float32) * 0.299
        + rgb[..., 1].astype(np.float32) * 0.587
        + rgb[..., 2].astype(np.float32) * 0.114
    )


def _sample_border(rgb: np.ndarray) -> np.ndarray:
    h, w, _ = rgb.shape
    inset = max(1, int(min(h, w) * 0.008))
    width = max(5, int(min(h, w) * 0.035))
    y0, y1 = inset, min(h, inset + width)
    y2, y3 = max(0, h - inset - width), max(1, h - inset)
    x0, x1 = inset, min(w, inset + width)
    x2, x3 = max(0, w - inset - width), max(1, w - inset)

    pieces = [
        rgb[y0:y1, :, :].reshape(-1, 3),
        rgb[y2:y3, :, :].reshape(-1, 3),
        rgb[:, x0:x1, :].reshape(-1, 3),
        rgb[:, x2:x3, :].reshape(-1, 3),
    ]
    return np.concatenate(pieces, axis=0)


def _dominant_color(samples: np.ndarray) -> tuple[np.ndarray, float]:
    """Estimate dominant color using coarse RGB bins, robust to text/antialiasing."""
    if samples.size == 0:
        return np.array([255, 255, 255], dtype=np.float32), 0.0

    # Keep the sample bounded for speed on 400-DPI pages.
    if len(samples) > 250_000:
        step = max(1, len(samples) // 250_000)
        samples = samples[::step]

    bins = (samples.astype(np.uint16) // 16).astype(np.uint16)
    keys = bins[:, 0] * 256 + bins[:, 1] * 16 + bins[:, 2]
    unique, counts = np.unique(keys, return_counts=True)
    best_key = unique[int(np.argmax(counts))]
    selected = samples[keys == best_key]
    color = np.median(selected, axis=0).astype(np.float32)
    coverage = float(len(selected) / max(1, len(samples)))
    return color, coverage


def analyze_rgb(rgb: np.ndarray, settings: ProcessorSettings) -> PageAnalysis:
    border = _sample_border(rgb)
    bg, coarse_border_coverage = _dominant_color(border)

    # A slide may have a thin white export margin. In that case, use the
    # dominant whole-page color when it is clearly dark and sufficiently common.
    h, w, _ = rgb.shape
    stride = max(1, int(max(h, w) / 900))
    sampled_u8 = rgb[::stride, ::stride, :]
    global_bg, global_coarse_coverage = _dominant_color(sampled_u8.reshape(-1, 3))
    border_bg_luma = float(bg[0] * 0.299 + bg[1] * 0.587 + bg[2] * 0.114)
    global_bg_luma = float(
        global_bg[0] * 0.299 + global_bg[1] * 0.587 + global_bg[2] * 0.114
    )
    if (
        border_bg_luma >= settings.dark_threshold
        and global_bg_luma < settings.dark_threshold
        and global_coarse_coverage >= 0.24
    ):
        bg = global_bg
        coarse_border_coverage = 0.0

    # Euclidean RGB tolerance. This catches gradients and anti-aliased edges
    # without confusing normal foreground objects with the background.
    tolerance = 48.0
    border_distance = np.linalg.norm(border.astype(np.float32) - bg, axis=1)
    border_coverage = max(coarse_border_coverage, float(np.mean(border_distance <= tolerance)))

    # Whole-page statistics use the already downsampled page.
    sampled = sampled_u8.astype(np.float32)
    page_distance = np.linalg.norm(sampled - bg, axis=2)
    page_coverage = float(np.mean(page_distance <= tolerance))

    luma = _luminance(sampled)
    bg_luma = float(bg[0] * 0.299 + bg[1] * 0.587 + bg[2] * 0.114)
    dark_fraction = float(np.mean(luma < settings.dark_threshold))

    enough_background = (
        border_coverage >= settings.min_background_coverage
        or page_coverage >= max(0.28, settings.min_background_coverage - 0.12)
        or dark_fraction >= 0.58
    )
    is_dark = bg_luma < settings.dark_threshold and enough_background

    return PageAnalysis(
        is_dark=is_dark,
        background_rgb=tuple(int(round(v)) for v in bg),
        background_luma=bg_luma,
        border_coverage=border_coverage,
        page_coverage=page_coverage,
        dark_pixel_fraction=dark_fraction,
    )


def _render_page_rgb(page: fitz.Page, dpi: int) -> tuple[np.ndarray, fitz.Matrix]:
    scale = dpi / 72.0
    matrix = fitz.Matrix(scale, scale)
    pix = page.get_pixmap(matrix=matrix, colorspace=fitz.csRGB, alpha=False)
    arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
    if pix.n > 3:
        arr = arr[..., :3]
    return arr.copy(), matrix


def _get_image_mask(page: fitz.Page, shape: tuple[int, int], dpi: int) -> np.ndarray:
    """Build a mask for embedded photos so they can remain normal grayscale.

    Full-page images are intentionally ignored: they are often flattened slides or
    backgrounds and must be toner-processed as a whole.
    """
    height, width = shape
    mask = np.zeros((height, width), dtype=np.uint8)
    page_area = max(1.0, page.rect.width * page.rect.height)
    scale = dpi / 72.0

    seen: set[tuple[int, int, int, int]] = set()
    for image in page.get_images(full=True):
        xref = image[0]
        try:
            rects = page.get_image_rects(xref)
        except Exception:
            continue

        for rect in rects:
            if rect.is_empty or rect.is_infinite:
                continue
            area_fraction = (rect.width * rect.height) / page_area
            if area_fraction >= 0.48:
                # Likely a full-slide screenshot/background.
                continue

            x0 = max(0, int(np.floor(rect.x0 * scale)) - 3)
            y0 = max(0, int(np.floor(rect.y0 * scale)) - 3)
            x1 = min(width, int(np.ceil(rect.x1 * scale)) + 3)
            y1 = min(height, int(np.ceil(rect.y1 * scale)) + 3)
            key = (x0, y0, x1, y1)
            if key in seen or x1 <= x0 or y1 <= y0:
                continue
            seen.add(key)
            mask[y0:y1, x0:x1] = 255

    if mask.any():
        # Feathering prevents hard rectangular seams around anti-aliased images.
        pil_mask = Image.fromarray(mask, mode="L").filter(ImageFilter.GaussianBlur(radius=2.0))
        mask = np.asarray(pil_mask, dtype=np.uint8)
    return mask


def transform_dark_page(
    rgb: np.ndarray,
    analysis: PageAnalysis,
    settings: ProcessorSettings,
    image_mask: np.ndarray | None = None,
) -> np.ndarray:
    """Convert a dark slide to a white-background grayscale print version."""
    gray = _luminance(rgb)
    bg_luma = analysis.background_luma

    # Measure foreground contrast relative to the detected background. PowerPoint
    # dark slides normally use lighter text/lines; this maps background -> white
    # and increasing contrast -> darker ink.
    positive = np.clip((gray - bg_luma) / max(1.0, 255.0 - bg_luma), 0.0, 1.0)

    # Also retain objects darker than the background, but with lower sensitivity.
    negative = np.clip((bg_luma - gray) / max(24.0, bg_luma), 0.0, 1.0) * 0.72
    foreground = np.maximum(positive, negative)
    foreground = np.power(foreground, settings.foreground_gamma)
    converted = 255.0 * (1.0 - foreground)

    # Pixels very close to the actual RGB background should be pure white. This is
    # important for navy/colored backgrounds that have similar luminance to shapes.
    bg = np.asarray(analysis.background_rgb, dtype=np.float32)
    color_distance = np.linalg.norm(rgb.astype(np.float32) - bg, axis=2)
    converted[color_distance < 34.0] = 255.0

    if settings.mode == "economy":
        converted = np.where(converted < settings.economy_threshold, 0.0, 255.0)

    if settings.preserve_images and image_mask is not None and image_mask.any():
        alpha = image_mask.astype(np.float32) / 255.0
        # Preserve photographs as normal grayscale. A tiny lift saves toner while
        # keeping photo detail and avoiding a visible border.
        photo_gray = np.clip(gray * 0.94 + 15.0, 0.0, 255.0)
        converted = converted * (1.0 - alpha) + photo_gray * alpha

    return np.clip(converted, 0, 255).astype(np.uint8)


def _image_to_jpeg_bytes(gray: np.ndarray, quality: int) -> bytes:
    image = Image.fromarray(gray, mode="L")
    buffer = BytesIO()
    image.save(
        buffer,
        format="JPEG",
        quality=max(70, min(100, quality)),
        optimize=True,
        progressive=False,
        dpi=(300, 300),
    )
    return buffer.getvalue()


def preview_page(
    pdf_path: str | Path,
    page_number: int,
    settings: ProcessorSettings,
    max_dimension: int = 1100,
) -> tuple[Image.Image, Image.Image, PageAnalysis]:
    path = Path(pdf_path)
    with fitz.open(path) as doc:
        if page_number < 0 or page_number >= doc.page_count:
            raise TonerSaverError("شماره صفحه خارج از محدوده است.")
        page = doc[page_number]
        preview_dpi = min(settings.dpi, 160)
        rgb, _ = _render_page_rgb(page, preview_dpi)
        analysis = analyze_rgb(rgb, settings)
        mask = _get_image_mask(page, rgb.shape[:2], preview_dpi) if settings.preserve_images else None
        if analysis.is_dark or settings.force_all_pages:
            changed = transform_dark_page(rgb, analysis, settings, mask)
        else:
            changed = _luminance(rgb).astype(np.uint8)

    before = Image.fromarray(rgb, mode="RGB")
    after = Image.fromarray(changed, mode="L").convert("RGB")
    before.thumbnail((max_dimension, max_dimension), Image.Resampling.LANCZOS)
    after.thumbnail((max_dimension, max_dimension), Image.Resampling.LANCZOS)
    return before, after, analysis


def find_first_dark_page(
    pdf_path: str | Path,
    settings: ProcessorSettings,
    max_pages: int = 30,
) -> int:
    path = Path(pdf_path)
    with fitz.open(path) as doc:
        for index in range(min(doc.page_count, max_pages)):
            page = doc[index]
            rgb, _ = _render_page_rgb(page, min(settings.dpi, 110))
            if analyze_rgb(rgb, settings).is_dark:
                return index
    return 0


def process_pdf(
    input_path: str | Path,
    output_path: str | Path,
    settings: ProcessorSettings | None = None,
    progress: ProgressCallback | None = None,
) -> ProcessResult:
    settings = settings or ProcessorSettings()
    source_path = Path(input_path)
    destination_path = Path(output_path)

    if not source_path.exists():
        raise TonerSaverError(f"فایل پیدا نشد: {source_path}")
    if source_path.suffix.lower() != ".pdf":
        raise TonerSaverError("فقط فایل PDF پشتیبانی می‌شود.")
    if source_path.resolve() == destination_path.resolve():
        raise TonerSaverError("مسیر خروجی نباید با فایل ورودی یکسان باشد.")

    destination_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = destination_path.with_suffix(destination_path.suffix + ".tmp")

    modified: list[int] = []
    skipped: list[int] = []

    try:
        with fitz.open(source_path) as src:
            if src.needs_pass:
                raise TonerSaverError("PDF رمزگذاری شده است. ابتدا رمز آن را حذف کنید.")
            out = fitz.open()
            try:
                total = src.page_count
                for page_index in range(total):
                    page = src[page_index]
                    if progress:
                        progress(page_index, total, f"بررسی صفحه {page_index + 1} از {total}")

                    rgb, _ = _render_page_rgb(page, settings.dpi)
                    analysis = analyze_rgb(rgb, settings)
                    should_modify = analysis.is_dark or settings.force_all_pages

                    if should_modify:
                        image_mask = (
                            _get_image_mask(page, rgb.shape[:2], settings.dpi)
                            if settings.preserve_images
                            else None
                        )
                        gray = transform_dark_page(rgb, analysis, settings, image_mask)
                        image_bytes = _image_to_jpeg_bytes(gray, settings.jpeg_quality)
                        new_page = out.new_page(width=page.rect.width, height=page.rect.height)
                        new_page.insert_image(new_page.rect, stream=image_bytes, keep_proportion=False)
                        modified.append(page_index + 1)
                    else:
                        new_page = out.new_page(width=page.rect.width, height=page.rect.height)
                        new_page.show_pdf_page(new_page.rect, src, page_index, keep_proportion=False)
                        skipped.append(page_index + 1)

                metadata = {key: (value or "") for key, value in dict(src.metadata or {}).items()}
                metadata["producer"] = "Toner Saver PDF - PyMuPDF"
                metadata["creator"] = metadata.get("creator") or "Toner Saver PDF"
                out.set_metadata(metadata)

                out.save(
                    temporary_path,
                    garbage=4,
                    deflate=True,
                    clean=True,
                )
            finally:
                out.close()

        temporary_path.replace(destination_path)
        if progress:
            progress(len(modified) + len(skipped), len(modified) + len(skipped), "تمام شد")

        return ProcessResult(
            input_path=source_path,
            output_path=destination_path,
            total_pages=len(modified) + len(skipped),
            modified_pages=modified,
            skipped_pages=skipped,
        )
    except Exception:
        if temporary_path.exists():
            temporary_path.unlink(missing_ok=True)
        raise


def make_output_path(input_path: str | Path, output_directory: str | Path) -> Path:
    source = Path(input_path)
    directory = Path(output_directory)
    return directory / f"{source.stem}_TonerSaver.pdf"
