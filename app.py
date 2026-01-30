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
        # Sufficiently wide viewport to allow Airtable to render all columns
        context = browser.new_context(viewport={'width': 1920, 'height': 3500})
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

                # 2. Dynamic Width Detection
                detected_width = page.evaluate("""
                () => {
                    const el = document.querySelector('[aria-label="Completed Request Gallery gallery"]') || 
                               document.querySelector('.width-full.rounded-big') ||
                               document.querySelector('[role="main"]');
                    return el ? el.getBoundingClientRect().width : 1600;
                }
                """)
                capture_width = int(detected_width) + 40

                # 3. Main Summary Capture
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
                
                # OPTIMIZATION: Lower JPEG quality (55) for strict file size target (<500KB)
                safe_region = region.lower().replace(' ', '-')
                main_filename = f"{safe_region}-main.jpg"
                
                page.screenshot(
                    path=main_filename, 
                    clip={'x': 0, 'y': 0, 'width': capture_width, 'height': clip_height},
                    type="jpeg",
                    quality=80
                )

                # Cloudinary upload with ECO optimization
                safe_date = capture_date.replace('-', '')
                upload_res = cloudinary.uploader.upload(
                    main_filename, 
                    folder="airtableautomation",
                    public_id=f"{safe_region}-main-{safe_date}",
                    fetch_format="auto",
                    quality="auto:eco"
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

                # 4. CONFINED GALLERY CAPTURE LOGIC
                if region != "All Regions":
                    gallery_count = 1
                    page_one_skipped = False
                    
                    while True:
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

                        if not page_one_skipped:
                            status_placeholder.write(f"⏭️ **{region}**: Skipping first gallery page...")
                        else:
                            status_placeholder.write(f"🔄 **{region}**: Gallery Page {gallery_count}...")
                            
                            page.mouse.wheel(0, gal_info['y'] - 100)
                            page.wait_for_timeout(1000)

                            gal_filename = f"{safe_region}-gal-{gallery_count}.jpg"
                            page.screenshot(
                                path=gal_filename, 
                                clip=gal_info,
                                type="jpeg",
                                quality=55
                            )
                            
                            gal_upload = cloudinary.uploader.upload(
                                gal_filename,
                                folder="airtableautomation",
                                public_id=f"{safe_region}-gal{gallery_count}-{safe_date}",
                                fetch_format="auto",
                                quality="auto:eco"
                            )
                            
                            region_entry["galleries"].append({
                                "local": gal_filename,
                                "url": gal_upload["secure_url"]
                            })
                            gallery_count += 1

                        next_btn = page.locator('[aria-label*="Completed Request Gallery"] div[role="button"]:has(path[d*="m4.64.17"])').first
                        
                        is_visible = next_btn.is_visible()
                        if is_visible:
                            is_disabled = next_btn.evaluate("el => el.getAttribute('aria-disabled') === 'true' || window.getComputedStyle(el).opacity === '0.5'")
                            if not is_disabled:
                                next_btn.click()
                                page_one_skipped = True 
                                page.wait_for_timeout(4000) 
                            else:
                                break
                        else:
                            break
                            
                        if gallery_count > 3: break 

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

    records_to_create = []
    
    for item in data_list:
        base_type = item.get("header_id", "Consolidated Report")
        record_type = f"{base_type} | {item['region']}"
        
        # Attachments array for file fields (Assuming "Attachments" is an actual Attachment field)
        record_attachments = [{"url": item["url"]}]
        for gal in item.get("galleries", []):
            record_attachments.append({"url": gal["url"]})
            
        fields = {
            "Type": record_type,
            "Date": item["date"],
            "Attachments": record_attachments,
            # Cloud ID is a Single Line Text field (Simple string URL)
            "Cloud ID": item["url"]
        }
        
        # Gallery 1, 2, 3 are Single Line Text fields
        gallery_items = item.get("galleries", [])
        for i in range(1, 4):
            field_name = f"Gallery {i}"
            if len(gallery_items) >= i:
                # SEND AS STRING URL, NOT OBJECT ARRAY
                fields[field_name] = gallery_items[i-1]["url"]
        
        records_to_create.append({"fields": fields})

    payload = {"records": records_to_create}

    response = requests.post(url, headers=headers, json=payload)
    if response.status_code == 200:
        st.success(f"🎉 Successfully created {len(records_to_create)} individual regional records!")
        st.session_state.capture_results = None
    else:
        st.error(f"❌ Sync Error: {response.text}")
    return response.json()

# --- 4. USER INTERFACE ---

st.set_page_config(page_title="Airtable Bi-Weekly Report Capture", layout="wide")
st.title("🗺️ Bi-Weekly Report Capture")

if 'capture_results' not in st.session_state:
    st.session_state.capture_results = None

url_input = st.text_input(
    "Airtable Interface URL",
    value="https://airtable.com/appyOEewUQye37FCb/shr9NiIaM2jisKHiK?tTPqb=sfsTkRwjWXEAjyRGj",
    key="fixed_url_input_v17"
)

col1, col2 = st.columns([1, 4])

with col1:
    if st.button("🚀 Run Capture", key="run_capture_btn"):
        if url_input:
            results = capture_regional_images(url_input)
            st.session_state.capture_results = results

with col2:
    if st.session_state.capture_results:
        if st.button("📤 Upload to Airtable (5 Records)", key="upload_airtable_btn", type="primary"):
            sync_to_airtable(st.session_state.capture_results)

if st.session_state.capture_results:
    st.divider()
    st.info("👀 Previewing Captured Images below. Review then click 'Upload to Airtable' above.")
    cols = st.columns(len(st.session_state.capture_results))
    for idx, item in enumerate(st.session_state.capture_results):
        with cols[idx]:
            st.subheader(item["region"])
            st.image(item["local_file"], use_container_width=True)
            for gal in item.get("galleries", []):
                st.image(gal["local"], caption=f"Gallery Image", use_container_width=True)
