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
    """Ensures Chromium binaries are present."""
    try:
        subprocess.run(["playwright", "install", "chromium"], check=True)
    except Exception as e:
        st.error(f"Setup Error: {e}")

install_browser_binaries()

# --- 2. CONFIGURATION ---
cloudinary.config(
    cloud_name = st.secrets["CLOUDINARY_CLOUD_NAME"],
    api_key = st.secrets["CLOUDINARY_API_KEY"],
    api_secret = st.secrets["CLOUDINARY_API_SECRET"],
    secure = True
)

# --- 3. CORE LOGIC ---

def capture_regional_images(target_url):
    regions = ["Asia", "Europe", "LATAM", "Canada", "All Regions"]
    captured_data = []
    capture_date = datetime.now().strftime("%Y-%m-%d")
    header_title = "Consolidated Report"

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        # Increased viewport width to 1800 to ensure wide interfaces have room to render fully
        context = browser.new_context(viewport={'width': 1800, 'height': 3500})
        page = context.new_page()
        
        st.info("🔗 Connecting to Airtable Interface...")
        page.goto(target_url, wait_until="networkidle")
        
        # --- REMOVE COOKIES & BANNERS ---
        page.evaluate("""
            () => {
                const removeSelectors = [
                    '#onetrust-banner-sdk', 
                    '.onetrust-pc-dark-filter',
                    '[id*="cookie"]', 
                    '[class*="cookie"]',
                    '.banner-content'
                ];
                removeSelectors.forEach(selector => {
                    const el = document.querySelector(selector);
                    if (el) el.remove();
                });
            }
        """)
        page.wait_for_timeout(1000)

        # Header Extraction
        try:
            header_selector = 'h2.font-family-display-updated, h1, .interfaceTitle'
            header_locator = page.locator(header_selector).first
            header_locator.wait_for(state="visible", timeout=10000)
            raw_header = header_locator.inner_text()
            header_title = raw_header.split("|")[0].strip() if "|" in raw_header else raw_header.strip()
        except Exception:
            st.warning("Header load timed out.")

        for region in regions:
            status_placeholder = st.empty()
            status_placeholder.write(f"🔄 **{region}**: Capturing Summary & Galleries...")
            
            try:
                # 1. Navigate to Tab
                tab_selector = page.locator(f'div[role="tab"]:has-text("{region}")')
                tab_selector.wait_for(state="visible", timeout=5000)
                tab_selector.click()
                page.wait_for_timeout(4000) 

                # 2. Main Summary Capture
                clip_height = 2420 
                dynamic_js = """
                () => {
                    const headers = Array.from(document.querySelectorAll('h1, h2, h3, h4, div'));
                    const targetHeader = headers.find(h => 
                        h.innerText && h.innerText.trim().toLowerCase() === 'in progress'
                    );
                    if (!targetHeader) return null;
                    let container = targetHeader.closest('[role="region"]') || targetHeader.closest('.interfaceControl') || targetHeader.parentElement;
                    const boxes = Array.from(container.querySelectorAll('.summaryCard, [class*="record"], [class*="Cell"], [role="button"]'));
                    if (boxes.length > 0) {
                        const bottoms = boxes.map(b => b.getBoundingClientRect().bottom + window.scrollY);
                        return Math.max(...bottoms) + 40;
                    }
                    return targetHeader.getBoundingClientRect().bottom + window.scrollY + 400;
                }
                """
                calculated_height = page.evaluate(dynamic_js)
                if calculated_height and calculated_height > 100:
                    clip_height = min(int(calculated_height), 3400) 
                
                main_filename = f"{region.lower().replace(' ', '')}_main.png"
                # Set width to 1550 for the main interface capture to prevent right-side clipping
                page.screenshot(path=main_filename, clip={'x': 0, 'y': 0, 'width': 1550, 'height': clip_height})

                upload_res = cloudinary.uploader.upload(
                    main_filename, 
                    folder="airtableautomation",
                    public_id=f"{region.lower().replace(' ', '')}_main_{capture_date.replace('-', '')}"
                )
                
                region_entry = {
                    "region": region,
                    "url": upload_res["secure_url"],
                    "local_file": main_filename,
                    "date": capture_date,
                    "header_id": header_title,
                    "height": clip_height,
                    "galleries": [] 
                }

                # 3. CONFINED GALLERY CAPTURE LOGIC
                if region != "All Regions":
                    gallery_count = 1
                    # Flag to track if we've handled the "stop capturing first page" requirement
                    page_one_skipped = False
                    
                    while True:
                        # Find the gallery container
                        container_js = """
                        () => {
                            let el = document.querySelector('[aria-label="Completed Request Gallery gallery"]');
                            if (!el) {
                                const h2s = Array.from(document.querySelectorAll('h2'));
                                const galHeader = h2s.find(h => h.innerText && h.innerText.trim() === 'Completed Request Gallery');
                                if (galHeader) el = galHeader.closest('.width-full.rounded-big') || galHeader.parentElement.parentElement;
                            }
                            if (!el) return null;
                            const rect = el.getBoundingClientRect();
                            return {
                                x: Math.floor(rect.left),
                                y: Math.floor(rect.top + window.scrollY),
                                width: Math.floor(rect.width),
                                height: Math.floor(rect.height)
                            };
                        }
                        """
                        gal_info = page.evaluate(container_js)
                        
                        if not gal_info:
                            status_placeholder.write(f"⚠️ **{region}**: Gallery container not found.")
                            break

                        # Logic to skip capturing the first page
                        if not page_one_skipped:
                            status_placeholder.write(f"⏭️ **{region}**: Skipping first gallery page...")
                        else:
                            status_placeholder.write(f"🔄 **{region}**: Gallery Page {gallery_count}...")
                            
                            # Move viewport to gallery box to ensure visibility
                            page.mouse.wheel(0, gal_info['y'] - 100)
                            page.wait_for_timeout(1000)

                            gal_filename = f"{region.lower().replace(' ', '')}_gal_{gallery_count}.png"
                            page.screenshot(path=gal_filename, clip=gal_info)
                            
                            gal_upload = cloudinary.uploader.upload(
                                gal_filename,
                                folder="airtableautomation",
                                public_id=f"{region.lower().replace(' ', '')}_gal{gallery_count}_{capture_date.replace('-', '')}"
                            )
                            
                            region_entry["galleries"].append({
                                "local": gal_filename,
                                "url": gal_upload["secure_url"]
                            })
                            gallery_count += 1

                        # Pagination Logic
                        next_btn = page.locator('[aria-label*="Completed Request Gallery"] div[role="button"]:has(path[d*="m4.64.17"])').first
                        
                        is_visible = next_btn.is_visible()
                        if is_visible:
                            is_disabled = next_btn.evaluate("el => el.getAttribute('aria-disabled') === 'true' || window.getComputedStyle(el).opacity === '0.5'")
                            if not is_disabled:
                                next_btn.click()
                                page_one_skipped = True # We have now moved past the first page
                                page.wait_for_timeout(4000) 
                            else:
                                break
                        else:
                            break
                            
                        if gallery_count > 15: break 

                captured_data.append(region_entry)
                status_placeholder.write(f"✅ **{region}** captured.")
                
            except Exception as e:
                st.error(f"Error on {region}: {e}")

        browser.close()
    return captured_data

