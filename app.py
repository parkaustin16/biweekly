# ================================
# REFACTORED AIRTABLE CAPTURE SCRIPT
# Main Summary (trimmed) + Completed Gallery + In Progress
# WITH ORIGINAL AIRTABLE PAYLOAD + LOCAL PREVIEWS RESTORED
# ================================

import streamlit as st
import subprocess
import os
import requests
import cloudinary
import cloudinary.uploader
from datetime import datetime
from playwright.sync_api import sync_playwright

# --- 1. CLOUD ENVIRONMENT SETUP ---
@st.cache_resource
def install_browser_binaries():
    try:
        subprocess.run(["playwright", "install", "chromium"], check=True)
    except Exception as e:
        st.error(f"Setup Error: {e}")

install_browser_binaries()

# --- 2. CONFIGURATION ---
cloudinary.config(
    cloud_name=st.secrets["CLOUDINARY_CLOUD_NAME"],
    api_key=st.secrets["CLOUDINARY_API_KEY"],
    api_secret=st.secrets["CLOUDINARY_API_SECRET"],
    secure=True
)

# --- 3. HELPERS ---

def capture_paginated_section(page, container_locator, next_button_locator, prefix, quality=55):
    images = []
    page_index = 1

    while True:
        box = container_locator.bounding_box()
        if not box:
            break

        filename = f"{prefix}-{page_index}.jpg"
        page.screenshot(
            path=filename,
            clip={
                'x': box['x'],
                'y': box['y'] + page.evaluate("window.scrollY"),
                'width': box['width'],
                'height': box['height']
            },
            type="jpeg",
            quality=quality
        )

        upload = cloudinary.uploader.upload(
            filename,
            folder="airtableautomation",
            fetch_format="auto",
            quality="auto:eco"
        )

        images.append({"local": filename, "url": upload["secure_url"]})
        page_index += 1

        if not next_button_locator.is_visible():
            break

        disabled = next_button_locator.evaluate(
            "el => el.getAttribute('aria-disabled') === 'true' || getComputedStyle(el).opacity === '0.5'"
        )
        if disabled:
            break

        next_button_locator.click()
        page.wait_for_timeout(3500)

    return images

# --- 4. CORE LOGIC ---

def capture_regional_images(target_url):
    regions = ["Asia", "Europe", "LATAM", "Canada", "All Regions"]
    results = []
    capture_date = datetime.now().strftime("%Y-%m-%d")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={'width': 1920, 'height': 3500})
        page = context.new_page()

        page.goto(target_url, wait_until="networkidle")
        page.wait_for_timeout(2000)

        # Header extraction (RESTORED)
        try:
            header_locator = page.locator('h2.font-family-display-updated, h1, .interfaceTitle').first
            header_locator.wait_for(state="visible", timeout=10000)
            raw_header = header_locator.inner_text()
            header_title = raw_header.split("|")[0].strip()
        except Exception:
            header_title = "Consolidated Report"

        for region in regions:
            safe_region = region.lower().replace(" ", "-")

            entry = {
                "region": region,
                "date": capture_date,
                "header_id": header_title,
                "url": None,
                "local_file": None,
                "galleries": []
            }

            # --- SWITCH TAB ---
            page.locator(f'div[role="tab"]:has-text("{region}")').click()
            page.wait_for_timeout(4000)

            # --- MAIN SUMMARY (STOP AT MASTER BANNER) ---
            banner = page.locator('text=Master Banner Usage Breakdown').first
            banner.wait_for(state="visible", timeout=10000)
            banner_y = banner.bounding_box()['y']

            main_file = f"{safe_region}-main.jpg"
            page.screenshot(
                path=main_file,
                clip={'x': 0, 'y': 0, 'width': 1920, 'height': int(banner_y + 120)},
                type="jpeg",
                quality=80
            )

            main_upload = cloudinary.uploader.upload(
                main_file,
                folder="airtableautomation",
                public_id=f"{safe_region}-main-{capture_date.replace('-', '')}",
                fetch_format="auto",
                quality="auto:eco"
            )

            entry["url"] = main_upload["secure_url"]
            entry["local_file"] = main_file

            # --- COMPLETED REQUEST GALLERY ---
            if region != "All Regions":
                completed_container = page.locator('[aria-label="Completed Request Gallery gallery"]').first
                completed_container.scroll_into_view_if_needed()
                page.wait_for_timeout(1000)

                completed_next = completed_container.locator('div[role="button"]:has(path)').last

                completed_images = capture_paginated_section(
                    page,
                    completed_container,
                    completed_next,
                    f"{safe_region}-gal"
                )

                entry["galleries"].extend(completed_images)

            # --- IN PROGRESS ---
            in_prog_header = page.locator('text=In Progress').first
            in_prog_container = in_prog_header.locator('xpath=ancestor::*[@role="region"]').first
            in_prog_container.scroll_into_view_if_needed()
            page.wait_for_timeout(1000)

            in_prog_next = in_prog_container.locator('div[role="button"]:has(path)').last

            in_prog_images = capture_paginated_section(
                page,
                in_prog_container,
                in_prog_next,
                f"{safe_region}-inprogress"
            )

            entry["galleries"].extend(in_prog_images)

            results.append(entry)

        browser.close()

    return results

# --- 5. AIRTABLE SYNC (UNCHANGED STRUCTURE) ---

def sync_to_airtable(data_list):
    url = f"https://api.airtable.com/v0/{st.secrets['BASE_ID']}/{st.secrets['TABLE_NAME']}"
    headers = {
        "Authorization": f"Bearer {st.secrets['AIRTABLE_TOKEN']}",
        "Content-Type": "application/json"
    }

    records = []

    for item in data_list:
        record_type = f"{item['header_id']} | {item['region']}"

        attachments = [{"url": item["url"]}]
        for g in item.get("galleries", []):
            attachments.append({"url": g["url"]})

        fields = {
            "Type": record_type,
            "Date": item["date"],
            "Attachments": attachments,
            "Cloud ID": item["url"]
        }

        for i in range(1, 4):
            if len(item["galleries"]) >= i:
                fields[f"Gallery {i}"] = item["galleries"][i-1]["url"]

        records.append({"fields": fields})

    payload = {"records": records}
    response = requests.post(url, headers=headers, json=payload)

    if response.status_code == 200:
        st.success(f"🎉 Successfully created {len(records)} records")
    else:
        st.error(response.text)

# --- 6. STREAMLIT UI ---

st.set_page_config(page_title="Airtable Bi-Weekly Report Capture", layout="wide")
st.title("🗺️ Bi-Weekly Report Capture")

if 'capture_results' not in st.session_state:
    st.session_state.capture_results = None

url_input = st.text_input("Airtable Interface URL")

if st.button("🚀 Run Capture"):
    if url_input:
        st.session_state.capture_results = capture_regional_images(url_input)

if st.session_state.capture_results:
    st.divider()
    st.info("👀 Previewing captured images")

    cols = st.columns(len(st.session_state.capture_results))
    for idx, item in enumerate(st.session_state.capture_results):
        with cols[idx]:
            st.subheader(item["region"])
            st.image(item["local_file"], use_container_width=True)
            for gal in item.get("galleries", []):
                st.image(gal["local"], use_container_width=True)

    if st.button("📤 Upload to Airtable", type="primary"):
        sync_to_airtable(st.session_state.capture_results)
