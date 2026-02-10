import streamlit as st
import subprocess
import os
import requests
import cloudinary
import cloudinary.uploader
import base64
from datetime import datetime
from playwright.sync_api import sync_playwright
from concurrent.futures import ThreadPoolExecutor

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

# Global executor for background uploads to speed up the loop
upload_executor = ThreadPoolExecutor(max_workers=5)

# --- 3. CORE LOGIC ---

@st.cache_data(show_spinner=False)
def get_base64_image(image_path):
    """Helper to convert local image to base64 for inline HTML rendering with caching."""
    if not os.path.exists(image_path):
        return ""
    with open(image_path, "rb") as img_file:
        return base64.b64encode(img_file.read()).decode()

def background_upload(file_path, public_id):
    """Uploads to Cloudinary in a background thread."""
    return cloudinary.uploader.upload(file_path, folder="airtableautomation", public_id=public_id)

def capture_regional_images(target_url):
    regions = ["Asia", "Europe", "LATAM", "Canada", "All Regions"]
    captured_data = []
    capture_date = datetime.now().strftime("%Y-%m-%d")
    header_title = "Consolidated Report"
    upload_futures = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={'width': 1920, 'height': 5000},
            device_scale_factor=2 
        )
        page = context.new_page()
        
        st.info("🔗 Connecting to Airtable Interface...")
        page.goto(target_url, wait_until="commit") # Faster initial load
        page.wait_for_selector('div[role="tab"]', timeout=15000)
        
        # --- CLEANUP UI ELEMENTS ---
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
            header_title = header_title_raw.split("|")[0].strip() if "|" in header_title_raw else header_title_raw.strip()
        except Exception:
            pass 

        for region in regions:
            status_placeholder = st.empty()
            status_placeholder.write(f"🔄 **{region}**: Capturing...")
            
            try:
                # 1. Optimized Navigation
                tab = page.locator(f'div[role="tab"]:has-text("{region}")')
                tab.click()
                
                # Wait for content to actually change instead of a fixed timeout
                page.wait_for_function("() => document.querySelector('.loading-spinner') === null")
                page.wait_for_timeout(400) # Tiny buffer for React paint

                # 2. Optimized Rect Calculation
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
                        
                        const headerClip = {
                            x: 0, y: Math.floor(startY), width: 1920, height: Math.floor(metricsBottom - startY)
                        };

                        let contentClip = null;
                        if (chartsRect) {
                            const charts = chartsSection.querySelectorAll('[data-testid="page-element:chart"]');
                            let maxBottom = chartsRect.y + chartsRect.height;
                            if (charts.length > 0) {
                                const bottoms = Array.from(charts).map(el => el.getBoundingClientRect().bottom + window.scrollY);
                                // Increased offset to +40 (was +25) for more padding at the bottom of the charts
                                maxBottom = Math.max(...bottoms) + 40; 
                            }
                            contentClip = {
                                x: 0, y: Math.floor(chartsRect.y - 10), width: 1920, height: Math.floor(maxBottom - chartsRect.y)
                            };
                        } else {
                            contentClip = { x: 0, y: 650, width: 1920, height: 1000 };
                        }
                        return { headerClip, contentClip };
                    }
                """)

                safe_region = region.lower().replace(' ', '-')
                safe_date = capture_date.replace('-', '')

                # --- PART 1 & 2: SCREENSHOT & PARALLEL UPLOAD ---
                header_filename = f"{safe_region}-header.jpg"
                page.screenshot(path=header_filename, clip=layout_info['headerClip'], type="jpeg", quality=90)
                h_future = upload_executor.submit(background_upload, header_filename, f"{safe_region}-header-{safe_date}")

                content_filename = f"{safe_region}-content.jpg"
                page.screenshot(path=content_filename, clip=layout_info['contentClip'], type="jpeg", quality=85)
                c_future = upload_executor.submit(background_upload, content_filename, f"{safe_region}-content-{safe_date}")

                region_entry = {
                    "region": region,
                    "h_future": h_future,
                    "c_future": c_future,
                    "local_header": header_filename,
                    "local_content": content_filename,
                    "date": capture_date,
                    "header_id": header_title,
                    "in_progress_futures": [],
                    "completed_futures": [] 
                }

                def capture_paged_gallery(gallery_label, future_key):
                    page.evaluate(f"document.querySelector('[aria-label*=\"{gallery_label}\"]')?.style.setProperty('display', 'block', 'important')")
                    page_idx = 1
                    while True:
                        gal_info = page.evaluate(f"""
                            () => {{
                                const el = document.querySelector('[aria-label*="{gallery_label}"]');
                                if (!el) return null;
                                const rect = el.getBoundingClientRect();
                                return {{ x: 0, y: rect.top + window.scrollY - 10, width: 1920, height: rect.height + 20 }};
                            }}
                        """)
                        if not gal_info: break
                        
                        page.mouse.wheel(0, gal_info['y'] - 100)
                        page.wait_for_timeout(300) 

                        gal_filename = f"{safe_region}-{gallery_label[:3].lower()}-{page_idx}.jpg"
                        page.screenshot(path=gal_filename, clip=gal_info, type="jpeg", quality=80)
                        
                        g_future = upload_executor.submit(background_upload, gal_filename, f"{safe_region}-{gallery_label[:3].lower()}{page_idx}-{safe_date}")
                        region_entry[future_key].append({"local": gal_filename, "future": g_future})

                        next_btn = page.locator(f'[aria-label*="{gallery_label}"] div[role="button"]:has(path[d*="m4.64.17"])').first
                        if next_btn.is_visible() and not next_btn.evaluate("el => el.getAttribute('aria-disabled') === 'true'"):
                            next_btn.click()
                            page_idx += 1
                            page.wait_for_timeout(400)
                        else: break
                        if page_idx > 5: break

                if region != "All Regions":
                    capture_paged_gallery("In Progress", "in_progress_futures")
                    capture_paged_gallery("Completed Request Gallery", "completed_futures")

                captured_data.append(region_entry)
                status_placeholder.write(f"✅ **{region}** captured")
                
            except Exception as e:
                st.error(f"Error on {region}: {e}")

        browser.close()

    # Finalize Data: Convert Futures to URLs
    final_data = []
    for item in captured_data:
        item["header_url"] = item.pop("h_future").result()["secure_url"]
        item["content_url"] = item.pop("c_future").result()["secure_url"]
        item["in_progress_pages"] = [{"local": f["local"], "url": f["future"].result()["secure_url"]} for f in item.pop("in_progress_futures")]
        item["completed_gallery_pages"] = [{"local": f["local"], "url": f["future"].result()["secure_url"]} for f in item.pop("completed_futures")]
        final_data.append(item)

    return final_data

def sync_to_airtable(data_list):
    url = f"https://api.airtable.com/v0/{st.secrets['BASE_ID']}/{st.secrets['TABLE_NAME']}"
    headers = {"Authorization": f"Bearer {st.secrets['AIRTABLE_TOKEN']}", "Content-Type": "application/json"}
    
    if not data_list: return None

    records_to_create = []
    for item in data_list:
        record_type = f"{item.get('header_id', 'Consolidated Report')} | {item['region']}"
        
        record_attachments = [{"url": item["header_url"]}, {"url": item["content_url"]}]
        for i_page in item.get("in_progress_pages", []): record_attachments.append({"url": i_page["url"]})
        for g_page in item.get("completed_gallery_pages", []): record_attachments.append({"url": g_page["url"]})
            
        fields = {
            "Type": record_type, "Date": item["date"], "Attachments": record_attachments,
            "Header": item["header_url"], "Charts": item["content_url"]
        }
        
        for i, p in enumerate(item.get("completed_gallery_pages", []), 1):
            if i <= 3: fields[f"Gallery {i}"] = p["url"]
        for i, p in enumerate(item.get("in_progress_pages", []), 1):
            if i <= 3: fields[f"Progress {i}"] = p["url"]
        
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

url_input = st.text_input("Airtable Interface URL", value="")

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
            html_parts = [f'<div class="preview-container" id="container-{idx}">']
            html_parts.append(f'<img src="data:image/jpeg;base64,{get_base64_image(item["local_header"])}" />')
            for g in item.get("in_progress_pages", []):
                html_parts.append(f'<img src="data:image/jpeg;base64,{get_base64_image(g["local"])}" />')
            html_parts.append(f'<img src="data:image/jpeg;base64,{get_base64_image(item["local_content"])}" />')
            for g in item.get("completed_gallery_pages", []):
                html_parts.append(f'<img src="data:image/jpeg;base64,{get_base64_image(g["local"])}" />')
            html_parts.append('</div>')
            st.markdown("".join(html_parts), unsafe_allow_html=True)
