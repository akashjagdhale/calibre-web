# -*- coding: utf-8 -*-

# OceanofPDF cleaner.
#
# Strips OceanofPDF branding from EPUBs uploaded through Calibre-Web. Only acts
# on files that contain the OceanofPDF marker — clean EPUBs are passed through
# untouched.
#
# What it removes:
#   * Text mentions of "oceanofpdf.com", "Ocean of PDF", "OceanofPDF" (any case)
#     from every (x)html, opf, ncx, txt, css file in the EPUB
#   * `<a>` tags whose href points to an oceanofpdf.com URL — the whole tag is
#     removed but its text content (if any) is kept
#   * The OceanofPDF promotional page itself, when it's a separate file
#     (filename or content gives it away). Manifest / spine entries pointing at
#     it are also pruned.
#   * "(OceanofPDF.com)" / "OceanofPDF.com" suffixes from `<dc:title>` and
#     `<dc:description>` metadata in the OPF
#
# What it does NOT do:
#   * Touch cover images. Watermarks burned into JPGs are left alone — that
#     would need image processing and risks corrupting the cover.
#   * Reflow / re-typeset the book content.

from __future__ import annotations

import os
import re
import shutil
import tempfile
import zipfile
from typing import Iterable

# All OceanofPDF wordings we want gone, ordered longest-first so that the
# domain doesn't get half-replaced before the spelled-out form is matched.
_TEXT_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\(\s*OceanofPDF\.com\s*\)", re.IGNORECASE),
    re.compile(r"\[\s*OceanofPDF\.com\s*\]", re.IGNORECASE),
    re.compile(r"OceanofPDF\.com", re.IGNORECASE),
    re.compile(r"Ocean\s+of\s+PDF\.com", re.IGNORECASE),
    re.compile(r"OceanofPDF", re.IGNORECASE),
    re.compile(r"Ocean\s+of\s+PDF", re.IGNORECASE),
]

# Anchor tags that link to oceanofpdf — we strip the tag, keep inner text.
_ANCHOR_PATTERN = re.compile(
    r"<a\b[^>]*?href\s*=\s*['\"][^'\"]*oceanofpdf[^'\"]*['\"][^>]*>(.*?)</a>",
    re.IGNORECASE | re.DOTALL,
)

# Empty-after-cleanup tags we leave behind. Conservative — we only kill blocks
# that are *entirely* whitespace/empty so we don't accidentally collapse real
# layout. Repeated until stable so chains of empties (e.g., `<p><span></span></p>`)
# eventually fully unwind.
_EMPTY_TAG_PATTERN = re.compile(
    r"<(p|span|div|h[1-6])\b[^>]*>\s*</\1>",
    re.IGNORECASE,
)

# Files inside the EPUB we should rewrite text in.
_TEXT_EXTS = {".html", ".xhtml", ".htm", ".opf", ".ncx", ".txt", ".css"}

# A page is treated as a dedicated OceanofPDF promo page if its filename
# obviously says so OR if more than this fraction of its visible text is
# OceanofPDF wording.
_PROMO_FILENAME_HINT = re.compile(r"oceanofpdf|ocean[-_ ]of[-_ ]pdf", re.IGNORECASE)
_PROMO_CONTENT_THRESHOLD = 0.4

_MARKER = re.compile(r"oceanofpdf|ocean\s+of\s+pdf", re.IGNORECASE)


def clean(file_path: str, file_extension: str) -> bool:
    """Clean OceanofPDF branding from an EPUB. Returns True if file was modified."""
    if file_extension != "epub":
        return False
    if not _is_oceanofpdf_epub(file_path):
        return False

    tmp_fd, tmp_out = tempfile.mkstemp(suffix=".epub", dir=os.path.dirname(file_path))
    os.close(tmp_fd)
    modified = False
    try:
        promo_files = _find_promo_files(file_path)
        with zipfile.ZipFile(file_path, "r") as zin, zipfile.ZipFile(
            tmp_out, "w", zipfile.ZIP_DEFLATED
        ) as zout:
            for item in zin.infolist():
                # Drop dedicated OceanofPDF promo pages entirely.
                if item.filename in promo_files:
                    modified = True
                    continue

                data = zin.read(item.filename)
                ext = os.path.splitext(item.filename)[1].lower()

                if ext in _TEXT_EXTS:
                    new_data, file_changed = _clean_text_file(
                        data, item.filename, promo_files
                    )
                    if file_changed:
                        modified = True
                        data = new_data

                # Preserve original metadata (timestamps, compression, etc).
                zout.writestr(item, data)

        if modified:
            shutil.move(tmp_out, file_path)
        else:
            os.unlink(tmp_out)
    except Exception:
        if os.path.exists(tmp_out):
            try:
                os.unlink(tmp_out)
            except OSError:
                pass
        raise

    return modified


