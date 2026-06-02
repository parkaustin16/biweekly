import streamlit as st
import subprocess
import os
import io
import re
import requests
import cloudinary
import cloudinary.uploader
import base64
from datetime import datetime
from playwright.sync_api import sync_playwright
from concurrent.futures import ThreadPoolExecutor
from PIL import Image

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
# Assumes these are set in .streamlit/secrets.toml
cloudinary.config(
    cloud_name = st.secrets["CLOUDINARY_CLOUD_NAME"],
    api_key = st.secrets["CLOUDINARY_API_KEY"],
    api_secret = st.secrets["CLOUDINARY_API_SECRET"],
    secure = True
)

upload_executor = ThreadPoolExecutor(max_workers=5)

CREATIVEHUB_URL = "https://lge-d2c.com/creativehub/reports"
CREATIVEHUB_TABS = ["All", "Asia", "Canada", "Europe", "LATAM", "MEA"]

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

def capture_regional_images(target_url):
    regions = ["Asia", "EU", "LATAM", "Canada", "MEA", "All Regions"]
    captured_data = []
    capture_date = datetime.now().strftime("%Y-%m-%d")
    header_title_clean = "Report"
    week_id = "W0"

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={'width': 1920, 'height': 5000},
            device_scale_factor=2 
        )
        page = context.new_page()
        
        connection_status = st.empty()
        connection_status.info("🔗 Connecting to Airtable Interface...")

        page.goto(target_url, wait_until="domcontentloaded", timeout=60000)
        # Give the Airtable React app time to hydrate before looking for tabs
        try:
            page.wait_for_load_state("networkidle", timeout=20000)
        except Exception:
            pass  # networkidle may never fire on SPAs; proceed anyway
        try:
            page.wait_for_selector('div[role="tab"]', timeout=30000)
        except Exception as tab_err:
            debug_path = "debug-no-tabs.jpg"
            page.screenshot(path=debug_path, full_page=True, type="jpeg", quality=80)
            debug_b64 = get_base64_image(debug_path)
            st.error(f"Could not find tabs on the page. Page snapshot below.")
            st.markdown(f'<img src="data:image/jpeg;base64,{debug_b64}" style="width:100%"/>', unsafe_allow_html=True)
            browser.close()
            return []
        
        connection_status.success("✅ Connected to Airtable Interface")
        
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

        # Extract Week ID
        try:
            header_selector = 'h2.font-family-display-updated, h1, .interfaceTitle'
            header_locator = page.locator(header_selector).first
            raw_text = header_locator.inner_text(timeout=3000)
            
            if "I" in raw_text:
                week_id = raw_text.split("I")[0].strip()
                header_title_clean = raw_text.strip()
            elif "|" in raw_text:
                week_id = raw_text.split("|")[0].strip()
                header_title_clean = raw_text.strip()
            else:
                week_id = raw_text.strip().replace(" ", "-")
                header_title_clean = raw_text.strip()
        except Exception:
            pass 

        for region in regions:
            status_placeholder = st.empty()
            status_placeholder.write(f"🔄 **{region}**: Navigating and Capturing...")
            img_counter = 1
            
            try:
                # 1. Click the tab via JS to bypass actionability/scroll issues
                # Exact match first, then substring fallback
                click_result = page.evaluate(f"""
                    () => {{
                        const tabs = Array.from(document.querySelectorAll('div[role="tab"]'));
                        const exact = tabs.find(t => t.textContent.trim() === '{region}');
                        if (exact) {{ exact.click(); return 'exact:' + exact.textContent.trim(); }}
                        const fuzzy = tabs.find(t => t.textContent.trim().includes('{region}'));
                        if (fuzzy) {{ fuzzy.click(); return 'fuzzy:' + fuzzy.textContent.trim(); }}
                        return 'not_found:' + tabs.map(t => t.textContent.trim()).join('|');
                    }}
                """)
                if click_result.startswith('not_found:'):
                    available = click_result.replace('not_found:', '')
                    raise Exception(f"Tab not found. Available tabs: [{available}]")
                status_placeholder.write(f"🔄 **{region}**: Tab clicked ({click_result}), waiting...")

                # 2. Wait for content to settle
                try:
                    page.wait_for_function("() => document.querySelector('.loading-spinner') === null", timeout=5000)
                except Exception:
                    pass
                page.wait_for_timeout(1200)

                # --- CAPTURE SPECIFIC TAB URL ---
                specific_tab_url = page.url

                safe_region = region.replace(' ', '-')
                safe_date = capture_date.replace('-', '')
                filename_week = week_id.replace(" ", "-")

                # Force lazy images to load, scroll to trigger remaining content
                page.evaluate("""
                    () => {
                        document.querySelectorAll('img[loading="lazy"]').forEach(img => {
                            img.loading = 'eager';
                            if (img.dataset.src) img.src = img.dataset.src;
                        });
                    }
                """)
                total_height = page.evaluate("document.body.scrollHeight")
                pos = 0
                while pos < total_height:
                    page.evaluate(f"window.scrollTo(0, {pos})")
                    page.wait_for_timeout(120)
                    pos += 800
                    total_height = page.evaluate("document.body.scrollHeight")
                page.evaluate("window.scrollTo(0, 0)")
                page.wait_for_timeout(500)

                # Resize viewport to full content height before screenshotting
                full_height = page.evaluate("document.body.scrollHeight")
                page.set_viewport_size({'width': 1920, 'height': min(full_height + 100, 20000)})
                page.wait_for_timeout(300)

                # FULL PAGE SCREENSHOT
                full_filename = f"{safe_region}-full.jpg"
                page.screenshot(path=full_filename, full_page=True, type="jpeg", quality=85)
                h_future = upload_executor.submit(background_upload, full_filename, f"{safe_region}-{filename_week}-image{img_counter}-{safe_date}")
                img_counter += 1

                region_entry = {
                    "region": region,
                    "h_future": h_future,
                    "c_future": h_future,
                    "date": capture_date,
                    "header_id": header_title_clean,
                    "local_header": full_filename,
                    "local_content": full_filename,
                    "tab_url": specific_tab_url,
                    "in_progress_futures": [],
                    "completed_futures": []
                }

                def capture_paged_gallery(gallery_label, future_key):
                    nonlocal img_counter
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

                        gal_prefix = "prog" if future_key == "in_progress_futures" else "comp"
                        gal_filename = f"{safe_region}-{gal_prefix}-{page_idx}.jpg"
                        page.screenshot(path=gal_filename, clip=gal_info, type="jpeg", quality=85)

                        g_future = upload_executor.submit(background_upload, gal_filename, f"{safe_region}-{filename_week}-image{img_counter}-{safe_date}")
                        region_entry[future_key].append({"local": gal_filename, "future": g_future})

                        img_counter += 1
                        page_idx += 1

                        next_btn = page.locator(f'[aria-label*="{gallery_label}"] div[role="button"]:has(path[d*="m4.64.17"])').first
                        if next_btn.is_visible() and not next_btn.evaluate("el => el.getAttribute('aria-disabled') === 'true'"):
                            next_btn.click()
                            page.wait_for_timeout(400)
                        else: break
                        if page_idx > 5: break

                capture_paged_gallery("Tickets in Progress", "in_progress_futures")
                capture_paged_gallery("Completed Ticket Gallery", "completed_futures")

                captured_data.append(region_entry)
                status_placeholder.write(f"✅ **{region}** captured")
                
            except Exception as e:
                st.error(f"Error on {region}: {e}")

        browser.close()

    final_data = []
    for item in captured_data:
        item["header_url"] = item.pop("h_future").result()["secure_url"]
        item["content_url"] = item.pop("c_future").result()["secure_url"]
        item["in_progress_pages"] = [{"local": f["local"], "url": f["future"].result()["secure_url"]} for f in item.pop("in_progress_futures")]
        item["completed_gallery_pages"] = [{"local": f["local"], "url": f["future"].result()["secure_url"]} for f in item.pop("completed_futures")]
        final_data.append(item)

    return final_data

