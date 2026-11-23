"""Catalogue product images: a sidecar file next to the catalogue workbook.

The workbook is the user's own file and is never modified. Images are collected by

    python -m app.services.images.catalogue --ids SB-001 SB-002 [--refresh] [--dry-run]
    python -m app.services.images.catalogue --all [--refresh] [--dry-run]

which reads each product's own page (`product_url`) over plain HTTP and updates
`data/catalogue/product_images.json` (CATALOGUE_IMAGES_PATH). At startup the sidecar is merged
into the catalogue products. A missing, unreadable or partial sidecar never stops startup:
products without an entry show a placeholder. Nothing here is specific to one industry.

Collection rules (deterministic):
  - unknown product IDs stop the command before any request;
  - a whole-catalogue collection needs an explicit `--all` (no accidental bulk fetching);
  - requests are paced per host (the finder's HostPacer), and a transient failure (network
    error, timeout, HTTP 5xx or 429) is retried once after a pause; 4xx pages are not retried;
  - a product with a usable image is skipped unless `--refresh`, and a refresh that fails
    never replaces a previously found image;
  - one page-level image (og:image) shared by several products is a site-wide picture and is
    marked INVALID;
  - records for product IDs that are no longer in the catalogue are dropped and reported;
  - the sidecar is re-read just before writing and replaced atomically (unique temporary file).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

from app.schemas.images import ImageStatus, ProductImage

log = logging.getLogger(__name__)
SIDECAR_VERSION = 1
RETRY_PAUSE_SECONDS = 5.0


def load_catalogue_images(path: Path) -> dict[str, ProductImage]:
    """product_id -> image from the sidecar; {} when the file is missing or unreadable."""
    p = Path(path)
    if not p.is_file():
        return {}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        items = raw.get("images", {}) if isinstance(raw, dict) else {}
    except (OSError, ValueError) as e:
        log.warning("Catalogue image sidecar %s could not be read (%s); products show placeholders", p.name,
                    type(e).__name__)  # fmt: skip
        return {}
    out: dict[str, ProductImage] = {}
    for pid, item in (items.items() if isinstance(items, dict) else ()):
        try:
            out[pid] = ProductImage.model_validate(item)
        except ValidationError:
            log.warning("Ignoring an invalid catalogue image record for %s", pid)
    return out


def apply_catalogue_images(catalogue, images: dict[str, ProductImage]) -> int:
    """Attach images to catalogue products in place. Returns how many products got one."""
    n = 0
    for pid, product in list(catalogue.products.items()):
        if pid in images:
            catalogue.products[pid] = product.model_copy(update={"image": images[pid]})
            n += 1
    return n


def write_catalogue_images(path: Path, images: dict[str, ProductImage]) -> None:
    """Replace the sidecar atomically: a unique temporary file in the same folder, then rename.

    Two writers never share a temporary file, and a reader never sees a partial sidecar."""
    data = {"version": SIDECAR_VERSION, "generated_at": datetime.now(UTC).isoformat(),
            "images": {pid: img.model_dump(mode="json") for pid, img in sorted(images.items())}}  # fmt: skip
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=f".{p.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps(data, indent=1, ensure_ascii=False))
        os.replace(tmp, p)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


class UnknownProductIds(ValueError):
    def __init__(self, ids: list[str]):
        super().__init__(f"unknown product ID(s): {', '.join(ids)}")
        self.ids = ids


def is_retryable(img: ProductImage) -> bool:
    """A transient failure worth one retry: network errors, timeouts, HTTP 5xx and 429."""
    note = img.note or ""
    if img.status is ImageStatus.BLOCKED:
        return "HTTP 429" in note
    if img.status is not ImageStatus.FETCH_FAILED:
        return False
    if note.startswith("Page returned HTTP "):
        code = note.removeprefix("Page returned HTTP ").split()[0]
        return code.isdigit() and int(code) >= 500
    return note.startswith(("Page could not be fetched", "Image lookup exceeded", "Image lookup failed"))


@dataclass
class CollectionReport:
    images: dict[str, ProductImage]
    fetched: list[str] = field(default_factory=list)  # product IDs looked up in this run
    skipped: list[str] = field(default_factory=list)  # already had a usable image
    retried: list[str] = field(default_factory=list)
    kept_previous: list[str] = field(default_factory=list)  # refresh failed; previous image kept
    shared: list[str] = field(default_factory=list)  # marked INVALID: one image shared by several products
    stale_removed: list[str] = field(default_factory=list)  # records of products no longer in the catalogue
    changed: list[str] = field(default_factory=list)  # records that differ from the sidecar read at the start


def _load(settings):
    from app.services.catalogue import load_catalogue

    return load_catalogue(settings.catalogue_path, settings.catalogue_products_sheet,
                          settings.catalogue_competitors_sheet)  # fmt: skip


def plan(catalogue, existing: dict[str, ProductImage], product_ids: list[str] | None, refresh: bool):
    """(products to look up, IDs skipped). Raises UnknownProductIds before any request."""
    if product_ids is not None:
        unknown = sorted({i for i in product_ids if i not in catalogue.products})
        if unknown:
            raise UnknownProductIds(unknown)
    ids = list(dict.fromkeys(product_ids)) if product_ids is not None else sorted(catalogue.products)
    todo = [catalogue.products[i] for i in ids if refresh or not (i in existing and existing[i].usable)]
    skipped = [i for i in ids if i in existing and existing[i].usable and not refresh]
    return todo, skipped


async def collect(settings, product_ids: list[str] | None, refresh: bool, finder=None,
                  retry_pause: float = RETRY_PAUSE_SECONDS) -> CollectionReport:  # fmt: skip
    """Look up images for catalogue products (`product_ids`, or all when None) and merge them
    onto the existing sidecar records. Nothing is written here (see `main`)."""
    from app.services.images import ImageCache, ImageFinder, flag_shared_images

    catalogue = _load(settings)
    existing = load_catalogue_images(settings.catalogue_images_path)
    todo, skipped = plan(catalogue, existing, product_ids, refresh)
    report = CollectionReport(images={}, skipped=skipped, fetched=[p.product_id for p in todo])
    own = finder is None
    finder = finder or ImageFinder(settings, cache=ImageCache(settings.image_cache_dir, settings.image_cache_max_bytes))
    try:
        found = list(await asyncio.gather(*(finder.find(str(p.product_url)) for p in todo)))
        retry = [i for i, img in enumerate(found) if is_retryable(img)]
        if retry:
            await asyncio.sleep(retry_pause)
            again = await asyncio.gather(*(finder.find(str(todo[i].product_url)) for i in retry))
            for i, img in zip(retry, again):
                found[i] = img
            report.retried = [todo[i].product_id for i in retry]
    finally:
        if own:
            await finder.aclose()
    result = dict(existing)
    for p, img in zip(todo, found):
        old = existing.get(p.product_id)
        if old is not None and old.usable and not img.usable:
            report.kept_previous.append(p.product_id)  # a failed refresh never loses a found image
            continue
        result[p.product_id] = img
    report.stale_removed = sorted(pid for pid in result if pid not in catalogue.products)
    for pid in report.stale_removed:
        del result[pid]
    report.shared = sorted(flag_shared_images(result, "catalogue products"))  # type: ignore[arg-type]
    report.images = result
    report.changed = sorted(pid for pid, img in result.items() if existing.get(pid) != img)
    return report


def save(settings, report: CollectionReport) -> None:
    """Write the report's records, merged onto the sidecar as it is on disk right now (so a
    concurrent collection for other products is not lost), dropping stale product IDs."""
    from app.services.images import flag_shared_images

    current = load_catalogue_images(settings.catalogue_images_path)
    merged = {**current, **{pid: report.images[pid] for pid in report.changed}}
    for pid in report.stale_removed:
        merged.pop(pid, None)
    flag_shared_images(merged, "catalogue products")  # type: ignore[arg-type]
    write_catalogue_images(settings.catalogue_images_path, merged)


def main(argv: list[str] | None = None) -> int:
    from app.core.config import Settings

    ap = argparse.ArgumentParser(description="Collect catalogue product images into the sidecar file.")
    who = ap.add_mutually_exclusive_group(required=True)
    who.add_argument("--ids", nargs="+", help="product IDs to collect")
    who.add_argument("--all", action="store_true", help="every catalogue product (paced; many requests)")
    ap.add_argument("--refresh", action="store_true", help="re-check products that already have an image")
    ap.add_argument("--dry-run", action="store_true", help="list what would be fetched; make no requests")
    args = ap.parse_args(argv)
    settings = Settings()
    ids = None if args.all else args.ids
    try:
        if args.dry_run:
            todo, skipped = plan(_load(settings), load_catalogue_images(settings.catalogue_images_path), ids,
                                 args.refresh)  # fmt: skip
            print(f"Would look up {len(todo)} product page(s); {len(skipped)} already have an image.")
            for p in todo:
                print(f"  {p.product_id}: {p.product_url}")
            return 0
        report = asyncio.run(collect(settings, ids, args.refresh))
    except UnknownProductIds as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    save(settings, report)
    images = report.images
    found = sum(1 for i in images.values() if i.usable)
    print(f"Looked up {len(report.fetched)}, skipped {len(report.skipped)} (already found), retried "
          f"{len(report.retried)}.")  # fmt: skip
    print(f"{found}/{len(images)} catalogue records have a usable image -> {settings.catalogue_images_path}")
    for label, ids_ in (("kept previous image after a failed refresh", report.kept_previous),
                        ("shared site-wide image (marked INVALID)", report.shared),
                        ("removed: no longer in the catalogue", report.stale_removed)):  # fmt: skip
        if ids_:
            print(f"  {label}: {', '.join(ids_)}")
    for pid, img in sorted(images.items()):
        if not img.usable:
            print(f"  {pid}: {img.status.value} ({img.note})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
