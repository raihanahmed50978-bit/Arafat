import asyncio
import os
import re
import json
import time
from pathlib import Path
import tkinter as tk
from tkinter import filedialog

from playwright.async_api import async_playwright
from dotenv import load_dotenv

load_dotenv()

# --- CONFIGURATION ---
PROXY_SERVER = os.getenv("PROXY_SERVER", "")
PROXY_USER   = os.getenv("PROXY_USER", "")
PROXY_PASS   = os.getenv("PROXY_PASS", "")

PAGE_LOAD_DELAY = 8000
SAMPLE_CLICK_DELAY = 0.3
MAX_RETRIES = 3
RETRY_DELAY = 2.0
TEST_MODE_2_ONLY = False

ASCII_ART = r"""
  _        _    _   _  ____   ____
 | |      / \  | \ | ||  _ \ |  _ \
 | |     / _ \ |  \| || | | || |_) |
 | |___ / ___ \| |\  || |_| ||  _ <
 |_____/_/   \_\_| \_||____/ |_| \_\
      S A M P L E   G R A B B E R
"""


# ── folder selection ─────────────────────────────────────────────────────────

def choose_download_folder() -> str:
    """
    Opens a Windows folder-selection dialog.
    Returns the selected folder path, or an empty string if cancelled.
    """

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)

    folder = filedialog.askdirectory(
        title="Select folder where LANDR samples will be downloaded"
    )

    root.destroy()

    return folder


# ── helpers ──────────────────────────────────────────────────────────────────

def sanitize(name: str) -> str:
    return re.sub(r'[\\/*?:"<>|]', "", name).strip()


def make_unique_path(folder: str, name: str, ext: str) -> str:
    base = os.path.join(folder, f"{name}{ext}")

    if not os.path.exists(base):
        return base

    counter = 1

    while True:
        candidate = os.path.join(
            folder,
            f"{name} ({counter}){ext}"
        )

        if not os.path.exists(candidate):
            return candidate

        counter += 1


def ext_from_url(url: str) -> str:
    clean = url.split("?")[0]

    if clean.endswith(".wav"):
        return ".wav"

    if clean.endswith(".flac"):
        return ".flac"

    return ".mp3"


async def get_sample_name(row) -> str:
    # Primary: span[data-original]
    # LANDR stores the real filename here.
    el = await row.query_selector("span[data-original]")

    if el:
        val = await el.get_attribute("data-original")

        if val and val.strip():
            return sanitize(val.strip())

    # Fallback selectors
    for selector in [
        'span[class*="Text"]',
        'span[class*="w_full"]',
        'span[class*="bold"]'
    ]:
        el = await row.query_selector(selector)

        if el:
            text = (await el.inner_text()).strip()

            if text and len(text) > 2 and not text.isdigit():
                return sanitize(text)

    return "unknown"


async def download_with_retry(
    page,
    audio_url: str,
    dest_path: str
) -> bool:

    for attempt in range(1, MAX_RETRIES + 1):

        try:
            resp = await page.request.get(audio_url)

            if resp.ok:
                Path(dest_path).write_bytes(
                    await resp.body()
                )
                return True

            print(
                f"    [!] HTTP {resp.status} "
                f"on attempt {attempt}"
            )

        except Exception as e:

            print(
                f"    [!] Download error "
                f"attempt {attempt}: {e}"
            )

        if attempt < MAX_RETRIES:
            await asyncio.sleep(RETRY_DELAY)

    return False


# ── main ─────────────────────────────────────────────────────────────────────

