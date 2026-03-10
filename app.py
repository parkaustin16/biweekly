import streamlit as st
import subprocess
import os
import requests
import cloudinary
import cloudinary.uploader
import base64
import re
from datetime import datetime
from playwright.sync_api import sync_playwright
from concurrent.futures import ThreadPoolExecutor

# --- 1. CLOUD ENVIRONMENT SETUP ---
@st.cache_resource
def install_browser_binaries():
    """Ensures Chromium binaries are present for Playwright."""
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

upload_executor = ThreadPoolExecutor(max_workers=5)

# --- 3. CORE LOGIC ---

@st.cache_data(show_spinner=False)
def get_base64_image(image_path):
    """Helper to convert local image to base64 for inline HTML rendering."""
    if not os.path.exists(image_path):
        return ""
    with open(image_path, "rb") as img_file:
        return base64.b64encode(img_file.read()).decode()

def background_upload(file_path, public_id):
    """Uploads to Cloudinary in a background thread."""
    return cloudinary.uploader.upload(file_path, folder="airtableautomation", public_id=public_id)

def normalize_type_string(raw_title):
    """
    Takes everything before the letter 'I' in the title and removes spaces.
    Example: 'W1-2 I Bi Weekly Report | MEA' -> 'W1-2'
    """
    # 1. Take everything before the pipe if it exists
    base_text = raw_title.split("|")[0].strip() if "|" in raw_title else raw_title.strip()
    
    # 2. Take everything before the character 'I'
    if "I" in base_text:
        base_text = base_text.split("I")[0].strip()
    
    # 3. Remove all remaining whitespace
    return re.sub(r'\s+', '', base_text)

def get_region_code(region_name):
    """Maps display names to specific lowercase codes."""
    mapping = {
        "LATAM": "latam",
        "Asia": "asia",
        "EU": "eu",
        "MEA": "mea",
        "Canada": "canada",
        "All Regions": "allregions"
    }
    return mapping.get(region_name, region_name.lower().replace(" ", ""))

