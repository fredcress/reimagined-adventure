"""Guest Wi-Fi QR code: point a phone camera at it to join.

Uses the standard WIFI: payload that iOS and Android cameras understand.
"""

import io

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


def qr_svg(text: str) -> bytes:
    """Black on white whatever the screen theme, so every phone camera reads it."""
    buf = io.BytesIO()
    segno.make(text, error="m").save(buf, kind="svg", scale=10, border=2, dark="#000", light="#fff", xmldecl=False)
    return buf.getvalue()