async def scrape_landr():

    print(ASCII_ART)

    # Ask user where samples should be saved.
    print()
    print("[*] Please select the folder where samples will be saved...")

    download_dir = choose_download_folder()

    if not download_dir:
        print("[!] No folder selected. Program cancelled.")
        return

    # Convert to absolute path.
    download_dir = os.path.abspath(download_dir)

    # Make sure the folder exists.
    Path(download_dir).mkdir(
        parents=True,
        exist_ok=True
    )

    print()
    print(f"[+] Download folder:")
    print(f"    {download_dir}")
    print()

    # Ask for LANDR Pack URL.
    target_url = input(
        "Enter the LANDR Pack URL: "
    ).strip()

    if not target_url:
        print("[!] URL cannot be empty.")
        return

    stats = {
        "downloaded": 0,
        "skipped": 0,
        "failed": 0
    }

    # Manifest will now be saved INSIDE the selected folder.
    manifest_path = os.path.join(
        download_dir,
        "_manifest.json"
    )

    manifest: dict = {}

    if os.path.exists(manifest_path):

        try:
            with open(
                manifest_path,
                "r",
                encoding="utf-8"
            ) as f:
                manifest = json.load(f)

        except Exception as e:

            print(
                f"[!] Could not read existing manifest: {e}"
            )

            manifest = {}

    async with async_playwright() as pw:

        proxy_config = (
            {
                "server": PROXY_SERVER,
                "username": PROXY_USER,
                "password": PROXY_PASS
            }
            if PROXY_SERVER
            else None
        )

        print(
            "[*] Launching browser"
            +
            (
                f" via proxy: {PROXY_SERVER}"
                if proxy_config
                else " (no proxy)"
            )
        )

        browser = await pw.chromium.launch(
            headless=False,
            **(
                {"proxy": proxy_config}
                if proxy_config
                else {}
            ),
        )

        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 "
                "(KHTML, like Gecko) "
                "Chrome/120.0.0.0 "
                "Safari/537.36"
            )
        )

        page = await context.new_page()

        print(
            f"[*] Navigating to: {target_url}"
        )

        await page.goto(target_url)

        print(
            f"[*] Waiting "
            f"{PAGE_LOAD_DELAY / 1000}s "
            f"for interface ..."
        )

        await page.wait_for_timeout(
            PAGE_LOAD_DELAY
        )

        # ── pagination ───────────────────────────────────────────────────────

        max_page = 1

        if not TEST_MODE_2_ONLY:

            for link in await page.query_selector_all(
                'a[href*="page="]'
            ):

                href = await link.get_attribute(
                    "href"
                )

                if not href:
                    continue

                m = re.search(
                    r"page=(\d+)",
                    href
                )

                if m:
                    max_page = max(
                        max_page,
                        int(m.group(1))
                    )

        print(
            f"[+] Pages to process: {max_page}\n"
        )

        # ── per-page loop ───────────────────────────────────────────────────

        for p_num in range(
            1,
            max_page + 1
        ):

            if (
                TEST_MODE_2_ONLY
                and stats["downloaded"] >= 2
            ):
                break

            if p_num > 1:

                curr_url = re.sub(
                    r"[?&]page=\d+",
                    "",
                    target_url
                )

                sep = (
                    "&"
                    if "?" in curr_url
                    else "?"
                )

                await page.goto(
                    f"{curr_url}"
                    f"{sep}page={p_num}"
                )

                await page.wait_for_timeout(
                    PAGE_LOAD_DELAY
                )

            rows = await page.query_selector_all(
                'div[class*="samplesMFE-Table__row"]'
            )

            if not rows:

                rows = await page.query_selector_all(
                    'div[class*="Table__row"]'
                )

            print(
                f"[*] Page {p_num}: "
                f"{len(rows)} rows found"
            )

            # ── process each sample ─────────────────────────────────────────

            for row in rows:

                if (
                    TEST_MODE_2_ONLY
                    and stats["downloaded"] >= 2
                ):
                    break

                try:

                    sample_name = await get_sample_name(
                        row
                    )

                    play_btn = await row.query_selector(
                        'button[aria-label="Play"]'
                    )

                    if not play_btn:

                        play_btn = await row.query_selector(
                            'button[aria-label*="play" i]'
                        )

                    if not play_btn:
                        continue

                    # ── catch audio URL ─────────────────────────────────────

                    audio_url = None

                    for attempt in range(
                        1,
                        MAX_RETRIES + 1
                    ):

                        try:

                            async with page.expect_response(
                                lambda r: (
                                    "assets.landr.com"
                                    in r.url
                                    or "/samples/"
                                    in r.url
                                    or r.url.endswith(".mp3")
                                    or r.url.endswith(".wav")
                                ),
                                timeout=8000,
                            ) as resp_info:

                                await play_btn.click()

                            audio_url = (
                                await resp_info.value
                            ).url

                            break

                        except Exception:

                            if attempt < MAX_RETRIES:
                                await asyncio.sleep(
                                    RETRY_DELAY
                                )

                    if not audio_url:

                        print(
                            f"    [!] No stream caught "
                            f"for: {sample_name}"
                        )

                        stats["failed"] += 1
                        continue

                    # ── create destination path ────────────────────────────

                    ext = ext_from_url(
                        audio_url
                    )

                    dest = make_unique_path(
                        download_dir,
                        sample_name,
                        ext
                    )

                    short = os.path.basename(
                        dest
                    )

                    # ── skip already downloaded sample ──────────────────────

                    if (
                        sample_name in manifest
                        and manifest[sample_name]
                        == audio_url
                    ):

                        if os.path.exists(dest):

                            print(
                                f"    [=] Skip (exists): "
                                f"{short}"
                            )

                            stats["skipped"] += 1

                            try:
                                await play_btn.click()
                            except Exception:
                                pass

                            await asyncio.sleep(
                                SAMPLE_CLICK_DELAY
                            )

                            continue

                    # ── download ───────────────────────────────────────────

                    print(
                        f"    [>] {short}"
                    )

                    ok = await download_with_retry(
                        page,
                        audio_url,
                        dest
                    )

                    if ok:

                        stats["downloaded"] += 1

                        manifest[sample_name] = (
                            audio_url
                        )

                    else:

                        print(
                            f"    [✗] Failed: "
                            f"{short}"
                        )

                        stats["failed"] += 1

                    # Stop playback.
                    try:
                        await play_btn.click()
                    except Exception:
                        pass

                    await asyncio.sleep(
                        SAMPLE_CLICK_DELAY
                    )

                except Exception as e:

                    print(
                        f"    [!] Row error: {e}"
                    )

                    continue

        await browser.close()

    # ── save manifest ────────────────────────────────────────────────────────

    try:

        with open(
            manifest_path,
            "w",
            encoding="utf-8"
        ) as f:

            json.dump(
                manifest,
                f,
                indent=2,
                ensure_ascii=False
            )

    except Exception as e:

        print(
            f"[!] Could not save manifest: {e}"
        )

    # ── final result ─────────────────────────────────────────────────────────

    print()
    print(
        "[DONE] "
        f"downloaded={stats['downloaded']}  "
        f"skipped={stats['skipped']}  "
        f"failed={stats['failed']}"
    )

    print()
    print(
        f"[*] Samples saved to:"
    )
    print(
        f"    {download_dir}"
    )

    print()
    input(
        "Press Enter to close..."
    )


# ── entry point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    asyncio.run(scrape_landr())