def capture_regional_images(target_url):
    regions = ["Asia", "EU", "LATAM", "Canada", "MEA", "All Regions"]
    captured_data = []
    capture_date = datetime.now().strftime("%Y-%m-%d")
    header_title_raw = "Consolidated Report"

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={'width': 1920, 'height': 5000}, device_scale_factor=2)
        page = context.new_page()
        
        st.info("🔗 Connecting to Airtable Interface...")
        page.goto(target_url, wait_until="commit")
        page.wait_for_selector('div[role="tab"]', timeout=15000)
        
        # UI Cleanup
        page.evaluate("""
            () => {
                const removeSelectors = [
                    '#onetrust-banner-sdk', '.onetrust-pc-dark-filter',
                    '[id*="cookie"]', '[class*="cookie"]',
                    'header.flex.flex-none.items-center.width-full',
                    '.flex.items-center.py2.px2-and-half.border-bottom',
                    '[data-testid="interface-header"]', '.interfaceHeader'
                ];
                removeSelectors.forEach(selector => {
                    document.querySelectorAll(selector).forEach(el => el.remove());
                });
            }
        """)

        try:
            header_selector = 'h2.font-family-display-updated, h1, .interfaceTitle'
            header_locator = page.locator(header_selector).first
            header_title_raw = header_locator.inner_text(timeout=3000)
        except Exception:
            pass 

        normalized_type = normalize_type_string(header_title_raw)

        for region in regions:
            status_placeholder = st.empty()
            status_placeholder.write(f"🔄 **{region}**: Capturing...")
            
            try:
                # 1. Navigate to Regional Tab
                tab = page.locator(f'div[role="tab"]:has-text("{region}")')
                tab.click()
                page.wait_for_function("() => document.querySelector('.loading-spinner') === null")
                page.wait_for_timeout(400)

                # 2. Rect Calculation
                layout_info = page.evaluate("""
                    () => {
                        const titleEl = document.querySelector('h2.font-family-display-updated, h1, .interfaceTitle');
                        const metricsGrid = document.querySelector('[data-testid="page-element:bigNumber"]')?.closest('[data-testid="gridRowSection"]');
                        const chartsSection = document.querySelector('[data-testid="page-element:chart"]')?.closest('[data-testid="gridRowSection"]');
                        const getRect = (el) => {
                            if (!el) return null;
                            const r = el.getBoundingClientRect();
                            return { x: r.left, y: r.top + window.scrollY, width: r.width, height: r.height };
                        };
                        const titleRect = getRect(titleEl);
                        const metricsRect = getRect(metricsGrid);
                        const chartsRect = getRect(chartsSection);
                        const startY = titleRect ? titleRect.y : 0;
                        const metricsBottom = metricsRect ? (metricsRect.y + metricsRect.height + 20) : 600;
                        const headerClip = { x: 0, y: Math.floor(startY), width: 1920, height: Math.floor(metricsBottom - startY) };
                        let contentClip = null;
                        if (chartsRect) {
                            const charts = chartsSection.querySelectorAll('[data-testid="page-element:chart"]');
                            let maxBottom = chartsRect.y + chartsRect.height;
                            if (charts.length > 0) {
                                const bottoms = Array.from(charts).map(el => el.getBoundingClientRect().bottom + window.scrollY);
                                maxBottom = Math.max(...bottoms) + 27; 
                            }
                            contentClip = { x: 0, y: Math.floor(chartsRect.y - 10), width: 1920, height: Math.floor(maxBottom - chartsRect.y) };
                        } else {
                            contentClip = { x: 0, y: 650, width: 1920, height: 1000 };
                        }
                        return { headerClip, contentClip };
                    }
                """)

                region_code = get_region_code(region)
                img_counter = 1 
                
                # --- CAPTURE SEQUENCE ---
                region_entry = {
                    "region": region,
                    "date": capture_date,
                    "header_id": header_title_raw,
                    "image_futures": [] 
                }

                # Image 1: Header
                header_filename = f"temp_{region_code}_1.jpg"
                page.screenshot(path=header_filename, clip=layout_info['headerClip'], type="jpeg", quality=85)
                pub_id = f"{region_code}-{normalized_type}-image{img_counter}-{capture_date}"
                region_entry["image_futures"].append({
                    "type": "header",
                    "local": header_filename,
                    "future": upload_executor.submit(background_upload, header_filename, pub_id)
                })
                img_counter += 1

                # Capture Galleries
                if region != "All Regions":
                    def capture_paged(label):
                        nonlocal img_counter
                        page_idx = 1
                        while page_idx <= 5:
                            gal_info = page.evaluate(f"""
                                () => {{
                                    const headers = Array.from(document.querySelectorAll('h1, h2, h3, h4, div'));
                                    const header = headers.find(h => h.innerText && h.innerText.includes("{label}"));
                                    if (header) {{
                                        const container = header.closest('[data-testid="gridRowSection"]') || header.closest('[data-testid="page-element:gallery"]');
                                        if (container) {{
                                            const rect = container.getBoundingClientRect();
                                            return {{ x: 0, y: rect.top + window.scrollY - 10, width: 1920, height: rect.height + 20 }};
                                        }}
                                    }}
                                    const el = document.querySelector('[aria-label*="{label}"]');
                                    if (!el) return null;
                                    const rect = el.getBoundingClientRect();
                                    return {{ x: 0, y: rect.top + window.scrollY - 10, width: 1920, height: rect.height + 20 }};
                                }}
                            """)
                            if not gal_info: break
                            page.mouse.wheel(0, gal_info['y'] - 100)
                            page.wait_for_timeout(300)
                            
                            fn = f"temp_{region_code}_gal_{img_counter}.jpg"
                            page.screenshot(path=fn, clip=gal_info, type="jpeg", quality=85)
                            p_id = f"{region_code}-{normalized_type}-image{img_counter}-{capture_date}"
                            
                            region_entry["image_futures"].append({
                                "type": "gallery",
                                "local": fn,
                                "future": upload_executor.submit(background_upload, fn, p_id)
                            })
                            img_counter += 1
                            
                            # Find the target section first, then the Next button inside it
                            section_locator = page.locator('div[data-testid="gridRowSection"], div[data-testid="page-element:gallery"]').filter(has_text=label).first
                            next_btn = section_locator.locator('div[role="button"]:has(path[d*="m4.64.17"]), button[aria-label="Next page"], div[aria-label="Next page"]').last
                            
                            # Fallback if the strict layout locator fails
                            if not next_btn.is_visible():
                                next_btn = page.locator(f'[aria-label*="{label}"] div[role="button"]:has(path[d*="m4.64.17"])').first

                            if next_btn.is_visible() and not next_btn.evaluate("el => el.getAttribute('aria-disabled') === 'true' || el.hasAttribute('disabled')"):
                                next_btn.click()
                                page_idx += 1
                                page.wait_for_timeout(800) # Increased timeout to allow page animation/render
                            else: 
                                break

                    capture_paged("Tickets in Progress")

                # Content/Charts
                content_filename = f"temp_{region_code}_content.jpg"
                page.screenshot(path=content_filename, clip=layout_info['contentClip'], type="jpeg", quality=85)
                pub_id = f"{region_code}-{normalized_type}-image{img_counter}-{capture_date}"
                region_entry["image_futures"].append({
                    "type": "content",
                    "local": content_filename,
                    "future": upload_executor.submit(background_upload, content_filename, pub_id)
                })
                img_counter += 1

                # Completed Gallery
                if region != "All Regions":
                    capture_paged("Completed Ticket Gallery")

                captured_data.append(region_entry)
                status_placeholder.write(f"✅ **{region}** captured")
                
            except Exception as e:
                st.error(f"Error on {region}: {e}")

        browser.close()

    # Finalize Data: Convert Futures to URLs
    final_data = []
    for item in captured_data:
        processed_images = []
        for img in item["image_futures"]:
            url = img["future"].result()["secure_url"]
            processed_images.append({"type": img["type"], "local": img["local"], "url": url})
        
        item["images"] = processed_images
        final_data.append(item)

    return final_data

