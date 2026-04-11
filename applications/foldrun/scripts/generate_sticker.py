"""Generate FoldRun sticker SVG with logo, tagline, and QR code.

Usage:
    uv venv /tmp/sticker-venv
    source /tmp/sticker-venv/bin/activate
    uv pip install "qrcode[pil]"
    python generate_sticker.py

Outputs:
    ../foldrun-sticker.svg  - Full circular sticker
    ../foldrun-qr.svg       - Standalone QR code
"""

import io
import xml.etree.ElementTree as ET

import qrcode
import qrcode.image.svg

# --- Configuration ---
DEMO_URL = "https://youtu.be/umTLrEF5L7A"
TITLE = "FoldRun"
TAGLINE = "Agentic Protein Folding on GCP"
SCAN_LABEL = "Scan to watch demo"
OUTPUT_STICKER = "../foldrun-sticker.svg"
OUTPUT_QR = "../foldrun-qr.svg"

# Google brand colors
GOOGLE_BLUE = "#4285f4"
GOOGLE_RED = "#ea4335"
GOOGLE_YELLOW = "#fbbc04"
GOOGLE_GREEN = "#34a853"
TEXT_PRIMARY = "#202124"
TEXT_SECONDARY = "#5f6368"
FONT_FAMILY = "Google Sans, Product Sans, Roboto, Arial, sans-serif"

# DNA helix path data (from uxwing.com/dna-icon)
HELIX_PATH = (
    "M115.82 496.61c16.08,-21.9 23.85,-41.68 27.08,-62.84 "
    "2.24,-14.62 2.4,-30.2 1.65,-47.69l8.78 0.45 0 -0.05c5.71,0.41 "
    "11.56,0.59 17.29,0.88l0.53 0.02c0.7,18.07 0.42,34.48 -2.02,50.43 "
    "-3.88,25.36 -13.04,48.86 -31.93,74.59l-21.38 -15.79zm-46.9 "
    "-123.72l59.4 59.4 -5.01 23.84 -76.66 -76.66c-10.51,4.1 "
    "-20.72,9.65 -30.56,17.11l-16.09 -21.08c49.36,-37.43 "
    "102.43,-34.69 155.59,-31.95 6,0.31 12,0.62 16.82,0.83 "
    "21.05,0.9 41.94,1.07 61.99,-2.26l-52.97 -52.97 5 -23.85 "
    "71.12 71.11c14.12,-4.75 27.91,-11.99 41.25,-22.85l-99.68 "
    "-99.67c-17.99,19.58 -26.37,41.02 -29.83,63.81 -2.41,15.85 "
    "-2.49,32.61 -1.73,49.96l-10.21 -0.52c-0.58,-0.06 -1.16,-0.09 "
    "-1.76,-0.09 -4.88,-0.25 -9.75,-0.5 -14.63,-0.72 -0.72,-17.93 "
    "-0.51,-35.48 2.1,-52.67 4.3,-28.35 15.02,-55.1 38.41,-79.69 "
    "24.96,-26.25 51.67,-39.99 79.32,-46.87 21.97,-5.47 44.11,-6.51 "
    "66.29,-6.09 0.31,5.24 0.67,10.62 1.06,16.17 0.25,3.49 0.49,6.96 "
    "0.72,10.42 -16.87,-0.4 -33.55,0.02 -49.68,2.74l54.11 54.11 "
    "-5.01 23.84 -72.21 -72.21c-14.44,4.9 -28.55,12.41 "
    "-42.17,23.74l99.82 99.82c18.71,-19.89 27.35,-41.71 "
    "30.88,-64.93 3.72,-24.46 1.9,-51.11 -0.04,-78.72 "
    "-2.19,-31.17 -3.56,-56.83 0.2,-81.4 3.88,-25.35 13.04,-48.86 "
    "31.93,-74.59l21.37 15.8c-16.07,21.9 -23.84,41.68 -27.08,62.83 "
    "-3.35,21.93 -2.04,46.04 0.02,75.45 2.05,29.19 3.97,57.37 "
    "-0.17,84.67 -4.31,28.35 -15.02,55.09 -38.41,79.69 "
    "-24.96,26.25 -51.67,39.99 -79.32,46.87 -27.09,6.74 "
    "-54.43,6.76 -81.79,5.59 -6.48,-0.28 -11.8,-0.55 -17.11,-0.83 "
    "-28.93,-1.49 -57.83,-2.98 -85.28,2.82zm373.73 -232.9l-57.36 "
    "-57.36 5.01 -23.84 74.92 74.92c11.21,-4.17 22.11,-9.95 "
    "32.58,-17.89l16.09 21.09c-39.99,30.31 -82.39,34.28 "
    "-125.31,33.23 -0.36,-5.72 -0.76,-11.46 -1.16,-17.25 "
    "-0.23,-3.19 -0.44,-6.31 -0.64,-9.38 18.96,0.52 37.72,0.04 "
    "55.87,-3.52z"
)


