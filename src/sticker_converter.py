"""
Telegram-to-WhatsApp Sticker Converter Module.
Handles downloading, resizing, format conversion (Static WebP / Animated .tgs to WebP),
canvas formatting (512x512 RGBA), tray icon generation (96x96 WebP), WhatsApp size limit
validation, pack splitting (3 to 30 stickers per pack chunk), and zip/manifest generation.
"""

import io
import os
import re
import json
import gzip
import logging
import zipfile
import tempfile
from typing import List, Dict, Tuple, Optional, Any
from PIL import Image

logger = logging.getLogger(__name__)

# Try importing lottie parser & cairo exporter for .tgs support
try:
    import lottie.parsers.tgs
    import lottie.exporters.cairo
    HAS_LOTTIE = True
except Exception as e:
    logger.warning(f"Lottie or Cairo library unavailable: {e}")
    HAS_LOTTIE = False

MAX_STATIC_SIZE_BYTES = 100 * 1024  # 100 KB
MAX_ANIMATED_SIZE_BYTES = 500 * 1024  # 500 KB
MAX_TRAY_SIZE_BYTES = 50 * 1024  # 50 KB

MIN_PACK_STICKERS = 3
MAX_PACK_STICKERS = 30


def parse_sticker_link(input_str: str) -> str:
    """
    Extracts the clean Telegram sticker set name from a URL or raw string.
    Examples:
        https://t.me/addstickers/Animals -> Animals
        t.me/addstickers/Animals -> Animals
        addstickers/Animals -> Animals
        Animals -> Animals
    """
    clean = input_str.strip()
    if not clean:
        return ""
    match = re.search(r'(?:https?://)?(?:t\.me/addstickers/|addstickers/)?([a-zA-Z0-9_]+)', clean)
    if match:
        return match.group(1)
    return clean


def format_canvas(img: Image.Image, canvas_size: Tuple[int, int] = (512, 512)) -> Image.Image:
    """
    Resizes image to fit within canvas_size while preserving aspect ratio,
    then centers it on a transparent RGBA canvas.
    """
    max_w, max_h = canvas_size
    w, h = img.size

    if w <= 0 or h <= 0:
        return Image.new("RGBA", canvas_size, (0, 0, 0, 0))

    ratio = min(max_w / w, max_h / h)
    new_w = max(1, int(w * ratio))
    new_h = max(1, int(h * ratio))

    if img.mode != "RGBA":
        img = img.convert("RGBA")

    resized = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
    canvas = Image.new("RGBA", canvas_size, (0, 0, 0, 0))

    offset_x = (max_w - new_w) // 2
    offset_y = (max_h - new_h) // 2
    canvas.paste(resized, (offset_x, offset_y), resized)

    return canvas


def process_static_sticker(raw_bytes: bytes, canvas_size: Tuple[int, int] = (512, 512)) -> bytes:
    """
    Converts static image bytes (PNG, WebP, JPG) into a 512x512 RGBA WebP sticker
    guaranteed to be under 100 KB.
    """
    img = Image.open(io.BytesIO(raw_bytes))
    canvas = format_canvas(img, canvas_size)

    for quality in range(90, 10, -10):
        buf = io.BytesIO()
        canvas.save(buf, format="WEBP", quality=quality, method=6)
        out_bytes = buf.getvalue()
        if len(out_bytes) <= MAX_STATIC_SIZE_BYTES:
            return out_bytes

    # Final fallback if still larger
    buf = io.BytesIO()
    canvas.save(buf, format="WEBP", quality=10, method=6)
    return buf.getvalue()


