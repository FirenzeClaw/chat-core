"""MultimodalHandler — 图像检测与视觉模型降级链"""

from __future__ import annotations

import base64
import json
import re
from pathlib import Path
from typing import Any

from chat_core.config import get_config


# 支持的图片扩展名
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}

# 图片 URL 模式
IMAGE_URL_PATTERN = re.compile(
    r"https?://[^\s]+\.(?:png|jpg|jpeg|gif|webp)(?:\?[^\s]*)?",
    re.IGNORECASE,
)

# 本地图片路径模式
IMAGE_PATH_PATTERN = re.compile(
    r"(?:\S+\.(?:png|jpg|jpeg|gif|webp))",
    re.IGNORECASE,
)


def _is_image_url(text: str) -> bool:
    """检查文本是否为图片 URL"""
    return bool(IMAGE_URL_PATTERN.fullmatch(text.strip()))


def _is_image_path(text: str) -> bool:
    """检查文本是否为本地图片路径"""
    path = Path(text.strip())
    return path.suffix.lower() in IMAGE_EXTENSIONS and path.exists()


def _encode_image_base64(path: str) -> str:
    """将本地图片编码为 base64 data URL"""
    ext = Path(path).suffix.lower().lstrip(".")
    mime_map = {
        "png": "image/png",
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "gif": "image/gif",
        "webp": "image/webp",
    }
    mime = mime_map.get(ext, "image/png")
    with open(path, "rb") as f:
        data = base64.b64encode(f.read()).decode("utf-8")
    return f"data:{mime};base64,{data}"


class MultimodalHandler:
    """四步图像处理流水线：

    1. 检测：扫描用户输入中的图片路径/URL
    2. 主视觉：检查 config 主 provider 的 vision capability → 直传 image_url
    3. 降级链：按 multimodal.chain 顺序尝试各 provider 做图像描述
    4. 不可用：所有 provider 都失败 → 注入系统提示
    """

    def __init__(self) -> None:
        cfg = get_config()
        mm = cfg.multimodal
        self._enabled: bool = bool(mm.get("enabled", True))
        self._chain: list[str] = mm.get("chain", [])
        self._supported_extensions = IMAGE_EXTENSIONS

    # ── Step 1: 检测 ──────────────────────────────────────────

    def detect_images(self, user_input: str) -> list[dict[str, Any]]:
        """扫描用户输入，返回检测到的图片列表。

        每项: { "type": "url" | "path", "value": str, "data_url": str | None }
        """
        if not self._enabled:
            return []

        images: list[dict[str, Any]] = []

        # 检测 URL
        for match in IMAGE_URL_PATTERN.finditer(user_input):
            images.append({"type": "url", "value": match.group(), "data_url": None})

        # 检测本地路径
        for match in IMAGE_PATH_PATTERN.finditer(user_input):
            candidate = match.group()
            path = Path(candidate)
            if not path.is_absolute():
                # 相对路径优先匹配，严格检查存在性
                if path.exists():
                    images.append({
                        "type": "path",
                        "value": str(path.resolve()),
                        "data_url": _encode_image_base64(str(path.resolve())),
                    })
            else:
                if path.exists():
                    images.append({
                        "type": "path",
                        "value": str(path),
                        "data_url": _encode_image_base64(str(path)),
                    })

        return images

    # ── Step 2: 主 provider vision ────────────────────────────

    def has_vision_capability(self, provider_name: str) -> bool:
        """检查 provider 是否具备 vision 能力"""
        cfg = get_config()
        api_cfg = cfg.apis.get(provider_name, {})
        capabilities: list[str] = api_cfg.get("capabilities", [])
        return "vision" in capabilities

    def build_vision_message(
        self, user_text: str, images: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """构建包含图像的多模态消息内容。

        返回格式适配 OpenAI vision API:
        [
            {"type": "text", "text": "..."},
            {"type": "image_url", "image_url": {"url": "..."}},
        ]
        """
        content: list[dict[str, Any]] = [{"type": "text", "text": user_text}]

        for img in images:
            url = img.get("data_url") or img["value"]
            content.append({
                "type": "image_url",
                "image_url": {"url": url},
            })

        return content

    # ── Step 3: 降级链 ───────────────────────────────────────

    def get_fallback_chain(self) -> list[str]:
        """返回配置的降级链 provider 列表"""
        return list(self._chain)

    def get_fallback_image_description_prompt(self) -> str:
        """生成图像描述请求的 prompt"""
        return "请用中文描述这张图片的内容。包括：主体对象、场景、动作、文字（如有）、氛围。简洁全面。"

    # ── Step 4: 不可用 ───────────────────────────────────────

    def unavailable_note(self) -> str:
        """所有 provider 不可用时的系统提示"""
        return "[系统] 用户发了一张图片，但当前没有可用的视觉模型"

    @property
    def enabled(self) -> bool:
        return self._enabled