def generate_qr_path(url: str) -> str:
    """Generate QR code and return its SVG path data."""
    factory = qrcode.image.svg.SvgPathImage
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=2,
    )
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(image_factory=factory)

    # Save standalone QR
    img.save(OUTPUT_QR)
    print(f"QR code saved to {OUTPUT_QR}")

    # Extract path data from SVG
    buf = io.BytesIO()
    img.save(buf)
    buf.seek(0)
    tree = ET.parse(buf)
    root = tree.getroot()
    ns = {"svg": "http://www.w3.org/2000/svg"}
    path_el = root.find(".//svg:path", ns) or root.find(".//path")
    return path_el.get("d", "")


def build_sticker(qr_path_data: str) -> str:
    """Build the complete sticker SVG string."""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 300 300" width="300" height="300">
  <defs>
    <linearGradient id="google-grad" x1="0%" y1="0%" x2="0%" y2="100%">
      <stop offset="0%" stop-color="{GOOGLE_BLUE}"/>
      <stop offset="33%" stop-color="{GOOGLE_RED}"/>
      <stop offset="66%" stop-color="{GOOGLE_YELLOW}"/>
      <stop offset="100%" stop-color="{GOOGLE_GREEN}"/>
    </linearGradient>
  </defs>

  <!-- Sticker background circle -->
  <circle cx="150" cy="150" r="148" fill="white" stroke="#e0e0e0" stroke-width="2"/>

  <!-- DNA helix icon in Google colors (centered, upper portion) -->
  <g transform="translate(115, 20) scale(0.14)">
    <path fill="url(#google-grad)" fill-rule="nonzero" d="{HELIX_PATH}"/>
  </g>

  <!-- Title text matching Cloud Run website typography -->
  <text x="150" y="120" text-anchor="middle" font-family="{FONT_FAMILY}" font-size="36" font-weight="400" fill="{TEXT_PRIMARY}" letter-spacing="-0.3">{TITLE}</text>

  <!-- Tagline -->
  <text x="150" y="143" text-anchor="middle" font-family="{FONT_FAMILY}" font-size="11" fill="{TEXT_SECONDARY}">{TAGLINE}</text>

  <!-- QR Code (scaled and centered at bottom) -->
  <g transform="translate(90, 155) scale(3.636)">
    <path d="{qr_path_data}" fill="{TEXT_PRIMARY}" fill-rule="nonzero" stroke="none"/>
  </g>

  <!-- Scan label -->
  <text x="150" y="280" text-anchor="middle" font-family="{FONT_FAMILY}" font-size="9" fill="{TEXT_SECONDARY}">{SCAN_LABEL}</text>
</svg>
"""


def main():
    qr_path_data = generate_qr_path(DEMO_URL)
    sticker_svg = build_sticker(qr_path_data)

    with open(OUTPUT_STICKER, "w") as f:
        f.write(sticker_svg)

    print(f"Sticker saved to {OUTPUT_STICKER}")
    print(f"\nConfiguration:")
    print(f"  Title:   {TITLE}")
    print(f"  Tagline: {TAGLINE}")
    print(f"  QR URL:  {DEMO_URL}")


if __name__ == "__main__":
    main()
