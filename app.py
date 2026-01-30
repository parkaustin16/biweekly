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

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={'width': 1920, 'height': 5000})
        page = context.new_page()
        
        st.info("🔗 Connecting to Airtable Interface...")
        page.goto(target_url, wait_until="networkidle")
        
        # --- REMOVE COOKIES & BANNERS ---
        page.evaluate("""
            () => {
                const removeSelectors = ['#onetrust-banner-sdk', '.onetrust-pc-dark-filter', '[id*="cookie"]', '.banner-content'];
                removeSelectors.forEach(s => {
                    const el = document.querySelector(s);
                    if (el) el.remove();
                });
            }
        """)
        page.wait_for_timeout(1000)

        for region in regions:
            status_placeholder = st.empty()
            status_placeholder.write(f"🔄 **{region}**: Processing sections...")
            
            try:
                # 1. Navigation
                tab_selector = page.locator(f'div[role="tab"]:has-text("{region}")')
                tab_selector.wait_for(state="visible", timeout=10000)
                tab_selector.click()
                page.wait_for_timeout(4000) 

                safe_region = region.lower().replace(' ', '-')
                safe_date = capture_date.replace('-', '')

                # --- 2. MAIN SUMMARY (Clipped to include full Master Banner Usage Breakdown card) ---
                summary_clip_js = """
                () => {
                    const h2s = Array.from(document.querySelectorAll('h2'));
                    const target = h2s.find(h => h.innerText.includes('Master Banner Usage Breakdown'));
                    if (!target) return 2400; // Fallback height
                    
                    // Look for the specific container described in the request
                    const card = target.closest('div[role="button"]') || target.closest('.interfaceControl') || target.parentElement;
                    const rect = card.getBoundingClientRect();
                    return Math.floor(rect.bottom + window.scrollY + 25);
                }
                """
                summary_height = page.evaluate(summary_clip_js)
                main_filename = f"{safe_region}-main.jpg"
                
                page.screenshot(
                    path=main_filename, 
                    clip={'x': 0, 'y': 0, 'width': 1920, 'height': summary_height},
                    type="jpeg",
                    quality=55
                )

                main_upload = cloudinary.uploader.upload(
                    main_filename, 
                    folder="airtableautomation",
                    public_id=f"{safe_region}-main-{safe_date}",
                    fetch_format="auto",
                    quality="auto:eco"
                )
                
                region_entry = {
                    "region": region,
                    "url": main_upload["secure_url"],
                    "local_file": main_filename,
                    "date": capture_date,
                    "galleries": [] 
                }

                # --- 3. PAGINATED SECTION CAPTURE (Uniform Logic for both) ---
                sections_to_capture = ["Completed Request Gallery", "In Progress"]
                
                for section_name in sections_to_capture:
                    page_num = 1
                    while page_num <= 5: 
                        # Use the "width-full rounded-big" container logic for precise section shots
                        section_js = f"""
                        () => {{
                            const h2s = Array.from(document.querySelectorAll('h2'));
                            const header = h2s.find(h => h.innerText.trim() === "{section_name}");
                            if (!header) return null;
                            const container = header.closest('.width-full.rounded-big') || header.parentElement.parentElement;
                            const rect = container.getBoundingClientRect();
                            return {{
                                x: Math.floor(rect.left),
                                y: Math.floor(rect.top + window.scrollY),
                                width: Math.floor(rect.width),
                                height: Math.floor(rect.height)
                            }};
                        }}
                        """
                        sec_info = page.evaluate(section_js)
                        if not sec_info:
                            break

                        page.mouse.wheel(0, sec_info['y'] - 100)
                        page.wait_for_timeout(1500)

                        sec_slug = section_name.lower().replace(' ', '-')
                        gal_filename = f"{safe_region}-{sec_slug}-{page_num}.jpg"
                        
                        page.screenshot(
                            path=gal_filename, 
                            clip=sec_info,
                            type="jpeg",
                            quality=55
                        )
                        
                        gal_upload = cloudinary.uploader.upload(
                            gal_filename,
                            folder="airtableautomation",
                            public_id=f"{safe_region}-{sec_slug}-{page_num}-{safe_date}",
                            fetch_format="auto",
                            quality="auto:eco"
                        )
                        
                        region_entry["galleries"].append({
                            "local": gal_filename,
                            "url": gal_upload["secure_url"],
                            "label": f"{section_name} P{page_num}"
                        })

                        # Pagination Detection
                        next_btn_check_js = f"""
                        () => {{
                            const h2s = Array.from(document.querySelectorAll('h2'));
                            const header = h2s.find(h => h.innerText.trim() === "{section_name}");
                            if (!header) return false;
                            const container = header.closest('.interfaceControl') || header.parentElement.parentElement;
                            const buttons = Array.from(container.querySelectorAll('div[role="button"]'));
                            const btn = buttons.find(b => b.innerText.includes('Next'));
                            
                            return (btn && 
                                    btn.getAttribute('aria-disabled') !== 'true' && 
                                    window.getComputedStyle(btn).display !== 'none' &&
                                    window.getComputedStyle(btn).visibility !== 'hidden');
                        }}
                        """
                        has_next = page.evaluate(next_btn_check_js)
                        
                        if has_next:
                            try:
                                section_container = page.locator(f"div.interfaceControl:has(h2:text-is('{section_name}'))").first
                                next_button = section_container.locator('div[role="button"]:has-text("Next")').first
                                
                                next_button.scroll_into_view_if_needed()
                                # Use force=True to bypass any invisible overlay issues
                                next_button.click(force=True, timeout=10000)
                                
                                page.wait_for_timeout(3000)
                                page_num += 1
                            except Exception as click_err:
                                st.warning(f"Stopping {section_name} at page {page_num}: {click_err}")
                                break
                        else:
                            break

                captured_data.append(region_entry)
                status_placeholder.write(f"✅ **{region}** captures complete.")
                
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
    
    records_to_create = []
    for item in data_list:
        record_attachments = [{"url": item["url"]}]
        for gal in item.get("galleries", []):
            record_attachments.append({"url": gal["url"]})
            
        fields = {
            "Type": f"Consolidated Report | {item['region']}",
            "Date": item["date"],
            "Attachments": record_attachments,
            "Cloud ID": item["url"]
        }
        
        gallery_items = item.get("galleries", [])
        for i in range(1, 4):
            if len(gallery_items) >= i:
                fields[f"Gallery {i}"] = gallery_items[i-1]["url"]
        
        records_to_create.append({"fields": fields})

    payload = {"records": records_to_create}
    response = requests.post(url, headers=headers, json=payload)
    
    if response.status_code == 200:
        st.success(f"🎉 Successfully synced records!")
        st.session_state.capture_results = None
    else:
        st.error(f"❌ Sync Error: {response.text}")