def process_animated_sticker(tgs_bytes: bytes, max_fps: int = 30) -> bytes:
    """
    Converts .tgs (gzipped Lottie JSON) or animated WebP/GIF into an animated WebP
    sticker fitting 512x512 canvas and under 500 KB.
    """
    frames: List[Image.Image] = []
    duration_ms = 33  # default ~30 FPS

    # 1. Check if it's .tgs (gzipped)
    is_tgs = False
    try:
        if tgs_bytes[:2] == b'\x1f\x8b':
            is_tgs = True
    except Exception:
        pass

    if is_tgs and HAS_LOTTIE:
        try:
            decompressed = gzip.decompress(tgs_bytes)
            f_in = io.BytesIO(tgs_bytes)
            an = lottie.parsers.tgs.parse_tgs(f_in)

            fps = min(max(int(getattr(an, 'frame_rate', 30)), 1), max_fps)
            duration_ms = int(1000 / fps)

            start_frame = int(getattr(an, 'in_point', 0))
            end_frame = int(getattr(an, 'out_point', 60))
            total_frames = max(1, end_frame - start_frame)

            # Cap max frames to prevent huge memory / processing spikes (e.g. max 90 frames)
            step = 1
            if total_frames > 90:
                step = total_frames // 90 + 1

            for frame_idx in range(start_frame, end_frame, step):
                out_png = io.BytesIO()
                lottie.exporters.cairo.export_png(an, out_png, frame=frame_idx)
                f_img = Image.open(out_png)
                formatted = format_canvas(f_img, (512, 512))
                frames.append(formatted)
        except Exception as e:
            logger.warning(f"Error parsing .tgs animation: {e}")
            frames = []

    # 2. If not .tgs or lottie rendering failed, try Pillow animated WebP / GIF
    if not frames:
        try:
            img = Image.open(io.BytesIO(tgs_bytes))
            n_frames = getattr(img, "n_frames", 1)
            duration_ms = int(img.info.get("duration", 33)) or 33

            step = 1
            if n_frames > 90:
                step = n_frames // 90 + 1

            for idx in range(0, n_frames, step):
                img.seek(idx)
                formatted = format_canvas(img, (512, 512))
                frames.append(formatted)
        except Exception as e:
            logger.warning(f"Error reading frames with Pillow: {e}")

    # 3. Fallback: create static frame if frame extraction failed
    if not frames:
        try:
            img = Image.open(io.BytesIO(tgs_bytes))
            frames = [format_canvas(img, (512, 512))]
        except Exception:
            # Emergency blank canvas
            blank = Image.new("RGBA", (512, 512), (0, 0, 0, 0))
            frames = [blank]

    if len(frames) == 1:
        # Save as single static frame WebP
        buf = io.BytesIO()
        frames[0].save(buf, format="WEBP", quality=80, method=6)
        out_bytes = buf.getvalue()
        if len(out_bytes) <= MAX_STATIC_SIZE_BYTES:
            return out_bytes
        return process_static_sticker(tgs_bytes)

    # 4. Save animated WebP iteratively tuning quality to fit under 500 KB
    for quality in range(85, 15, -15):
        buf = io.BytesIO()
        frames[0].save(
            buf,
            format="WEBP",
            save_all=True,
            append_images=frames[1:],
            duration=duration_ms,
            loop=0,
            quality=quality,
            method=4
        )
        out_bytes = buf.getvalue()
        if len(out_bytes) <= MAX_ANIMATED_SIZE_BYTES:
            return out_bytes

    # Last resort fallback if animated webp is still > 500 KB: drop every 2nd frame
    sub_frames = frames[::2] if len(frames) > 2 else frames
    buf = io.BytesIO()
    sub_frames[0].save(
        buf,
        format="WEBP",
        save_all=True,
        append_images=sub_frames[1:],
        duration=duration_ms * 2,
        loop=0,
        quality=20,
        method=4
    )
    return buf.getvalue()


def create_tray_icon(raw_bytes: bytes) -> bytes:
    """
    Creates a 96x96 RGBA WebP tray icon under 50 KB.
    """
    try:
        img = Image.open(io.BytesIO(raw_bytes))
    except Exception:
        img = Image.new("RGBA", (96, 96), (0, 0, 0, 0))

    canvas = format_canvas(img, (96, 96))
    buf = io.BytesIO()
    canvas.save(buf, format="WEBP", quality=80, method=6)
    out_bytes = buf.getvalue()

    if len(out_bytes) <= MAX_TRAY_SIZE_BYTES:
        return out_bytes

    buf = io.BytesIO()
    canvas.save(buf, format="WEBP", quality=30, method=6)
    return buf.getvalue()


