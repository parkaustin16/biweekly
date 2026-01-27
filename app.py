import streamlit as st
import subprocess
import os
import requests
import cloudinary
import cloudinary.uploader
from playwright.sync_api import sync_playwright

# --- SETUP: BROWSER INSTALLATION ---
@st.cache_resource
def install_browser_binaries():
    subprocess.run(["playwright", "install", "chromium"])
    subprocess.run(["playwright", "install-deps"])

install_browser_binaries()

# --- CONFIGURATION: CLOUDINARY ---
# Add these to your Streamlit Secrets
cloudinary.config(
    cloud_name = st.secrets["CLOUDINARY_CLOUD_NAME"],
    api_key = st.secrets["CLOUDINARY_API_KEY"],
    api_secret = st.secrets["CLOUDINARY_API_SECRET"],
    secure = True
)

# --- HELPER FUNCTIONS ---

def capture_airtable(url, output_path):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={'width': 1920, 'height': 1080})
        page = context.new_page()
        page.set_default_timeout(60000) 
        
        st.info("📸 Capturing Airtable Interface...")
        page.goto(url, wait_until="load")
        
        # Wait for the interface to render
        try:
            page.wait_for_selector(".viewContainer", timeout=15000)
        except:
            page.wait_for_timeout(8000) 
            
        page.screenshot(path=output_path, full_page=True)
        browser.close()

def upload_to_cloudinary(image_path):
    """Uploads the screenshot to Cloudinary and returns the public URL."""
    response = cloudinary.uploader.upload(image_path, folder="airtable_captures")
    return response["secure_url"]

def send_to_airtable(public_url):
    """Tells Airtable to fetch the image from the Cloudinary URL."""
    url = f"https://api.airtable.com/v0/{st.secrets['BASE_ID']}/{st.secrets['TABLE_NAME']}"
    headers = {
        "Authorization": f"Bearer {st.secrets['AIRTABLE_TOKEN']}",
        "Content-Type": "application/json"
    }
    
    data = {
        "records": [
            {
                "fields": {
                    "Name": f"Capture {st.date_input('Today')}", 
                    "Attachment": [{"url": public_url}] # Ensure field name matches Airtable
                }
            }
        ]
    }
    
    response = requests.post(url, headers=headers, json=data)
    response.raise_for_status()
    return response.json()

# --- STREAMLIT UI ---
st.title("🖼️ Airtable to Cloudinary to Airtable")

target_url = st.text_input("Paste Airtable Interface URL:")

if st.button("Process Capture"):
    if target_url:
        try:
            filename = "capture_temp.png"
            
            with st.spinner("1/3: Screenshotting..."):
                capture_airtable(target_url, filename)
            
            with st.spinner("2/3: Uploading to Cloudinary..."):
                public_link = upload_to_cloudinary(filename)
            
            with st.spinner("3/3: Attaching to Airtable..."):
                send_to_airtable(public_link)
            
            st.success("✅ Done! Check your Airtable base.")
            st.image(filename)
            
            # Clean up local file
            if os.path.exists(filename):
                os.remove(filename)
                
        except Exception as e:
            st.error(f"Error: {e}")
