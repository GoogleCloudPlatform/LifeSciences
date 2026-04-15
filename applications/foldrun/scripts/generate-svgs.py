"""Generate FoldRun SVG assets: sticker, helix icon, and wordmark.

Usage:
    uv venv /tmp/sticker-venv
    source /tmp/sticker-venv/bin/activate
    uv pip install "qrcode[pil]"
    python generate-svgs.py

Outputs:
    ../img/foldrun-sticker.svg  - Full circular sticker
    ../img/foldrun-qr.svg       - Standalone QR code
    ../img/foldrun-helix.svg    - Standalone DNA helix icon
    ../img/foldrun-icon.svg     - Helix + FoldRun wordmark
"""

import io
import xml.etree.ElementTree as ET

import qrcode
import qrcode.image.svg

# --- Configuration ---
DEMO_URL = "https://bit.ly/foldrun"
TITLE = "FoldRun"
TAGLINE = "Agentic Protein Folding on"
SCAN_LABEL = "https://goo.gle/foldrun"

# Google Cloud icon (../img/google-cloud.svg, viewBox 0 0 128 128, 4-color logo)
GCP_ICON_FILE = "../img/google-cloud.svg"
OUTPUT_STICKER = "../img/foldrun-sticker.svg"
OUTPUT_QR = "../img/foldrun-qr.svg"
OUTPUT_HELIX = "../img/foldrun-helix.svg"
OUTPUT_ICON = "../img/foldrun-icon.svg"

# Google brand colors
GOOGLE_BLUE = "#4285f4"
GOOGLE_RED = "#ea4335"
GOOGLE_YELLOW = "#fbbc04"
GOOGLE_GREEN = "#34a853"
TEXT_PRIMARY = "#202124"
TEXT_SECONDARY = "#5f6368"
FONT_FAMILY = "Google Sans, Noto Sans, Helvetica, Arial, sans-serif"
FONT_FAMILY_DISPLAY = "Google Sans Display, Google Sans, Noto Sans, Helvetica, Arial, sans-serif"

# DNA helix path inspired by https://uxwing.com/dna-icon/
HELIX_PATH = (
    "M120.06,489.41c15.49 -21.5,23.23 -40.69,26.29 -61.04,"
    "1.91 -14.18,2.1 -29.3,1.34 -46.50l8.47,0.44,0 -0.05"
    "c5.73,0.40,10.99,0.41,16.85,1.12l0.56,0.02"
    "c0.6,17.81,0.41,33.17 -1.74,48.79 "
    "-3.98,24.37 -12.76,47.58 -31.16,72.40l-20.66 -15.39z"
    "m-45.5 -120.27l57.4,57.4 -4.75,23.08 -74.47 -74.31"
    "c-10.22,3.9 -19.92,9.48 -29.80,16.64l-15.59 -20.22"
    "c48.02 -36.43,99.65 -33.88,150.87 -30.84,"
    "6,0.30,12,0.32,16.42,0.96,"
    "20.46,1.1,40.57,1.16,60.19 -2.14l-51.41 -51.18,"
    "5 -23.15,69.08,68.71c13.82 -4.52,27.37 -11.44,39.88 -22.23"
    "l-96.59 -96.97c-17.47,18.79 -25.81,39.52 -28.77,61.67 "
    "-2.49,15.31 -2.19,31.38 -1.71,48.49l-9.67 -0.31"
    "c-0.34 -0.06 -1.26 -0.09 -1.76 -0.09 "
    "-4.82 -0.24 -9.23 -0.5 -13.92 -0.91 "
    "-0.89 -17.55 -0.49 -34.58,2.0 -51.04,"
    "4.0 -27.80,14.52 -53.5,37.30 -77.03,"
    "24.33 -25.45,50.19 -38.68,76.67 -45.22,"
    "21.48 -5.08,42.97 -6.38,64.24 -6.15,"
    "0.30,5.16,0.39,10.04,0.85,15.48,"
    "0.24,3.29,0.48,6.48,0.40,9.90 "
    "-16.60 -0.4 -32.63,0.02 -48.47,2.88l52.56,52.28 "
    "-5.01,23.03 -70.13 -70.27c-13.80,5.0 -27.71,12.03 "
    "-41.15,22.79l96.73,96.68c18.35 -19.50,26.24 -40.19,"
    "29.97 -63.19,3.63 -24.01,1.9 -49.29 -0.04 -76.14 "
    "-2.01 -30.38 -3.53 -55.32,0.2 -78.8,"
    "3.78 -24.42,12.55 -47.56,31.16 -72.06l20.94,15.5"
    "c-15.40,21.4 -23.29,40.44 -26.35,60.66 "
    "-3.53,21.14 -2.12,44.77,0.02,73.46,"
    "1.96,28.58,4.14,55.92 -0.16,82.05 "
    "-4.35,27.34 -14.75,53.26 -37.18,77.54 "
    "-24.01,25.45 -50.03,38.97 -77.19,45.56 "
    "-26.03,6.71 -52.65,6.54 -79.53,5.60 "
    "-6.39 -0.27 -11.3 -0.25 -16.66 -0.86 "
    "-27.79 -1.31 -56.29 -3.11 -82.93,2.98z"
    "m362.70 -226.1l-55.44 -55.35,4.95 -23.21,72.70,72.45"
    "c10.58 -3.76,21.54 -9.64,31.86 -17.39l15.83,20.65"
    "c-38.96,29.25 -80.04,33.10 -121.50,32.09 "
    "-0.35 -5.60 -0.96 -10.87 -1.21 -16.76 "
    "-0.22 -3.04 -0.43 -5.88 -0.67 -8.85,"
    "18.39,0.52,36.60,0.04,53.91 -3.45z"
)


