"""Image tool — read metadata, convert, resize, analyze images.

Uses Pillow (PIL) for image operations.
Can read EXIF data, convert between formats, resize, get info.
"""

from __future__ import annotations

import base64
import io
import json
import os
from pathlib import Path
from typing import Any

SCHEMA = {
    "name": "image",
    "description": "Work with images: read metadata/EXIF, convert format, "
                   "resize, get dimensions, color info. Supports JPG, PNG, WebP, GIF.",
    "parameters": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Path to the image file",
            },
            "action": {
                "type": "string",
                "enum": ["info", "metadata", "convert", "resize"],
                "description": "What to do with the image",
                "default": "info",
            },
            "format": {
                "type": "string",
                "enum": ["JPEG", "PNG", "WebP", "GIF", "BMP"],
                "description": "Target format for convert action",
                "default": "PNG",
            },
            "width": {
                "type": "integer",
                "description": "Target width for resize action",
                "default": 0,
            },
            "height": {
                "type": "integer",
                "description": "Target height for resize action",
                "default": 0,
            },
            "output": {
                "type": "string",
                "description": "Output path for convert/resize (optional)",
                "default": "",
            },
            "quality": {
                "type": "integer",
                "description": "JPEG/WebP quality 1-100 (default: 85)",
                "default": 85,
            },
        },
        "required": ["path"],
    },
}


def execute(params: dict) -> str:
    """Execute an image operation.

    Args:
        params: See SCHEMA.

    Returns:
        Formatted result.
    """
    try:
        from PIL import Image, ExifTags
    except ImportError:
        return "❌ Pillow not installed. Run: pip install Pillow"

    path = Path(params["path"]).expanduser()
    if not path.exists():
        return f"❌ File not found: {path}"

    action = params.get("action", "info")

    try:
        img = Image.open(path)

        if action == "info":
            return _get_info(img, path)
        elif action == "metadata":
            return _get_metadata(img, path)
        elif action == "convert":
            return _convert(img, path, params)
        elif action == "resize":
            return _resize(img, path, params)
        else:
            return f"Unknown action: {action}"

    except Exception as e:
        return f"❌ Image error: {e}"


def _get_info(img, path: Path) -> str:
    """Get basic image info."""
    lines = [
        f"🖼 Image: {path.name}",
        f"   Size: {path.stat().st_size:,} bytes",
        f"   Dimensions: {img.width} × {img.height} px",
        f"   Format: {img.format or 'unknown'}",
        f"   Mode: {img.mode}",
    ]
    if img.format == "GIF":
        try:
            frames = getattr(img, "n_frames", 1)
            lines.append(f"   Frames: {frames}")
        except Exception:
            pass
    if hasattr(img, "info") and img.info:
        info_keys = list(img.info.keys())
        lines.append(f"   Info keys: {', '.join(info_keys[:10])}")
    return "\n".join(lines)


def _get_metadata(img, path: Path) -> str:
    """Get EXIF metadata."""
    lines = [f"🖼 Metadata: {path.name}\n"]

    if not hasattr(img, "_getexif") or img._getexif() is None:
        lines.append("   No EXIF data found.")
    else:
        from PIL import ExifTags
        exif = img._getexif()
        count = 0
        for tag_id, value in sorted(exif.items()):
            tag = ExifTags.TAGS.get(tag_id, str(tag_id))
            if isinstance(value, bytes):
                try:
                    value = value.decode("utf-8", errors="replace")[:100]
                except Exception:
                    value = f"[binary {len(value)} bytes]"
            lines.append(f"   {tag}: {value}")
            count += 1
            if count >= 30:
                lines.append(f"   ... and {len(exif) - 30} more tags")
                break

    # File info
    stat = path.stat()
    lines.append(f"\n   File: {path.name}")
    lines.append(f"   Size: {stat.st_size:,} bytes")
    lines.append(f"   Modified: {os.path.getmtime(path):.0f}")

    return "\n".join(lines)


def _convert(img, path: Path, params: dict) -> str:
    """Convert image to another format."""
    fmt = params.get("format", "PNG").upper()
    output = params.get("output", "")
    quality = min(params.get("quality", 85), 100)

    if not output:
        stem = path.stem
        output = str(path.parent / f"{stem}.{fmt.lower()}")

    out_path = Path(output).expanduser()

    # Convert
    kwargs = {}
    if fmt in ("JPEG", "WebP"):
        kwargs["quality"] = quality

    # Handle RGBA → RGB for JPEG
    if fmt == "JPEG" and img.mode in ("RGBA", "P"):
        img = img.convert("RGB")

    img.save(str(out_path), format=fmt, **kwargs)

    orig_size = path.stat().st_size
    new_size = out_path.stat().st_size
    ratio = (1 - new_size / orig_size) * 100 if orig_size else 0
    direction = "smaller" if ratio > 0 else "larger"

    return (
        f"✅ Converted: {path.name} → {out_path.name}\n"
        f"   Format: {fmt} ({img.mode})\n"
        f"   Size: {orig_size:,} → {new_size:,} bytes ({abs(ratio):.1f}% {direction})"
    )


def _resize(img, path: Path, params: dict) -> str:
    """Resize image to given dimensions."""
    width = params.get("width", 0)
    height = params.get("height", 0)
    output = params.get("output", "")
    quality = min(params.get("quality", 85), 100)

    if not width and not height:
        return "❌ Specify at least width or height"

    orig_w, orig_h = img.size

    # Calculate new dimensions (maintain aspect ratio if one dimension is 0)
    if width and not height:
        ratio = width / orig_w
        new_w = width
        new_h = int(orig_h * ratio)
    elif height and not width:
        ratio = height / orig_h
        new_h = height
        new_w = int(orig_w * ratio)
    else:
        new_w = width
        new_h = height

    resized = img.resize((new_w, new_h), Image.LANCZOS)

    if not output:
        stem = path.stem
        ext = path.suffix
        output = str(path.parent / f"{stem}_{new_w}x{new_h}{ext}")

    out_path = Path(output).expanduser()

    save_kwargs = {}
    ext_lower = out_path.suffix.lower()
    if ext_lower in (".jpg", ".jpeg"):
        save_kwargs["quality"] = quality
    elif ext_lower == ".webp":
        save_kwargs["quality"] = quality

    # Handle mode for JPEG
    if ext_lower in (".jpg", ".jpeg") and resized.mode in ("RGBA", "P"):
        resized = resized.convert("RGB")

    resized.save(str(out_path), **save_kwargs)

    orig_size = path.stat().st_size
    new_size = out_path.stat().st_size

    return (
        f"✅ Resized: {orig_w}×{orig_h} → {new_w}×{new_h}\n"
        f"   Saved: {out_path}\n"
        f"   Size: {orig_size:,} → {new_size:,} bytes"
    )