def sync_to_airtable(data_list):
    """Sends captured data, specific deep-links, and Cloudinary links to Airtable."""
    url = f"https://api.airtable.com/v0/{st.secrets['BASE_ID']}/{st.secrets['TABLE_NAME']}"
    headers = {"Authorization": f"Bearer {st.secrets['AIRTABLE_TOKEN']}", "Content-Type": "application/json"}
    
    if not data_list: return None

    records_to_create = []
    for item in data_list:
        record_type = f"{item.get('header_id', 'Consolidated Report')} | {item['region']}"
        
        record_attachments = [{"url": item["header_url"]}]
        for i_page in item.get("in_progress_pages", []): record_attachments.append({"url": i_page["url"]})
        record_attachments.append({"url": item["content_url"]})
        for g_page in item.get("completed_gallery_pages", []): record_attachments.append({"url": g_page["url"]})
            
        fields = {
            "Type": record_type, 
            "Date": item["date"], 
            "URL": item["tab_url"],  # Map the specific deep-link URL here
            "Attachments": record_attachments,
            "Header": item["header_url"], 
            "Charts": item["content_url"]
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

def capture_creativehub_reports():
    """Captures a full-page screenshot for each tab on the CreativeHub Reports site."""
    captured_data = []
    capture_date = datetime.now().strftime("%Y-%m-%d")
    safe_date = capture_date.replace('-', '')

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={'width': 1920, 'height': 1080},
            device_scale_factor=2
        )
        page = context.new_page()

        conn_status = st.empty()
        conn_status.info("🔗 Connecting to CreativeHub Reports...")
        page.goto(CREATIVEHUB_URL, wait_until="domcontentloaded", timeout=30000)
        try:
            page.wait_for_load_state("networkidle", timeout=10000)
        except Exception:
            pass

        # Handle password gate if present
        try:
            pwd_input = page.locator('input[type="password"]')
            if pwd_input.is_visible(timeout=3000):
                pwd_input.fill("123098")
                page.keyboard.press("Enter")
                try:
                    page.wait_for_load_state("networkidle", timeout=10000)
                except Exception:
                    pass
                page.wait_for_timeout(500)
        except Exception:
            pass

        conn_status.success("✅ Connected to CreativeHub Reports")

        for tab_name in CREATIVEHUB_TABS:
            tab_status = st.empty()
            tab_status.write(f"🔄 **{tab_name}**: Capturing...")
            try:
                # Wait until nav tabs are actually rendered before clicking.
                # "Skip to main content" appearing alone means JS hasn't run yet.
                try:
                    page.wait_for_function(
                        """() => {
                            const candidates = document.querySelectorAll('[role="tab"], a, button, li');
                            return Array.from(candidates).some(el => {
                                const t = (el.innerText || el.textContent || '').trim();
                                return t.length > 0 && t !== 'Skip to main content';
                            });
                        }""",
                        timeout=15000
                    )
                except Exception:
                    pass

                # Click the correct tab — pick topmost y-position match to avoid
                # hitting gallery region badges that share the same label text.
                click_result = page.evaluate(f"""
                    () => {{
                        const tabName = '{tab_name}';
                        const candidates = Array.from(document.querySelectorAll(
                            '[role="tab"], a, button, li'
                        ));
                        let matches = candidates.filter(el => {{
                            const t = (el.innerText || el.textContent || '').trim();
                            return t === tabName;
                        }});
                        if (matches.length === 0) {{
                            matches = candidates.filter(el => {{
                                const t = (el.innerText || el.textContent || '').trim();
                                return t.startsWith(tabName);
                            }});
                        }}
                        if (matches.length === 0) {{
                            const sample = candidates.slice(0, 20)
                                .map(el => (el.innerText || el.textContent || '').trim().slice(0, 30))
                                .join('|');
                            return 'not_found:' + sample;
                        }}
                        const visible = matches.filter(el => {{
                            const r = el.getBoundingClientRect();
                            return r.width > 0 && r.height > 0;
                        }});
                        const pool = visible.length > 0 ? visible : matches;
                        pool.sort((a, b) => a.getBoundingClientRect().top - b.getBoundingClientRect().top);
                        const best = pool[0];
                        best.click();
                        const r = best.getBoundingClientRect();
                        return 'clicked:' + best.tagName + ':y=' + Math.round(r.top) + ':' + (best.innerText || best.textContent || '').trim();
                    }}
                """)
                if click_result.startswith('not_found:'):
                    raise Exception(f"Tab not found. Candidates: {click_result}")

                try:
                    page.wait_for_load_state("networkidle", timeout=8000)
                except Exception:
                    pass
                page.wait_for_timeout(1000)

                tab_url = page.url
                safe_tab = tab_name.replace(' ', '-')
                filename = f"ch-{safe_tab}-{safe_date}.jpg"

                # Step 1: find the inner scroller and scroll to trigger lazy image loading
                page.evaluate("""() => {
                    const scroller = Array.from(document.querySelectorAll('*')).find(el => {
                        if (el === document.body || el === document.documentElement) return false;
                        const s = window.getComputedStyle(el);
                        return (s.overflowY === 'scroll' || s.overflowY === 'auto')
                            && el.scrollHeight > el.clientHeight + 100;
                    });
                    if (scroller) {
                        window.__ch_scroller = scroller;
                        scroller.scrollTop = scroller.scrollHeight;
                    }
                }""")
                page.wait_for_timeout(800)

                # Step 2: force all images eager
                page.evaluate("""() => {
                    document.querySelectorAll('img').forEach(img => {
                        img.loading = 'eager';
                        if (img.dataset.src) img.src = img.dataset.src;
                        if (img.dataset.lazySrc) img.src = img.dataset.lazySrc;
                    });
                }""")
                page.wait_for_timeout(800)

                # Step 3: unlock the scroller so the full content height is
                # reachable by full_page screenshot (no viewport resize needed).
                page.evaluate("""() => {
                    const scroller = window.__ch_scroller;
                    if (scroller) {
                        scroller.scrollTop = 0;
                        scroller.style.setProperty('overflow', 'visible', 'important');
                        scroller.style.setProperty('overflow-y', 'visible', 'important');
                        scroller.style.setProperty('height', 'auto', 'important');
                        scroller.style.setProperty('max-height', 'none', 'important');
                        scroller.style.setProperty('min-height', '0', 'important');
                        let el = scroller.parentElement;
                        while (el && el !== document.documentElement) {
                            el.style.setProperty('height', 'auto', 'important');
                            el.style.setProperty('max-height', 'none', 'important');
                            el.style.setProperty('overflow', 'visible', 'important');
                            el = el.parentElement;
                        }
                    }
                    document.body.style.setProperty('height', 'auto', 'important');
                    document.body.style.setProperty('max-height', 'none', 'important');
                    document.body.style.setProperty('overflow', 'visible', 'important');
                    document.documentElement.style.setProperty('height', 'auto', 'important');
                    document.documentElement.style.setProperty('max-height', 'none', 'important');
                    document.documentElement.style.setProperty('overflow', 'visible', 'important');
                }""")
                page.wait_for_timeout(500)

                # Hide the floating nav panel via injected CSS (most reliable approach).
                page.evaluate("""() => {
                    const style = document.createElement('style');
                    style.textContent = `
                        div[style*="172px"] { display: none !important; }
                        [aria-label="Close menu"] { display: none !important; }
                    `;
                    document.head.appendChild(style);
                }""")
                page.wait_for_timeout(200)

                # Step 4: measure crop bounds after layout has settled.
                # Use textContent (not innerText) — innerText applies CSS text-transform
                # so 'uppercase' class would return 'D2C CREATIVE HUB', not matching.
                coords = page.evaluate("""() => {
                    const gallery = document.querySelector('section#gallery');
                    let startY = 0;
                    for (const p of document.querySelectorAll('p')) {
                        if ((p.textContent || '').trim() === 'D2C Creative Hub') {
                            startY = Math.max(0, Math.round(p.getBoundingClientRect().top));
                            break;
                        }
                    }
                    const endY = gallery
                        ? Math.round(gallery.getBoundingClientRect().bottom) + 24
                        : document.documentElement.scrollHeight;

                    // Find the panel's left edge so we can crop it out precisely.
                    let panelLeft = window.innerWidth;
                    const btn = document.querySelector('[aria-label="Close menu"]');
                    if (btn) {
                        let el = btn;
                        while (el && el !== document.body) {
                            const styleAttr = el.getAttribute('style') || '';
                            if (styleAttr.includes('172px')) {
                                panelLeft = Math.round(el.getBoundingClientRect().left);
                                break;
                            }
                            el = el.parentElement;
                        }
                    }
                    return {startY, endY, panelLeft};
                }""")

                # Step 5: full_page PNG captures all content regardless of viewport,
                # then Pillow crops precisely to [startY, endY].
                dpr = 2  # device_scale_factor
                png_bytes = page.screenshot(full_page=True, type="png")
                img = Image.open(io.BytesIO(png_bytes))
                top_px = max(0, coords['startY'] * dpr)
                bot_px = min(img.height, coords['endY'] * dpr)
                panel_left = coords['panelLeft']
                right_px = (panel_left * dpr) if panel_left > 100 else img.width
                img.crop((0, top_px, right_px, bot_px)).save(filename, "JPEG", quality=85)

                future = upload_executor.submit(
                    background_upload, filename,
                    f"creativehub-{safe_tab}-{safe_date}"
                )
                captured_data.append({
                    "tab": tab_name,
                    "date": capture_date,
                    "tab_url": tab_url,
                    "local": filename,
                    "future": future,
                })
                tab_status.write(f"✅ **{tab_name}** captured")
            except Exception as e:
                st.error(f"Error on {tab_name}: {e}")

        browser.close()

    final_data = []
    for item in captured_data:
        item["url"] = item.pop("future").result()["secure_url"]
        final_data.append(item)

    return final_data