def sync_to_airtable(data_list):
    url = f"https://api.airtable.com/v0/{st.secrets['BASE_ID']}/{st.secrets['TABLE_NAME']}"
    headers = {"Authorization": f"Bearer {st.secrets['AIRTABLE_TOKEN']}", "Content-Type": "application/json"}
    
    if not data_list: return None

    records_to_create = []
    for item in data_list:
        record_type = f"{item['header_id'].split('|')[0].strip()} | {item['region']}"
        record_attachments = [{"url": img["url"]} for img in item["images"]]
        
        header_url = next((i["url"] for i in item["images"] if i["type"] == "header"), "")
        charts_url = next((i["url"] for i in item["images"] if i["type"] == "content"), "")
        
        fields = {
            "Type": record_type, 
            "Date": item["date"], 
            "Attachments": record_attachments,
            "Header": header_url, 
            "Charts": charts_url
        }
        
        records_to_create.append({"fields": fields})

    for i in range(0, len(records_to_create), 10):
        chunk = records_to_create[i:i+10]
        response = requests.post(url, headers=headers, json={"records": chunk})
        if response.status_code == 200:
            st.success(f"🎉 Created records {i+1} to {min(i+10, len(records_to_create))}")
        else:
            st.error(f"❌ Sync Error: {response.text}")
    
    st.session_state.capture_results = None

# --- 4. USER INTERFACE ---

st.set_page_config(page_title="Airtable Report Capture", layout="wide")
st.title("🗺️ Bi-Weekly Report Capture")

st.markdown("""
    <style>
    .preview-container {
        max-height: 700px; overflow-y: auto; border: 1px solid #ddd;
        border-radius: 8px; padding: 0px; background: #f9f9f9; margin-bottom: 20px;
    }
    .preview-container img {
        width: 100%; margin-bottom: 8px; display: block;
        box-shadow: 0 1px 3px rgba(0,0,0,0.1);
    }
    </style>
""", unsafe_allow_html=True)

if 'capture_results' not in st.session_state:
    st.session_state.capture_results = None

url_input = st.text_input("Airtable Interface URL", placeholder="https://airtable.com/app...")

col_btn1, col_btn2 = st.columns([1, 4])
with col_btn1:
    if st.button("🚀 Run Capture"):
        if url_input:
            st.session_state.capture_results = capture_regional_images(url_input)
        else:
            st.warning("Please enter a URL first.")
with col_btn2:
    if st.session_state.capture_results:
        if st.button("📤 Upload to Airtable", type="primary"):
            sync_to_airtable(st.session_state.capture_results)

if st.session_state.capture_results:
    st.divider()
    cols = st.columns(len(st.session_state.capture_results))
    for idx, item in enumerate(st.session_state.capture_results):
        with cols[idx]:
            st.subheader(item['region'])
            html_parts = [f'<div class="preview-container">']
            for img in item["images"]:
                html_parts.append(f'<img src="data:image/jpeg;base64,{get_base64_image(img["local"])}" />')
            html_parts.append('</div>')
            st.markdown("".join(html_parts), unsafe_allow_html=True)
