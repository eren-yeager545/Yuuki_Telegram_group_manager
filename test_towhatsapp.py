import io
import os
import json
import gzip
import zipfile
import tempfile
import pytest
from PIL import Image
from unittest.mock import AsyncMock, MagicMock, patch

from src.sticker_converter import (
    parse_sticker_link,
    format_canvas,
    process_static_sticker,
    process_animated_sticker,
    create_tray_icon,
    partition_stickers,
    build_whatsapp_contents_json,
    convert_and_bundle_pack,
    MAX_STATIC_SIZE_BYTES,
    MAX_ANIMATED_SIZE_BYTES,
    MAX_TRAY_SIZE_BYTES
)
from bot import towhatsapp_cmd


def create_dummy_png_bytes(width=800, height=600, color=(255, 0, 100, 255)):
    img = Image.new("RGBA", (width, height), color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def create_dummy_tgs_bytes():
    fake_lottie = {
        "v": "5.5.2",
        "fr": 30,
        "ip": 0,
        "op": 30,
        "w": 512,
        "h": 512,
        "nm": "TestAnim",
        "ddd": 0,
        "assets": [],
        "layers": []
    }
    raw_json = json.dumps(fake_lottie).encode("utf-8")
    return gzip.compress(raw_json)


def test_parse_sticker_link():
    assert parse_sticker_link("https://t.me/addstickers/Animals") == "Animals"
    assert parse_sticker_link("http://t.me/addstickers/CutePacks") == "CutePacks"
    assert parse_sticker_link("t.me/addstickers/MyPack_v2") == "MyPack_v2"
    assert parse_sticker_link("addstickers/Pack123") == "Pack123"
    assert parse_sticker_link("SimplePackName") == "SimplePackName"
    assert parse_sticker_link("") == ""


def test_format_canvas():
    raw_png = create_dummy_png_bytes(1000, 500)
    img = Image.open(io.BytesIO(raw_png))
    canvas = format_canvas(img, (512, 512))

    assert canvas.size == (512, 512)
    assert canvas.mode == "RGBA"


def test_process_static_sticker():
    raw_bytes = create_dummy_png_bytes(1200, 1200)
    webp_bytes = process_static_sticker(raw_bytes)

    assert len(webp_bytes) <= MAX_STATIC_SIZE_BYTES
    out_img = Image.open(io.BytesIO(webp_bytes))
    assert out_img.size == (512, 512)
    assert out_img.format == "WEBP"


def test_process_animated_sticker():
    tgs_bytes = create_dummy_tgs_bytes()
    webp_bytes = process_animated_sticker(tgs_bytes)

    assert len(webp_bytes) <= MAX_ANIMATED_SIZE_BYTES
    out_img = Image.open(io.BytesIO(webp_bytes))
    assert out_img.size == (512, 512)
    assert out_img.format == "WEBP"


def test_create_tray_icon():
    raw_bytes = create_dummy_png_bytes(500, 500)
    tray_bytes = create_tray_icon(raw_bytes)

    assert len(tray_bytes) <= MAX_TRAY_SIZE_BYTES
    tray_img = Image.open(io.BytesIO(tray_bytes))
    assert tray_img.size == (96, 96)
    assert tray_img.format == "WEBP"


def test_partition_stickers_chunking():
    # 35 static stickers -> split into chunk 1 (30), chunk 2 (5)
    stickers_info = [
        {"is_animated": False, "is_video": False, "id": i}
        for i in range(35)
    ]
    partitioned = partition_stickers(stickers_info)
    static_chunks = partitioned["static_chunks"]

    assert len(static_chunks) == 2
    assert len(static_chunks[0]) == 30
    assert len(static_chunks[1]) == 5

    # 1 static sticker -> padded to 3 stickers
    small_set = [{"is_animated": False, "is_video": False, "id": 1}]
    p_small = partition_stickers(small_set)
    assert len(p_small["static_chunks"][0]) == 3


def test_build_whatsapp_contents_json():
    meta = [{"emojis": ["🌸"]}, {"emojis": ["💕"]}, {"emojis": ["✨"]}]
    json_str = build_whatsapp_contents_json(
        pack_id="my_pack",
        pack_title="My Pack",
        publisher="Yuki Bot",
        is_animated=False,
        stickers_meta=meta
    )
    parsed = json.loads(json_str)

    assert "sticker_packs" in parsed
    pack = parsed["sticker_packs"][0]
    assert pack["identifier"] == "my_pack"
    assert pack["name"] == "My Pack"
    assert pack["publisher"] == "Yuki Bot"
    assert len(pack["stickers"]) == 3


def test_convert_and_bundle_pack():
    static_bytes = create_dummy_png_bytes(400, 400)
    stickers_data = [
        {"raw_bytes": static_bytes, "is_animated": False, "is_video": False, "emojis": ["🌸"]}
        for _ in range(4)
    ]

    with tempfile.TemporaryDirectory() as temp_dir:
        chunks_info, manifest_data = convert_and_bundle_pack(
            pack_title="Test Pack",
            publisher="Yuki Bot",
            stickers_data=stickers_data,
            work_dir=temp_dir
        )

        assert len(chunks_info) == 1
        chunk = chunks_info[0]
        assert os.path.exists(chunk["zip_path"])

        # Check Zip Contents
        with zipfile.ZipFile(chunk["zip_path"], "r") as zf:
            namelist = zf.namelist()
            assert "contents.json" in namelist
            assert "tray_icon.webp" in namelist
            assert "01.webp" in namelist

        # Check Manifest
        manifest_path = os.path.join(temp_dir, "manifest.json")
        assert os.path.exists(manifest_path)
        assert manifest_data["total_stickers"] == 4


@pytest.mark.asyncio
async def test_towhatsapp_cmd_usage_prompt():
    update = AsyncMock()
    context = AsyncMock()

    update.effective_message = AsyncMock()
    context.args = []
    update.effective_message.reply_to_message = None

    await towhatsapp_cmd(update, context)

    update.effective_message.reply_html.assert_called_once()
    assert "/towhatsapp" in update.effective_message.reply_html.call_args[0][0]


@pytest.mark.asyncio
async def test_towhatsapp_cmd_execution_flow():
    update = AsyncMock()
    context = AsyncMock()

    update.effective_message = AsyncMock()
    progress_msg = AsyncMock()
    update.effective_message.reply_text.return_value = progress_msg

    context.args = ["https://t.me/addstickers/Animals"]

    # Mock Telegram Sticker Set & File
    fake_sticker = MagicMock()
    fake_sticker.file_id = "sticker_1"
    fake_sticker.is_animated = False
    fake_sticker.is_video = False
    fake_sticker.emoji = "🐱"

    fake_sticker_set = MagicMock()
    fake_sticker_set.title = "Cute Animals"
    fake_sticker_set.stickers = [fake_sticker] * 3

    context.bot.get_sticker_set.return_value = fake_sticker_set

    fake_file = AsyncMock()
    dummy_png = create_dummy_png_bytes(300, 300)

    async def mock_download_to_memory(buf):
        buf.write(dummy_png)

    fake_file.download_to_memory.side_effect = mock_download_to_memory
    context.bot.get_file.return_value = fake_file

    await towhatsapp_cmd(update, context)

    context.bot.get_sticker_set.assert_called_once_with("Animals")
    assert update.effective_message.reply_document.call_count >= 2  # zip + manifest
    assert update.effective_message.reply_html.call_count >= 1
    assert "Conversion Complete!" in update.effective_message.reply_html.call_args[0][0]



@pytest.mark.asyncio
async def test_towhatsapp_tempfile_cleanup():
    """Verify temporary directory and files are purged under success and failure scenarios."""
    update = AsyncMock()
    context = AsyncMock()
    update.effective_message = AsyncMock()
    progress_msg = AsyncMock()
    update.effective_message.reply_text.return_value = progress_msg
    context.args = ["https://t.me/addstickers/TestCleanupPack"]

    fake_sticker = MagicMock()
    fake_sticker.file_id = "stk_1"
    fake_sticker.is_animated = False
    fake_sticker.is_video = False
    fake_sticker.emoji = "🌸"

    fake_sticker_set = MagicMock()
    fake_sticker_set.title = "Cleanup Pack"
    fake_sticker_set.stickers = [fake_sticker] * 3
    context.bot.get_sticker_set.return_value = fake_sticker_set

    fake_file = AsyncMock()
    dummy_png = create_dummy_png_bytes(200, 200)

    async def mock_download_to_memory(buf):
        buf.write(dummy_png)

    fake_file.download_to_memory.side_effect = mock_download_to_memory
    context.bot.get_file.return_value = fake_file

    created_dirs = []
    original_mkdtemp = tempfile.mkdtemp

    def tracking_mkdtemp(*args, **kwargs):
        d = original_mkdtemp(*args, **kwargs)
        created_dirs.append(d)
        return d

    with patch("tempfile.mkdtemp", side_effect=tracking_mkdtemp):
        await towhatsapp_cmd(update, context)

    assert len(created_dirs) == 1
    target_dir = created_dirs[0]
    # Assert temporary directory was completely removed
    assert not os.path.exists(target_dir)

    # Test failure / exception scenario
    created_dirs.clear()
    update.effective_message.reply_document.side_effect = Exception("Telegram Upload Failed")

    with patch("tempfile.mkdtemp", side_effect=tracking_mkdtemp):
        await towhatsapp_cmd(update, context)

    assert len(created_dirs) == 1
    fail_dir = created_dirs[0]
    assert not os.path.exists(fail_dir)
