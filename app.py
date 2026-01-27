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
    """Ensures Chromium binaries are present. System deps are handled by packages.txt"""
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
        # 800x2700 viewport for the 2500 capture
        context = browser.new_context(viewport={'width': 800, 'height': 2700})
        page = context.new_page()
        
        st.info("🔗 Connecting...")
        page.goto(target_url, wait_until="domcontentloaded")
        
        # Optimized Header Extraction: Dynamic wait instead of flat 7s sleep
        try:
            # Look for h2 with the specific class or h1
            header_selector = 'h2.font-family-display-updated, h1, .interfaceTitle'
            header_locator = page.locator(header_selector).first
            header_locator.wait_for(state="visible", timeout=10000)
            
            raw_header = header_locator.inner_text()
            header_title = raw_header.split("|")[0].strip() if "|" in raw_header else raw_header.strip()
        except Exception as e:
            st.warning("Header load timed out, using default title.")

        for region in regions:
            status_placeholder = st.empty()
            status_placeholder.write(f"🔄 **{region}**...")
            
            try:
                tab_selector = page.locator(f'div[role="tab"]:has-text("{region}")')
                tab_selector.wait_for(state="visible", timeout=5000)
                tab_selector.click()
                
                # Reduced wait time: 3s is usually enough for data refresh on fast connections
                page.wait_for_timeout(3000) 
                
                # Snappier scroll sequence (reduced pauses)
                page.mouse.wheel(0, 2500)
                page.wait_for_timeout(800)
                page.mouse.wheel(0, -2500)
                page.wait_for_timeout(500)

                filename = f"{region.lower().replace(' ', '')}snap.png"
                page.screenshot(
                    path=filename,
                    clip={'x': 0, 'y': 0, 'width': 800, 'height': 2500}
                )

                # Upload to Cloudinary
                upload_res = cloudinary.uploader.upload(
                    filename, 
                    folder="airtableautomation",
                    public_id=f"snap{region.lower().replace(' ', '')}{capture_date.replace('-', '')}"
                )
                
                captured_data.append({
                    "region": region,
                    "url": upload_res["secure_url"],
                    "local_file": filename,
                    "date": capture_date,
                    "header_id": header_title
                })
                status_placeholder.write(f"✅ **{region}** ready.")
                
            except Exception as e:
                st.error(f"Error on {region}: {e}")

        browser.close()
    return captured_data

def sync_to_airtable(data_list):
    """Sends all captured images to Airtable as a single consolidated record with specific Cloud ID mapping."""
    url = f"https://api.airtable.com/v0/{st.secrets['BASE_ID']}/{st.secrets['TABLE_NAME']}"
    headers = {
        "Authorization": f"Bearer {st.secrets['AIRTABLE_TOKEN']}",
        "Content-Type": "application/json"
    }
    
    if not data_list: return None

    # Helper function to find a URL by region name
    def get_url(region_name):
        for item in data_list:
            if item["region"] == region_name:
                return item["url"]
        return ""

    all_attachments = [{"url": item["url"]} for item in data_list]
    
    # Mapping logic for specific Cloud ID fields
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
    
    payload = {
        "records": [{
            "fields": fields
        }]
    }

    response = requests.post(url, headers=headers, json=payload)
    if response.status_code == 200:
        st.success(f"🎉 Consolidated record created with Cloud ID mappings!")
    else:
        st.error(f"❌ Sync Error: {response.text}")
    return response.json()

# --- 4. USER INTERFACE ---

st.set_page_config(page_title="Airtable Regional Snap", layout="wide")
st.title("🗺️ Regional Snap")

url_input = st.text_input("Airtable Interface URL")

if st.button("🚀 Run Cycle"):
    if url_input:
        results = capture_regional_images(url_input)
        if results:
            sync_to_airtable(results)
            st.divider()
            cols = st.columns(len(results))
            for idx, item in enumerate(results):
                with cols[idx]:
                    st.image(item["local_file"], caption=item["region"])
                    if os.path.exists(item["local_file"]):
                        os.remove(item["local_file"])