# --- 4. USER INTERFACE ---

st.set_page_config(page_title="Airtable Automation Capture", layout="wide")
st.title("🗺️ Bi-Weekly Report Automation")

if 'capture_results' not in st.session_state:
    st.session_state.capture_results = None

url_input = st.text_input(
    "Airtable Interface URL",
    value="https://airtable.com/appyOEewUQye37FCb/shr9NiIaM2jisKHiK?tTPqb=sfsTkRwjWXEAjyRGj",
    key="url_input_v21"
)

col1, col2 = st.columns([1, 4])

with col1:
    if st.button("🚀 Run Capture", key="run_btn"):
        if url_input:
            st.session_state.capture_results = capture_regional_images(url_input)

with col2:
    if st.session_state.capture_results:
        if st.button("📤 Upload to Airtable", key="upload_btn", type="primary"):
            sync_to_airtable(st.session_state.capture_results)

if st.session_state.capture_results:
    st.divider()
    for item in st.session_state.capture_results:
        with st.expander(f"Region: {item['region']}", expanded=True):
            c1, c2 = st.columns([1, 1])
            with c1:
                st.image(item["local_file"], caption="Main Summary")
            with c2:
                g_cols = st.columns(2)
                for idx, gal in enumerate(item.get("galleries", [])):
                    with g_cols[idx % 2]:
                        st.image(gal["local"], caption=gal["label"])
