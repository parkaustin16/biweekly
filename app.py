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
    base_text = raw_title.split("|")[0].strip() if "|" in raw_title else raw_title.strip()
    if "I" in base_text:
        base_text = base_text.split("I")[0].strip()
    return re.sub(r'\s+', '', base_text)

def get_region_code(region_name):
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
        # Large height to ensure we can scroll to any content
        context = browser.new_context(viewport={'width': 1920, 'height': 8000}, device_scale_factor=2)
        page = context.new_page()
        
        st.info("🔗 Connecting to Airtable Interface...")
        page.goto(target_url, wait_until="networkidle")
        page.wait_for_selector('div[role="tab"]', timeout=20000)
        
        # Enhanced UI Cleanup: Remove fixed headers/navs that block screenshots
        page.evaluate("""
            () => {
                const removeSelectors = [
                    '#onetrust-banner-sdk', '.onetrust-pc-dark-filter',
                    '[id*="cookie"]', '[class*="cookie"]',
                    'header', '.interfaceHeader', '[data-testid="interface-header"]',
                    '.flex.items-center.py2.px2-and-half.border-bottom'
                ];
                removeSelectors.forEach(selector => {
                    document.querySelectorAll(selector).forEach(el => el.remove());
                });
                // Ensure the body doesn't have overflow hidden from modals
                document.body.style.overflow = 'visible';
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
            status_placeholder.write(f"🔄 **{region}**: Processing...")
            
            try:
                # 1. Switch Tab
                tab = page.locator(f'div[role="tab"]:has-text("{region}")')
                tab.click()
                page.wait_for_timeout(1000) # Wait for tab content to transition

                # 2. Get Layout Dimensions (Flexible)
                # This calculates the exact bounding boxes based on whatever is actually on screen
                layout_info = page.evaluate("""
                    () => {
                        const getRect = (el) => {
                            if (!el) return null;
                            const r = el.getBoundingClientRect();
                            return { x: r.left, y: r.top + window.scrollY, width: r.width, height: r.height };
                        };

                        const titleEl = document.querySelector('h2.font-family-display-updated, h1, .interfaceTitle');
                        const metricsGrid = document.querySelector('[data-testid="page-element:bigNumber"]')?.closest('[data-testid="gridRowSection"]');
                        
                        const titleRect = getRect(titleEl);
                        const metricsRect = getRect(metricsGrid);

                        const startY = titleRect ? titleRect.y : 0;
                        const metricsBottom = metricsRect ? (metricsRect.y + metricsRect.height + 20) : 550;

                        return {
                            headerClip: { x: 0, y: Math.floor(startY - 10), width: 1920, height: Math.floor(metricsBottom - startY + 20) }
                        };
                    }
                """)

                region_code = get_region_code(region)
                img_counter = 1 
                region_entry = {
                    "region": region,
                    "date": capture_date,
                    "header_id": header_title_raw,
                    "image_futures": [] 
                }

                # Image 1: Header + Metrics
                header_filename = f"temp_{region_code}_1.jpg"
                page.screenshot(path=header_filename, clip=layout_info['headerClip'], type="jpeg", quality=90)
                
                pub_id = f"{region_code}-{normalized_type}-img{img_counter}-{capture_date}"
                region_entry["image_futures"].append({
                    "type": "header",
                    "local": header_filename,
                    "future": upload_executor.submit(background_upload, header_filename, pub_id)
                })
                img_counter += 1

                # 3. Flexible Gallery Capture (Finds exact box no matter how many records)
                def capture_dynamic_gallery(label_text):
                    nonlocal img_counter
                    page_idx = 1
                    
                    while page_idx <= 10: # Limit pages to prevent infinite loops
                        # JS logic to find the specific gallery box for this label
                        gal_rect = page.evaluate(f"""
                            (label) => {{
                                // Find any header or text containing the label
                                const elements = Array.from(document.querySelectorAll('h1, h2, h3, h4, div.font-weight-bold'));
                                const header = elements.find(el => el.innerText && el.innerText.includes(label));
                                if (!header) return null;

                                // Navigate up to the actual component container
                                const container = header.closest('[data-testid="gridRowSection"]') || 
                                                  header.closest('[data-testid="page-element:gallery"]') ||
                                                  header.parentElement.parentElement;
                                
                                if (!container) return null;
                                
                                const r = container.getBoundingClientRect();
                                return {{ 
                                    x: 0, 
                                    y: Math.floor(r.top + window.scrollY - 15), 
                                    width: 1920, 
                                    height: Math.floor(r.height + 30) 
                                }};
                            }}
                        """, label_text)

                        if not gal_rect:
                            break
                        
                        # Scroll to it
                        page.mouse.wheel(0, gal_rect['y'] - 200)
                        page.wait_for_timeout(500)

                        # Re-calculate after scroll to ensure accuracy
                        gal_rect = page.evaluate(f"""
                            (label) => {{
                                const elements = Array.from(document.querySelectorAll('h1, h2, h3, h4, div.font-weight-bold'));
                                const header = elements.find(el => el.innerText && el.innerText.includes(label));
                                const container = header.closest('[data-testid="gridRowSection"]') || header.closest('[data-testid="page-element:gallery"]');
                                const r = container.getBoundingClientRect();
                                return {{ x: 0, y: Math.floor(r.top + window.scrollY - 10), width: 1920, height: Math.floor(r.height + 20) }};
                            }}
                        """, label_text)

                        fn = f"temp_{region_code}_gal_{img_counter}.jpg"
                        page.screenshot(path=fn, clip=gal_rect, type="jpeg", quality=90)
                        
                        p_id = f"{region_code}-{normalized_type}-img{img_counter}-{capture_date}"
                        region_entry["image_futures"].append({
                            "type": "gallery",
                            "local": fn,
                            "future": upload_executor.submit(background_upload, fn, p_id)
                        })
                        img_counter += 1

                        # Look for "Next" button specifically within this gallery container
                        # Airtable uses specific SVG paths for the chevron
                        next_btn = page.locator(f'div[data-testid="gridRowSection"]:has-text("{label_text}")') \
                                       .locator('div[role="button"]:has(svg), button:has(svg)') \
                                       .filter(has=page.locator('path[d*="m4.64.17"]')) \
                                       .last

                        if next_btn.is_visible() and not next_btn.evaluate("el => el.getAttribute('aria-disabled') === 'true'"):
                            next_btn.click()
                            page.wait_for_timeout(1000) # Wait for records to swap
                            page_idx += 1
                        else:
                            break

                if region != "All Regions":
                    capture_dynamic_gallery("Tickets in Progress")

                # 4. Capture Chart Section (Flexible Height)
                chart_info = page.evaluate("""
                    () => {
                        const charts = document.querySelectorAll('[data-testid="page-element:chart"]');
                        if (charts.length === 0) return null;
                        
                        let minTop = Infinity;
                        let maxBottom = 0;
                        
                        charts.forEach(c => {
                            const r = c.closest('[data-testid="gridRowSection"]').getBoundingClientRect();
                            minTop = Math.min(minTop, r.top + window.scrollY);
                            maxBottom = Math.max(maxBottom, r.bottom + window.scrollY);
                        });
                        
                        return { x: 0, y: Math.floor(minTop - 20), width: 1920, height: Math.floor(maxBottom - minTop + 40) };
                    }
                """)

                if chart_info:
                    content_filename = f"temp_{region_code}_charts.jpg"
                    page.screenshot(path=content_filename, clip=chart_info, type="jpeg", quality=90)
                    p_id = f"{region_code}-{normalized_type}-img{img_counter}-{capture_date}"
                    region_entry["image_futures"].append({
                        "type": "charts",
                        "local": content_filename,
                        "future": upload_executor.submit(background_upload, content_filename, p_id)
                    })
                    img_counter += 1

                if region != "All Regions":
                    capture_dynamic_gallery("Completed Ticket Gallery")

                captured_data.append(region_entry)
                status_placeholder.write(f"✅ **{region}** captured")
                
            except Exception as e:
                st.error(f"Error on {region}: {str(e)}")

        browser.close()

    # Finalize Futures
    final_data = []
    for item in captured_data:
        processed_images = []
        for img in item["image_futures"]:
            res = img["future"].result()
            processed_images.append({"type": img["type"], "local": img["local"], "url": res["secure_url"]})
        item["images"] = processed_images
        final_data.append(item)

    return final_data

def sync_to_airtable(data_list):
    url = f"https://api.airtable.com/v0/{st.secrets['BASE_ID']}/{st.secrets['TABLE_NAME']}"
    headers = {"Authorization": f"Bearer {st.secrets['AIRTABLE_TOKEN']}", "Content-Type": "application/json"}
    
    if not data_list: return

    records = []
    for item in data_list:
        record_type = f"{item['header_id'].split('|')[0].strip()} | {item['region']}"
        record_attachments = [{"url": img["url"]} for img in item["images"]]
        
        # Determine specific images for distinct fields
        header_url = next((i["url"] for i in item["images"] if i["type"] == "header"), "")
        charts_url = next((i["url"] for i in item["images"] if i["type"] == "charts"), "")
        
        records.append({
            "fields": {
                "Type": record_type,
                "Date": item["date"],
                "Attachments": record_attachments,
                "Header": header_url,
                "Charts": charts_url
            }
        })

    for i in range(0, len(records), 10):
        chunk = records[i:i+10]
        resp = requests.post(url, headers=headers, json={"records": chunk})
        if resp.status_code == 200:
            st.success(f"🎉 Synced {len(chunk)} records.")
        else:
            st.error(f"❌ Error: {resp.text}")
    
    st.session_state.capture_results = None

# --- UI ---
st.set_page_config(page_title="Airtable Report Capture", layout="wide")
st.title("🗺️ Bi-Weekly Report Capture")

if 'capture_results' not in st.session_state:
    st.session_state.capture_results = None

url_input = st.text_input("Airtable Interface URL")

c1, c2 = st.columns([1, 4])
with c1:
    if st.button("🚀 Run Capture"):
        if url_input:
            st.session_state.capture_results = capture_regional_images(url_input)
        else:
            st.warning("Enter a URL.")
with c2:
    if st.session_state.capture_results:
        if st.button("📤 Upload to Airtable", type="primary"):
            sync_to_airtable(st.session_state.capture_results)

if st.session_state.capture_results:
    st.divider()
    cols = st.columns(len(st.session_state.capture_results))
    for idx, item in enumerate(st.session_state.capture_results):
        with cols[idx]:
            st.subheader(item['region'])
            for img in item["images"]:
                st.image(img['local'], use_container_width=True)
