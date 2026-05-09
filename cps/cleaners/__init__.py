# -*- coding: utf-8 -*-

# Custom upload-time cleaners for Calibre-Web.
#
# This is an extension point added in this fork — runs after the uploaded file
# is saved to a temp path and before metadata extraction. Each cleaner gets a
# chance to mutate the file in place.
#
# Cleaners must be cheap to call on every upload — they should detect their
# own applicability (e.g., OceanofPDF cleaner only acts on files that contain
# the OceanofPDF marker) and no-op otherwise.

from __future__ import annotations

from .. import logger
from . import oceanofpdf

log = logger.create()

# Registered cleaners, in order. Each one is a callable
# (file_path: str, file_extension: str) -> bool returning True if it modified
# the file. Errors are caught and logged so a misbehaving cleaner can never
# break uploads.
_CLEANERS = [
    oceanofpdf.clean,
]


def run_cleaners(file_path: str, file_extension: str) -> None:
    """Run all registered cleaners against the temp file.

    file_extension includes the leading dot, e.g. ".epub".
    """
    ext = (file_extension or "").lower().lstrip(".")
    for cleaner in _CLEANERS:
        try:
            modified = cleaner(file_path, ext)
            if modified:
                log.info("Cleaner %s modified upload %s", cleaner.__module__, file_path)
        except Exception as e:  # noqa: BLE001 - cleaner failures must not break uploads
            log.exception("Cleaner %s failed on %s: %s", cleaner.__module__, file_path, e)
