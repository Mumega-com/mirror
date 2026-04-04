"""
Lambda Tensor — LSB steganography for embedding 16D vectors into PNG avatars.

Encodes agent DNA (JSON) into the least-significant bits of image pixels.
The image looks identical to the naked eye but carries the agent's full genetic code.

Requires: Pillow, numpy
"""

import json
import logging
import os
from typing import Dict, Optional, Any

import numpy as np
from PIL import Image, ImageDraw

logger = logging.getLogger("mirror.lambda_tensor")

AVATAR_DIR = os.path.join(os.path.dirname(__file__), "avatars")
os.makedirs(AVATAR_DIR, exist_ok=True)


def generate_base_avatar(agent_id: str, tensor: list, size: int = 512) -> str:
    """
    Generate a procedural PNG avatar from the 16D tensor.
    Returns the file path.
    """
    img = Image.new("RGB", (size, size), (10, 9, 8))
    draw = ImageDraw.Draw(img)
    cx, cy = size // 2, size // 2

    # Use tensor values to drive the geometry
    import math

    for i, val in enumerate(tensor):
        angle = (i / len(tensor)) * 2 * math.pi
        radius = int(abs(val) * size * 0.35) + 20
        hue_shift = int((val + 1) * 127)

        # Color from tensor position
        r = max(0, min(255, 6 + hue_shift if i < 4 else 20))
        g = max(0, min(255, 182 if i < 8 else hue_shift))
        b = max(0, min(255, 212 if i < 12 else 40 + hue_shift))

        x1 = cx + int(radius * math.cos(angle))
        y1 = cy + int(radius * math.sin(angle))
        x2 = cx + int(radius * 0.3 * math.cos(angle + 0.5))
        y2 = cy + int(radius * 0.3 * math.sin(angle + 0.5))

        # Draw translucent circles at each axis point
        overlay = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        odraw = ImageDraw.Draw(overlay)
        cr = 15 + int(abs(val) * 25)
        odraw.ellipse(
            [x1 - cr, y1 - cr, x1 + cr, y1 + cr],
            fill=(r, g, b, 40),
            outline=(r, g, b, 80),
        )
        # Line from center to point
        odraw.line([(cx, cy), (x1, y1)], fill=(r, g, b, 25), width=1)
        img.paste(Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB"))

    # Center dot
    draw = ImageDraw.Draw(img)
    draw.ellipse([cx - 4, cy - 4, cx + 4, cy + 4], fill=(255, 255, 255, 200))

    path = os.path.join(AVATAR_DIR, f"{agent_id}.png")
    img.save(path, "PNG")
    return path


def encode_tensor(image_path: str, data: Dict[str, Any]) -> bool:
    """
    Embed a JSON-serializable dict into a PNG image via LSB steganography.
    Overwrites the image in-place.
    """
    try:
        json_str = json.dumps(data)
        binary_data = _str_to_bin(json_str)

        # 32-bit length prefix
        length_bin = format(len(binary_data), "032b")
        payload = length_bin + binary_data

        img = Image.open(image_path)
        if img.mode != "RGB":
            img = img.convert("RGB")

        pixels = np.array(img)
        flat = pixels.flatten()

        if len(payload) > len(flat):
            logger.error(f"Data too large ({len(payload)} bits) for image ({len(flat)} channels)")
            return False

        for i in range(len(payload)):
            flat[i] = (int(flat[i]) & 0xFE) | int(payload[i])

        new_pixels = flat.reshape(pixels.shape)
        Image.fromarray(new_pixels.astype(np.uint8)).save(image_path, "PNG")
        logger.info(f"Encoded {len(json_str)} bytes into {image_path}")
        return True
    except Exception as e:
        logger.error(f"Encoding failed: {e}")
        return False


def decode_tensor(image_path: str) -> Optional[Dict[str, Any]]:
    """Extract embedded JSON from a PNG image."""
    try:
        img = Image.open(image_path)
        if img.mode != "RGB":
            img = img.convert("RGB")

        flat = np.array(img).flatten()

        # Read 32-bit length
        length_bits = "".join(str(int(flat[i]) & 1) for i in range(32))
        data_length = int(length_bits, 2)

        if data_length <= 0 or data_length > len(flat) - 32:
            return None

        payload_bits = "".join(str(int(flat[i]) & 1) for i in range(32, 32 + data_length))
        json_str = _bin_to_str(payload_bits)
        return json.loads(json_str)
    except Exception as e:
        logger.error(f"Decoding failed: {e}")
        return None


def _str_to_bin(s: str) -> str:
    return "".join(format(ord(c), "08b") for c in s)


def _bin_to_str(b: str) -> str:
    return "".join(chr(int(b[i : i + 8], 2)) for i in range(0, len(b), 8))