def load_gcp_icon() -> str:
    """Load Google Cloud icon SVG and return inner path elements."""
    tree = ET.parse(GCP_ICON_FILE)
    root = tree.getroot()
    paths = []
    for el in root.iter("{http://www.w3.org/2000/svg}path"):
        fill = el.get("fill", "")
        d = el.get("d", "")
        paths.append(f'<path fill="{fill}" d="{d}"/>')
    return "\n    ".join(paths)


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
    path_el = root.find("{http://www.w3.org/2000/svg}path")
    if path_el is None:
        # Fallback: iterate children
        for child in root:
            if child.tag.endswith("}path") or child.tag == "path":
                path_el = child
                break
    return path_el.get("d", "")


def build_sticker(qr_path_data: str, gcp_icon: str) -> str:
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
  <circle cx="150" cy="150" r="148" fill="white"/>

  <!-- Google 4-color border ring (blue=right, green=bottom, yellow=left, red=top) -->
  <path d="M254.65,45.35 A148,148 0 0,1 254.65,254.65" fill="none" stroke="{GOOGLE_BLUE}" stroke-width="4"/>
  <path d="M254.65,254.65 A148,148 0 0,1 45.35,254.65" fill="none" stroke="{GOOGLE_GREEN}" stroke-width="4"/>
  <path d="M45.35,254.65 A148,148 0 0,1 45.35,45.35" fill="none" stroke="{GOOGLE_YELLOW}" stroke-width="4"/>
  <path d="M45.35,45.35 A148,148 0 0,1 254.65,45.35" fill="none" stroke="{GOOGLE_RED}" stroke-width="4"/>

  <!-- DNA helix icon in Google colors (centered, upper portion) -->
  <g transform="translate(115, 20) scale(0.14)">
    <path fill="url(#google-grad)" fill-rule="nonzero" d="{HELIX_PATH}"/>
  </g>

  <!-- Title text matching Cloud Run website typography -->
  <text x="150" y="120" text-anchor="middle" font-family="{FONT_FAMILY_DISPLAY}" font-size="36" font-weight="500" fill="{TEXT_PRIMARY}" letter-spacing="-0.3">{TITLE}</text>

  <!-- Tagline with inline Google Cloud icon replacing "GCP" -->
  <text x="137" y="143" text-anchor="middle" font-family="{FONT_FAMILY}" font-size="11" fill="{TEXT_SECONDARY}">{TAGLINE}</text>
  <g transform="translate(205, 126) scale(0.19)">
    {gcp_icon}
  </g>

  <!-- QR Code (scaled and centered at bottom) -->
  <g transform="translate(97, 155) scale(3.636)">
    <path d="{qr_path_data}" fill="{TEXT_PRIMARY}" fill-rule="nonzero" stroke="none"/>
  </g>

  <!-- Scan label -->
  <text x="150" y="280" text-anchor="middle" font-family="{FONT_FAMILY}" font-size="9" fill="{TEXT_SECONDARY}">{SCAN_LABEL}</text>
</svg>
"""


def build_helix() -> str:
    """Build standalone DNA helix SVG."""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 514 513" width="512" height="512">
  <defs>
    <linearGradient id="google-grad" x1="0%" y1="0%" x2="0%" y2="100%">
      <stop offset="0%" stop-color="{GOOGLE_BLUE}"/>
      <stop offset="33%" stop-color="{GOOGLE_RED}"/>
      <stop offset="66%" stop-color="{GOOGLE_YELLOW}"/>
      <stop offset="100%" stop-color="{GOOGLE_GREEN}"/>
    </linearGradient>
  </defs>
  <path fill="url(#google-grad)" fill-rule="nonzero" d="{HELIX_PATH}"/>
</svg>
"""


def build_icon() -> str:
    """Build helix + FoldRun wordmark SVG."""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 280 40" width="280" height="40">
  <!-- DNA helix icon in Google colors -->
  <defs>
    <linearGradient id="google-grad" x1="0%" y1="0%" x2="0%" y2="100%">
      <stop offset="0%" stop-color="{GOOGLE_BLUE}"/>
      <stop offset="33%" stop-color="{GOOGLE_RED}"/>
      <stop offset="66%" stop-color="{GOOGLE_YELLOW}"/>
      <stop offset="100%" stop-color="{GOOGLE_GREEN}"/>
    </linearGradient>
  </defs>
  <g transform="translate(2, 2) scale(0.07)">
    <path fill="url(#google-grad)" fill-rule="nonzero" d="{HELIX_PATH}"/>
  </g>
  <!-- "FoldRun" text matching Cloud Run website typography -->
  <text x="48" y="29" font-family="{FONT_FAMILY_DISPLAY}" font-size="24" font-weight="400" fill="{TEXT_PRIMARY}" letter-spacing="-0.2">{TITLE}</text>
</svg>
"""


def main():
    qr_path_data = generate_qr_path(DEMO_URL)
    gcp_icon = load_gcp_icon()
    sticker_svg = build_sticker(qr_path_data, gcp_icon)

    with open(OUTPUT_STICKER, "w") as f:
        f.write(sticker_svg)
    print(f"Sticker saved to {OUTPUT_STICKER}")

    helix_svg = build_helix()
    with open(OUTPUT_HELIX, "w") as f:
        f.write(helix_svg)
    print(f"Helix saved to {OUTPUT_HELIX}")

    icon_svg = build_icon()
    with open(OUTPUT_ICON, "w") as f:
        f.write(icon_svg)
    print(f"Icon saved to {OUTPUT_ICON}")

    print(f"\nConfiguration:")
    print(f"  Title:   {TITLE}")
    print(f"  Tagline: {TAGLINE}")
    print(f"  QR URL:  {DEMO_URL}")


if __name__ == "__main__":
    main()
