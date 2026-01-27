import streamlit as st
import subprocess
import os
import requests
import cloudinary
import cloudinary.uploader
from playwright.sync_api import sync_playwright

# --- 1. CLOUD ENVIRONMENT SETUP ---
@st.cache_resource
def install_browser_binaries():
    """Ensures Chromium and Linux dependencies are present on Streamlit Cloud."""
    try:
        subprocess.run(["playwright", "install", "chromium"], check=True)
        subprocess.run(["playwright", "install-deps"], check=True)
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

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        # Large viewport to ensure the 2400px height is physically rendered
        context = browser.new_context(viewport={'width': 1200, 'height': 2600})
        page = context.new_page()
        
        st.info("🔗 Connecting to Airtable Interface...")
        page.goto(target_url, wait_until="load")
        page.wait_for_timeout(5000) # Initial soak time

        for region in regions:
            status_text = st.empty()
            status_text.write(f"🔄 Navigating to **{region}**...")
            
            try:
                # Click the tab by its text name
                tab = page.get_by_role("button", name=region, exact=True)
                tab.click()
                
                # Wait for charts/data to refresh and animations to settle
                page.wait_for_timeout(5000) 
                
                # Scroll down and back up to trigger any lazy-loading elements
                page.mouse.wheel(0, 2000)
                page.wait_for_timeout(1000)
                page.mouse.wheel(0, -2000)
                page.wait_for_timeout(1000)

                # Capture specific 800x2400 area
                filename = f"{region.lower()}_snap.png"
                page.screenshot(
                    path=filename,
                    clip={'x': 0, 'y': 0, 'width': 800, 'height': 2400}
                )

                # Upload to Cloudinary
                status_text.write(f"☁️ Uploading **{region}** to Cloudinary...")
                upload_res = cloudinary.uploader.upload(filename, folder="airtable_automation")
                
                captured_data.append({
                    "region": region,
                    "url": upload_res["secure_url"],
                    "local_file": filename
                })
                
            except Exception as e:
                st.error(f"Could not capture {region}: {e}")

        browser.close()
    return captured_data

def sync_to_airtable(data_list):
    """Sends all captured images to Airtable as new records."""
    url = f"https://api.airtable.com/v0/{st.secrets['BASE_ID']}/{st.secrets['TABLE_NAME']}"
    headers = {
        "Authorization": f"Bearer {st.secrets['AIRTABLE_TOKEN']}",
        "Content-Type": "application/json"
    }
    
    records = []
    for item in data_list:
        records.append({
            "fields": {
                "Region": item["region"],  # Ensure this field exists in Airtable
                "Attachments": [{"url": item["url"]}] # Ensure this is an 'Attachment' field
            }
        })

    response = requests.post(url, headers=headers, json={"records": records})
    
    if response.status_code == 200:
        st.success(f"✅ Successfully created {len(records)} records in Airtable!")
    else:
        st.error(f"❌ Airtable Sync Error (422/401): {response.text}")
    
    return response.json()

# --- 4. USER INTERFACE ---

st.set_page_config(page_title="Airtable Regional Snap", layout="wide")
st.title("🗺️ Regional Interface Automation")
st.write("This tool cycles through regional tabs, captures an 800x2400 snapshot, and syncs to Airtable via Cloudinary.")

url_input = st.text_input("Airtable Interface URL", placeholder="https://airtable.com/...")

if st.button("🚀 Run Full Cycle"):
    if url_input:
        # Step 1: Capture & Cloudinary
        results = capture_regional_images(url_input)
        
        if results:
            # Step 2: Airtable Sync
            with st.spinner("Finalizing Airtable records..."):
                sync_to_airtable(results)
            
            # Step 3: Gallery Preview
            st.divider()
            st.subheader("Latest Captures")
            cols = st.columns(len(results))
            for idx, item in enumerate(results):
                with cols[idx]:
                    st.image(item["local_file"], caption=item["region"])
                    # Clean up
                    if os.path.exists(item["local_file"]):
                        os.remove(item["local_file"])
    else:
        st.warning("Please enter a valid Airtable Interface URL.")
