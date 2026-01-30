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
        context = browser.new_context(viewport={'width': 1920, 'height': 3500})
        page = context.new_page()
        
        st.info("🔗 Connecting to Airtable Interface...")
        page.goto(target_url, wait_until="networkidle")
        
        # --- CLEANUP: REMOVE COOKIES & GLOBAL OVERLAYS ---
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
            status_placeholder.write(f"🔄 **{region}**: Processing...")
            
            try:
                # 1. Navigate to Tab
                tab_selector = page.locator(f'div[role="tab"]:has-text("{region}")')
                tab_selector.wait_for(state="visible", timeout=5000)
                tab_selector.click()
                page.wait_for_timeout(4000) 

                # 2. HIDE GALLERIES FOR SUMMARY CAPTURE
                page.evaluate("""
                    () => {
                        const hideElements = (labelText) => {
                            const el = document.querySelector(`[aria-label*="${labelText}"]`);
                            if (el) {
                                el.style.display = 'none';
                                const container = el.closest('.width-full.rounded-big');
                                if (container) container.style.display = 'none';
                            }
                        };
                        hideElements("Completed Request Gallery");
                        hideElements("In Progress");
                    }
                """)

                # 3. Dynamic Height Calculation for "Master Banner Usage Breakdown"
                dynamic_js = """
                () => {
                    const headers = Array.from(document.querySelectorAll('h1, h2, h3, h4, div'));
                    const targetHeader = headers.find(h => 
                        h.innerText && h.innerText.trim().toLowerCase() === 'master banner usage breakdown'
                    );
                    
                    if (!targetHeader) return 2200;

                    let container = targetHeader.closest('[role="region"]') || targetHeader.closest('.interfaceControl') || targetHeader.parentElement;
                    const boxes = Array.from(container.querySelectorAll('.summaryCard, [class*="record"], [class*="Cell"], [role="button"], [class*="grid"], [class*="chart"]'));
                    if (boxes.length > 0) {
                        const bottoms = boxes.map(b => b.getBoundingClientRect().bottom + window.scrollY);
                        return Math.max(...bottoms) + 80;
                    }
                    return targetHeader.getBoundingClientRect().bottom + window.scrollY + 500;
                }
                """
                calculated_height = page.evaluate(dynamic_js)
                clip_height = min(int(calculated_height), 3400) if (calculated_height and calculated_height > 100) else 2000

                # 4. Main Summary Capture
                safe_region = region.lower().replace(' ', '-')
                main_filename = f"{safe_region}-main.jpg"
                page.screenshot(
                    path=main_filename, 
                    clip={'x': 0, 'y': 0, 'width': 1100, 'height': clip_height},
                    type="jpeg",
                    quality=85
                )

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
                    "in_progress_pages": [],
                    "completed_gallery_pages": [] 
                }

                # Helper to capture paged galleries
                def capture_paged_gallery(gallery_label, storage_key):
                    # Show the gallery first
                    page.evaluate(f"""
                        () => {{
                            const el = document.querySelector('[aria-label*="{gallery_label}"]');
                            if (el) {{
                                el.style.display = 'block';
                                const container = el.closest('.width-full.rounded-big');
                                if (container) container.style.display = 'block';
                            }}
                        }}
                    """)
                    
                    page_idx = 1
                    while True:
                        container_js = f"""
                        () => {{
                            const el = document.querySelector('[aria-label*="{gallery_label}"]');
                            if (!el) return null;
                            const rect = el.getBoundingClientRect();
                            return {{
                                x: Math.floor(rect.left),
                                y: Math.floor(rect.top + window.scrollY),
                                width: Math.floor(rect.width),
                                height: Math.floor(rect.height)
                            }};
                        }}
                        """
                        gal_info = page.evaluate(container_js)
                        if not gal_info: break

                        status_placeholder.write(f"📸 **{region}**: {gallery_label} Page {page_idx}...")
                        page.mouse.wheel(0, gal_info['y'] - 100)
                        page.wait_for_timeout(1000)

                        safe_label = gallery_label.lower().replace(' ', '-')
                        gal_filename = f"{safe_region}-{safe_label}-{page_idx}.jpg"
                        page.screenshot(
                            path=gal_filename, 
                            clip=gal_info,
                            type="jpeg",
                            quality=65
                        )
                        
                        gal_upload = cloudinary.uploader.upload(
                            gal_filename,
                            folder="airtableautomation",
                            public_id=f"{safe_region}-{safe_label}{page_idx}-{safe_date}",
                            fetch_format="auto",
                            quality="auto:eco"
                        )

                        region_entry[storage_key].append({
                            "local": gal_filename,
                            "url": gal_upload["secure_url"]
                        })

                        # Pagination Logic
                        next_btn = page.locator(f'[aria-label*="{gallery_label}"] div[role="button"]:has(path[d*="m4.64.17"])').first
                        if next_btn.is_visible():
                            is_disabled = next_btn.evaluate("el => el.getAttribute('aria-disabled') === 'true' || window.getComputedStyle(el).opacity === '0.5'")
                            if not is_disabled:
                                next_btn.click()
                                page_idx += 1
                                page.wait_for_timeout(4000) 
                            else: break
                        else: break
                        if page_idx > 5: break

                # 5. Capture "In Progress" Gallery
                if region != "All Regions":
                    capture_paged_gallery("In Progress", "in_progress_pages")
                    capture_paged_gallery("Completed Request Gallery", "completed_gallery_pages")

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
        
        record_attachments = [{"url": item["url"]}]
        for page in item.get("in_progress_pages", []):
            record_attachments.append({"url": page["url"]})
        for page in item.get("completed_gallery_pages", []):
            record_attachments.append({"url": page["url"]})
            
        fields = {
            "Type": record_type,
            "Date": item["date"],
            "Attachments": record_attachments,
            "Cloud ID": item["url"]
        }
        
        # Flatten all gallery pages for the Gallery 1-3 fields if needed
        all_gal_urls = [p["url"] for p in item.get("in_progress_pages", [])] + \
                       [p["url"] for p in item.get("completed_gallery_pages", [])]

        for i in range(1, 4):
            if len(all_gal_urls) >= i:
                fields[f"Gallery {i}"] = all_gal_urls[i-1]
        
        records_to_create.append({"fields": fields})

    payload = {"records": records_to_create}
    response = requests.post(url, headers=headers, json=payload)
    if response.status_code == 200:
        st.success(f"🎉 Successfully created {len(records_to_create)} individual regional records!")
        st.session_state.capture_results = None
    else:
        st.error(f"❌ Sync Error: {response.text}")

