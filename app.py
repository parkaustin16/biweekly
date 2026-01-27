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

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        # Increased height to 2700 to comfortably render the 2500px capture
        context = browser.new_context(viewport={'width': 800, 'height': 2700})
        page = context.new_page()
        
        st.info("🔗 Connecting to Airtable Interface...")
        page.goto(target_url, wait_until="load")
        page.wait_for_timeout(7000) 

        for region in regions:
            status_placeholder = st.empty()
            status_placeholder.write(f"🔄 Navigating to **{region}**...")
            
            try:
                # Direct target based on HTML structure: role="tab" containing region text
                tab_selector = page.locator(f'div[role="tab"]:has-text("{region}")')
                
                # Wait for tab and click
                tab_selector.wait_for(state="visible", timeout=15000)
                tab_selector.click()
                
                # Wait for data to refresh
                page.wait_for_timeout(6000) 
                
                # Scroll to trigger lazy-loading for the tall portrait format (now 2500)
                page.mouse.wheel(0, 2460)
                page.wait_for_timeout(2000)
                page.mouse.wheel(0, -2460)
                page.wait_for_timeout(1000)

                # Capture exactly 800x2500
                filename = f"{region.lower().replace(' ', '_')}_snap.png"
                page.screenshot(
                    path=filename,
                    clip={'x': 0, 'y': 0, 'width': 800, 'height': 2460}
                )

                # Upload to Cloudinary
                status_placeholder.write(f"☁️ Uploading **{region}** to Cloudinary...")
                upload_res = cloudinary.uploader.upload(
                    filename, 
                    folder="airtable_automation",
                    public_id=f"snap_{region.lower().replace(' ', '_')}_{capture_date}"
                )
                
                captured_data.append({
                    "region": region,
                    "url": upload_res["secure_url"],
                    "local_file": filename,
                    "date": capture_date
                })
                status_placeholder.write(f"✅ **{region}** captured.")
                
            except Exception as e:
                st.error(f"Could not capture {region}: {e}")

        browser.close()
    return captured_data

def sync_to_airtable(data_list):
    """Sends all captured images to Airtable as new records including the capture date."""
    url = f"https://api.airtable.com/v0/{st.secrets['BASE_ID']}/{st.secrets['TABLE_NAME']}"
    headers = {
        "Authorization": f"Bearer {st.secrets['AIRTABLE_TOKEN']}",
        "Content-Type": "application/json"
    }
    
    records = []
    for item in data_list:
        records.append({
            "fields": {
                "Region": item["region"], 
                "Attachments": [{"url": item["url"]}],
                "Date": item["date"]  # Ensure this field (Date type) exists in Airtable
            }
        })

    response = requests.post(url, headers=headers, json={"records": records})
    if response.status_code == 200:
        st.success(f"🎉 Successfully created {len(records)} records in Airtable!")
    else:
        st.error(f"❌ Airtable Sync Error: {response.text}")
    return response.json()

# --- 4. USER INTERFACE ---

st.set_page_config(page_title="Airtable Regional Snap", layout="wide")
st.title("🗺️ Regional Interface Automation")

url_input = st.text_input("Airtable Interface URL", placeholder="https://airtable.com/app...")

if st.button("🚀 Run Full Cycle"):
    if url_input:
        results = capture_regional_images(url_input)
        if results:
            with st.spinner("Syncing to Airtable..."):
                sync_to_airtable(results)
            
            st.divider()
            st.subheader("Results Preview")
            cols = st.columns(len(results))
            for idx, item in enumerate(results):
                with cols[idx]:
                    st.image(item["local_file"], caption=f"{item['region']} ({item['date']})")
                    if os.path.exists(item["local_file"]):
                        os.remove(item["local_file"])
    else:
        st.warning("Please enter a valid Airtable Interface URL.")
