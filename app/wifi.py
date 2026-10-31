"""Guest Wi-Fi QR code: point a phone camera at it to join.

Uses the standard WIFI: payload that iOS and Android cameras understand.
"""

import base64
import io
import struct
from pathlib import Path

import segno

def _escape(s: str) -> str:
    """Backslash the characters the WIFI: format treats as separators."""
    for c in '\\;,:"':  # backslash first, so the ones added aren't doubled
        s = s.replace(c, "\\" + c)
    return s


def payload(ssid: str, password: str | None, security: str = "WPA", hidden: bool = False) -> str:
    security = security if password else "nopass"
    out = f"WIFI:T:{security};S:{_escape(ssid)};"
    if password:
        out += f"P:{_escape(password)};"
    if hidden:
        out += "H:true;"
    return out + ";"


LOGO = Path(__file__).parent / "qr_cat.png"  # pixelated cat cut-out, white outline, transparent around
LOGO_HEIGHT = 0.5  # of the code's width; the cut-out covers ~13% of the modules, well inside H's 30%


def png_size(data: bytes) -> tuple[int, int]:
    """Width and height from a PNG's header, so the picture keeps its shape."""
    return struct.unpack(">II", data[16:24])


def logo_box(width: int, data: bytes) -> tuple[float, float]:
    """The picture's size in modules for a code `width` modules across."""
    w, h = png_size(data)
    height = width * LOGO_HEIGHT
    return height * w / h, height


def qr_svg(text: str, logo: Path | None = LOGO) -> bytes:
    """Black on white whatever the screen theme, so every phone camera reads it.

    With a logo, error correction goes up to H (30% recoverable) and the
    picture covers the middle, which the decoder treats as damage. Only the
    cat and its outline cover modules; the code shows through around it.
    """
    border = 2
    qr = segno.make(text, error="h" if logo else "m", boost_error=False)
    buf = io.BytesIO()
    qr.save(buf, kind="svg", scale=10, border=border, dark="#000", light="#fff", xmldecl=False)
    svg = buf.getvalue()
    if not (logo and logo.exists()):
        return svg
    data = logo.read_bytes()
    width = qr.symbol_size(border=0)[0]
    w, h = logo_box(width, data)
    x, y = border + (width - w) / 2, border + (width - h) / 2
    href = "data:image/png;base64," + base64.b64encode(data).decode()
    image = (
        f'<image href="{href}" x="{x:.2f}" y="{y:.2f}" width="{w:.2f}" height="{h:.2f}"'
        ' preserveAspectRatio="none" style="image-rendering:pixelated"/>'
    )
    return svg.replace(b"</g></svg>", image.encode() + b"</g></svg>")