# --- 4. USER INTERFACE ---

st.set_page_config(page_title="Airtable Bi-Weekly Report Capture", layout="wide")
st.title("🗺️ Bi-Weekly Report Capture")

if 'capture_results' not in st.session_state:
    st.session_state.capture_results = None

url_input = st.text_input("Airtable Interface URL", value="https://airtable.com/appyOEewUQye37FCb/shr9NiIaM2jisKHiK?tTPqb=sfsTkRwjWXEAjyRGj")

col1, col2 = st.columns([1, 4])
with col1:
    if st.button("🚀 Run Capture"):
        if url_input:
            results = capture_regional_images(url_input)
            st.session_state.capture_results = results

with col2:
    if st.session_state.capture_results:
        if st.button("📤 Upload to Airtable", type="primary"):
            sync_to_airtable(st.session_state.capture_results)

if st.session_state.capture_results:
    st.divider()
    st.info("👀 Reviewing Captured Images. Summary -> In Progress -> Completed Gallery.")
    
    for item in st.session_state.capture_results:
        st.subheader(f"Region: {item['region']}")
        
        st.caption("Summary")
        st.image(item["local_file"], width=800)
        
        for i, page in enumerate(item.get("in_progress_pages", [])):
            st.caption(f"In Progress Page {i+1}")
            st.image(page["local"], width=800)
            
        for i, page in enumerate(item.get("completed_gallery_pages", [])):
            st.caption(f"Completed Gallery Page {i+1}")
            st.image(page["local"], width=800)
            
        st.divider()
