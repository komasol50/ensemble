"""幅360pxの画面写真を撮り、横スクロールが無いことを確かめる。

使い方: python -X utf8 scripts/screenshot.py <URL> <出力.png>
"""

import sys

from playwright.sync_api import sync_playwright


def main(url: str, out: str) -> int:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 360, "height": 740}, device_scale_factor=2)
        page.goto(url, wait_until="networkidle")
        page.screenshot(path=out, full_page=True)
        scroll_w, client_w = page.evaluate(
            "[document.documentElement.scrollWidth, document.documentElement.clientWidth]"
        )
        browser.close()
    print(f"scrollWidth={scroll_w} clientWidth={client_w}")
    if scroll_w > client_w:
        print("NG: 横スクロールがあります")
        return 1
    print("OK: 横スクロールなし")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