def sync_creativehub_to_airtable(data_list):
    """Sends CreativeHub full-page captures to Airtable."""
    table_name = st.secrets.get("CH_TABLE_NAME", st.secrets["TABLE_NAME"])
    url = f"https://api.airtable.com/v0/{st.secrets['BASE_ID']}/{table_name}"
    headers = {"Authorization": f"Bearer {st.secrets['AIRTABLE_TOKEN']}", "Content-Type": "application/json"}

    if not data_list:
        return None

    records_to_create = []
    for item in data_list:
        week_match = re.search(r'(\d{1,2}-\d{1,2})\/?$', item.get('tab_url', ''))
        week = f"W{week_match.group(1)}" if week_match else ""
        label = f"{week} | Bi Weekly CreativeHub Report | {item['tab']}" if week else f"Bi Weekly CreativeHub Report | {item['tab']}"
        fields = {
            "Type": label,
            "Date": item["date"],
            "URL": item["tab_url"],
            "Attachments": [{"url": item["url"]}],
            "Header": item["url"],
        }
        records_to_create.append({"fields": fields})

    for i in range(0, len(records_to_create), 10):
        chunk = records_to_create[i:i+10]
        response = requests.post(url, headers=headers, json={"records": chunk})
        if response.status_code == 200:
            st.success(f"🎉 Created records {i+1} to {min(i+10, len(records_to_create))}")
        else:
            st.error(f"❌ Sync Error: {response.text}")

    st.session_state.ch_results = None

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
if 'ch_results' not in st.session_state:
    st.session_state.ch_results = None

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
            # Display link for verification
            st.caption(f"[Direct Tab Link]({item['tab_url']})")
            html_parts = [f'<div class="preview-container" id="container-{idx}">']
            html_parts.append(f'<img src="data:image/jpeg;base64,{get_base64_image(item["local_header"])}" />')
            if item.get("local_content") and item["local_content"] != item["local_header"]:
                html_parts.append(f'<img src="data:image/jpeg;base64,{get_base64_image(item["local_content"])}" />')
            for g in item.get("completed_gallery_pages", []):
                html_parts.append(f'<img src="data:image/jpeg;base64,{get_base64_image(g["local"])}" />')
            for g in item.get("in_progress_pages", []):
                html_parts.append(f'<img src="data:image/jpeg;base64,{get_base64_image(g["local"])}" />')
            html_parts.append('</div>')
            st.markdown("".join(html_parts), unsafe_allow_html=True)

# --- CREATIVEHUB REPORTS ---
st.divider()
st.subheader("📊 CreativeHub Reports")
st.caption(f"Full-page capture per tab from [{CREATIVEHUB_URL}]({CREATIVEHUB_URL})")

ch_col1, ch_col2 = st.columns([1, 4])
with ch_col1:
    if st.button("🚀 Run CreativeHub Capture"):
        st.session_state.ch_results = capture_creativehub_reports()
with ch_col2:
    if st.session_state.ch_results:
        if st.button("📤 Upload CreativeHub to Airtable", type="primary"):
            sync_creativehub_to_airtable(st.session_state.ch_results)

if st.session_state.ch_results:
    st.divider()
    ch_cols = st.columns(len(st.session_state.ch_results))
    for idx, item in enumerate(st.session_state.ch_results):
        with ch_cols[idx]:
            st.subheader(item['tab'])
            st.caption(f"[Tab Link]({item['tab_url']})")
            html_parts = [f'<div class="preview-container" id="ch-container-{idx}">']
            html_parts.append(f'<img src="data:image/jpeg;base64,{get_base64_image(item["local"])}" />')
            html_parts.append('</div>')
            st.markdown("".join(html_parts), unsafe_allow_html=True)