def sync_to_airtable(data_list):
    url = f"https://api.airtable.com/v0/{st.secrets['BASE_ID']}/{st.secrets['TABLE_NAME']}"
    headers = {
        "Authorization": f"Bearer {st.secrets['AIRTABLE_TOKEN']}",
        "Content-Type": "application/json"
    }
    
    if not data_list: return None

    def get_url(region_name):
        for item in data_list:
            if item["region"] == region_name:
                return item["url"]
        return ""

    all_attachments = []
    for item in data_list:
        all_attachments.append({"url": item["url"]})
        for gal in item.get("galleries", []):
            all_attachments.append({"url": gal["url"]})
    
    fields = {
        "Type": data_list[0].get("header_id", "Consolidated Report"), 
        "Date": data_list[0]["date"],
        "Attachments": all_attachments,
        "Cloud ID 1": get_url("All Regions"),
        "Cloud ID 2": get_url("Asia"),
        "Cloud ID 3": get_url("Europe"),
        "Cloud ID 4": get_url("LATAM"),
        "Cloud ID 5": get_url("Canada")
    }
    
    payload = {"records": [{"fields": fields}]}

    response = requests.post(url, headers=headers, json=payload)
    if response.status_code == 200:
        st.success(f"🎉 Record created with {len(all_attachments)} images!")
    else:
        st.error(f"❌ Sync Error: {response.text}")
    return response.json()

# --- 4. USER INTERFACE ---

st.set_page_config(page_title="Airtable Bi-Weekly Report Capture", layout="wide")
st.title("🗺️ Bi-Weekly Report Capture")

url_input = st.text_input(
    "Airtable Interface URL",
    value="https://airtable.com/appyOEewUQye37FCb/shr9NiIaM2jisKHiK?tTPqb=sfsTkRwjWXEAjyRGj",
    key="fixed_url_input_v4"
)

if st.button("🚀 Run Capture", key="fixed_run_btn_v4"):
    if url_input:
        results = capture_regional_images(url_input)
        if results:
            sync_to_airtable(results)
            st.divider()
            cols = st.columns(len(results))
            for idx, item in enumerate(results):
                with cols[idx]:
                    st.subheader(item["region"])
                    st.image(item["local_file"])
                    for gal in item.get("galleries", []):
                        st.image(gal["local"])