def _is_oceanofpdf_epub(file_path: str) -> bool:
    """Quick scan: does this EPUB contain the OceanofPDF marker anywhere?"""
    try:
        with zipfile.ZipFile(file_path, "r") as zf:
            for name in zf.namelist():
                if os.path.splitext(name)[1].lower() not in _TEXT_EXTS:
                    continue
                try:
                    chunk = zf.read(name)
                except KeyError:
                    continue
                if _MARKER.search(chunk.decode("utf-8", errors="ignore")):
                    return True
    except zipfile.BadZipFile:
        return False
    return False


def _find_promo_files(file_path: str) -> set[str]:
    """Identify HTML files inside the EPUB that are dedicated OceanofPDF promo pages."""
    promo: set[str] = set()
    with zipfile.ZipFile(file_path, "r") as zf:
        for name in zf.namelist():
            ext = os.path.splitext(name)[1].lower()
            if ext not in {".html", ".xhtml", ".htm"}:
                continue
            if _PROMO_FILENAME_HINT.search(os.path.basename(name)):
                promo.add(name)
                continue
            try:
                text = zf.read(name).decode("utf-8", errors="ignore")
            except KeyError:
                continue
            visible = re.sub(r"<[^>]+>", " ", text)
            visible = re.sub(r"\s+", " ", visible).strip()
            if not visible:
                continue
            promo_chars = sum(len(m.group(0)) for m in _MARKER.finditer(visible))
            if promo_chars / max(len(visible), 1) >= _PROMO_CONTENT_THRESHOLD:
                promo.add(name)
    return promo


def _clean_text_file(
    data: bytes, name: str, promo_files: Iterable[str]
) -> tuple[bytes, bool]:
    """Rewrite a single text file inside the EPUB. Returns (data, modified)."""
    try:
        text = data.decode("utf-8")
        encoding = "utf-8"
    except UnicodeDecodeError:
        # Fall back: many older EPUBs are utf-8 anyway, but if not, leave alone.
        return data, False

    original = text
    ext = os.path.splitext(name)[1].lower()

    # OPF-specific: drop manifest / spine entries for promo pages BEFORE text
    # substitutions run, so href values still match the original filenames.
    if ext == ".opf" and promo_files:
        promo_basenames = {os.path.basename(p) for p in promo_files}
        for promo in promo_basenames:
            # Match a self-closing or opening <item ... href="...promo..." ...>
            # Note: media-type attributes contain "/" so we explicitly match up
            # to the first ">" rather than excluding "/".
            href_re = re.compile(
                r"<item\b[^>]*?href\s*=\s*['\"][^'\"]*"
                + re.escape(promo)
                + r"['\"][^>]*?/?>",
                re.IGNORECASE,
            )
            id_match = re.findall(
                r"<item\b[^>]*?id\s*=\s*['\"]([^'\"]+)['\"][^>]*?href\s*=\s*['\"][^'\"]*"
                + re.escape(promo)
                + r"['\"][^>]*?/?>",
                text,
                re.IGNORECASE,
            )
            text = href_re.sub("", text)
            for item_id in id_match:
                spine_re = re.compile(
                    r"<itemref\b[^>]*?idref\s*=\s*['\"]"
                    + re.escape(item_id)
                    + r"['\"][^>]*?/?>",
                    re.IGNORECASE,
                )
                text = spine_re.sub("", text)

    # Strip oceanofpdf anchors (keep inner text).
    text = _ANCHOR_PATTERN.sub(lambda m: m.group(1), text)

    # Replace text mentions.
    for pattern in _TEXT_PATTERNS:
        text = pattern.sub("", text)

    # HTML-only: collapse empties left by the substitutions, until stable.
    if ext in {".html", ".xhtml", ".htm"}:
        prev = None
        while prev != text:
            prev = text
            text = _EMPTY_TAG_PATTERN.sub("", text)

    if text == original:
        return data, False
    return text.encode(encoding), True