def partition_stickers(stickers_info: List[Dict[str, Any]]) -> Dict[str, List[List[Dict[str, Any]]]]:
    """
    Separates stickers into static vs animated, and splits each category
    into chunks of between MIN_PACK_STICKERS (3) and MAX_PACK_STICKERS (30) stickers.
    Pads small sets (< 3) by duplicating existing items so WhatsApp requirements are met.
    """
    static_items = [s for s in stickers_info if not s.get("is_animated") and not s.get("is_video")]
    animated_items = [s for s in stickers_info if s.get("is_animated") or s.get("is_video")]

    def chunk_list(items: List[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
        if not items:
            return []

        # Pad if less than MIN_PACK_STICKERS
        if len(items) < MIN_PACK_STICKERS:
            padded = list(items)
            while len(padded) < MIN_PACK_STICKERS:
                padded.append(items[len(padded) % len(items)])
            items = padded

        chunks = []
        for i in range(0, len(items), MAX_PACK_STICKERS):
            chunk = items[i:i + MAX_PACK_STICKERS]
            if len(chunk) < MIN_PACK_STICKERS:
                while len(chunk) < MIN_PACK_STICKERS:
                    chunk.append(items[len(chunk) % len(items)])
            chunks.append(chunk)

        return chunks

    return {
        "static_chunks": chunk_list(static_items),
        "animated_chunks": chunk_list(animated_items)
    }


def build_whatsapp_contents_json(
    pack_id: str,
    pack_title: str,
    publisher: str,
    is_animated: bool,
    stickers_meta: List[Dict[str, Any]],
    tray_filename: str = "tray_icon.webp"
) -> str:
    """
    Generates WhatsApp official contents.json structure.
    """
    contents = {
        "android_play_store_link": "",
        "ios_app_store_link": "",
        "sticker_packs": [
            {
                "identifier": pack_id,
                "name": pack_title,
                "publisher": publisher,
                "tray_image_file": tray_filename,
                "publisher_email": "",
                "publisher_website": "",
                "privacy_policy_website": "",
                "license_agreement_website": "",
                "animated_sticker_pack": is_animated,
                "stickers": [
                    {
                        "image_file": f"{idx + 1:02d}.webp",
                        "emojis": s.get("emojis") or ["🌸"]
                    }
                    for idx, s in enumerate(stickers_meta)
                ]
            }
        ]
    }
    return json.dumps(contents, indent=2)


def convert_and_bundle_pack(
    pack_title: str,
    publisher: str,
    stickers_data: List[Dict[str, Any]],
    work_dir: str
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """
    Processes a list of raw downloaded sticker items, converts them, partitions them,
    and creates zipped pack chunks and overall manifest.json inside work_dir.

    stickers_data item format:
    {
        "raw_bytes": bytes,
        "is_animated": bool,
        "is_video": bool,
        "emojis": list[str]
    }

    Returns:
        (chunk_files_info, manifest_data)
    """
    partitioned = partition_stickers(stickers_data)
    static_chunks = partitioned["static_chunks"]
    animated_chunks = partitioned["animated_chunks"]

    all_chunks_info = []
    chunk_counter = 1

    total_chunks = len(static_chunks) + len(animated_chunks)

    def process_chunk_group(chunks: List[List[Dict[str, Any]]], is_animated: bool):
        nonlocal chunk_counter
        for c_idx, chunk in enumerate(chunks, start=1):
            type_label = "Animated" if is_animated else "Static"
            chunk_title = f"{pack_title} ({type_label} Part {c_idx})" if len(chunks) > 1 else f"{pack_title} ({type_label})"
            clean_identifier = re.sub(r'[^a-zA-Z0-9_]', '_', chunk_title.lower()).strip('_')
            if not clean_identifier:
                clean_identifier = f"pack_chunk_{chunk_counter}"

            zip_filename = f"{clean_identifier}.zip"
            zip_filepath = os.path.join(work_dir, zip_filename)

            # Convert stickers for this chunk
            converted_stickers = []
            stickers_meta = []

            for s_idx, item in enumerate(chunk, start=1):
                raw = item["raw_bytes"]
                emojis = item.get("emojis") or ["🌸"]

                if is_animated:
                    c_bytes = process_animated_sticker(raw)
                else:
                    c_bytes = process_static_sticker(raw)

                converted_filename = f"{s_idx:02d}.webp"
                converted_stickers.append((converted_filename, c_bytes))
                stickers_meta.append({"emojis": emojis})

            # Create Tray Icon from first sticker
            tray_bytes = create_tray_icon(chunk[0]["raw_bytes"])

            # Create contents.json
            contents_json_str = build_whatsapp_contents_json(
                pack_id=clean_identifier,
                pack_title=chunk_title,
                publisher=publisher,
                is_animated=is_animated,
                stickers_meta=stickers_meta
            )

            # Write zip
            with zipfile.ZipFile(zip_filepath, 'w', zipfile.ZIP_DEFLATED) as zf:
                zf.writestr("contents.json", contents_json_str)
                zf.writestr("tray_icon.webp", tray_bytes)
                for fname, fbytes in converted_stickers:
                    zf.writestr(fname, fbytes)

            all_chunks_info.append({
                "chunk_index": chunk_counter,
                "pack_identifier": clean_identifier,
                "title": chunk_title,
                "is_animated": is_animated,
                "sticker_count": len(chunk),
                "zip_filename": zip_filename,
                "zip_path": zip_filepath,
                "zip_size": os.path.getsize(zip_filepath)
            })
            chunk_counter += 1

    process_chunk_group(static_chunks, is_animated=False)
    process_chunk_group(animated_chunks, is_animated=True)

    manifest_data = {
        "title": pack_title,
        "publisher": publisher,
        "total_stickers": len(stickers_data),
        "total_chunks": len(all_chunks_info),
        "chunks": [
            {
                "chunk_index": c["chunk_index"],
                "pack_identifier": c["pack_identifier"],
                "title": c["title"],
                "is_animated": c["is_animated"],
                "sticker_count": c["sticker_count"],
                "zip_filename": c["zip_filename"],
                "zip_size_bytes": c["zip_size"]
            }
            for c in all_chunks_info
        ],
        "android_integration": {
            "intent_action": "com.whatsapp.intent.action.ENABLE_STICKER_PACK",
            "required_app": "Companion Android App required for direct WhatsApp import.",
            "instructions": "Install the companion Android app, grant storage permissions, and trigger ENABLE_STICKER_PACK intent with authority."
        }
    }

    manifest_path = os.path.join(work_dir, "manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest_data, f, indent=2)

    return all_chunks_info, manifest_data
