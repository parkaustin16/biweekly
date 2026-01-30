import st
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
        # Use a large initial height to allow for calculations
        context = browser.new_context(viewport={'width': 800, 'height': 3500})
        page = context.new_page()
        
        st.info("🔗 Connecting to Airtable Interface...")
        page.goto(target_url, wait_until="networkidle")
        
        # Header Extraction
        try:
            header_selector = 'h2.font-family-display-updated, h1, .interfaceTitle'
            header_locator = page.locator(header_selector).first
            header_locator.wait_for(state="visible", timeout=10000)
            raw_header = header_locator.inner_text()
            header_title = raw_header.split("|")[0].strip() if "|" in raw_header else raw_header.strip()
        except Exception:
            st.warning("Header load timed out, using default title.")

        for region in regions:
            status_placeholder = st.empty()
            status_placeholder.write(f"🔄 **{region}**: Finding dynamic boundary...")
            
            try:
                # 1. Navigate to Tab
                tab_selector = page.locator(f'div[role="tab"]:has-text("{region}")')
                tab_selector.wait_for(state="visible", timeout=5000)
                tab_selector.click()
                page.wait_for_timeout(3000) 

                # 2. DYNAMIC SIZING LOGIC
                # We look for the container that has "In Progress" and find its bounding box
                # We then find the last 'record count' box within that specific section
                clip_height = 2420 # Default fallback
                
                dynamic_js = """
                () => {
                    // 1. Find the section containing "In Progress"
                    const headers = Array.from(document.querySelectorAll('h2, h3, .sectionHeader, div'));
                    const targetHeader = headers.find(h => h.innerText && h.innerText.includes('In Progress'));
                    
                    if (!targetHeader) return null;

                    // 2. Find the parent container or the next sibling structure that holds the data
                    let container = targetHeader.closest('.interfaceControl') || targetHeader.parentElement;
                    
                    // 3. Find all boxes that usually contain record counts (often have specific Airtable classes)
                    // We look for elements that look like summary cards or grid items
                    const boxes = container.querySelectorAll('.summaryCard, [class*="record"], [class*="Cell"]');
                    
                    if (boxes.length > 0) {
                        const lastBox = boxes[boxes.length - 1];
                        const rect = lastBox.getBoundingClientRect();
                        return rect.bottom + window.scrollY + 20; // Add 20px padding
                    }
                    
                    // Fallback: just use the bottom of the "In Progress" container
                    return targetHeader.getBoundingClientRect().bottom + window.scrollY + 500;
                }
                """
                
                calculated_height = page.evaluate(dynamic_js)
                if calculated_height:
                    clip_height = min(int(calculated_height), 3400) # Cap at 3400 to prevent errors
                
                # 3. Optimized Scroll to trigger lazy loading for the calculated area
                page.mouse.wheel(0, clip_height)
                page.wait_for_timeout(1000)
                page.mouse.wheel(0, -clip_height)
                page.wait_for_timeout(500)

                # 4. Capture
                filename = f"{region.lower().replace(' ', '')}snap.png"
                page.screenshot(
                    path=filename,
                    clip={'x': 0, 'y': 0, 'width': 800, 'height': clip_height}
                )

                # 5. Upload to Cloudinary
                upload_res = cloudinary.uploader.upload(
                    filename, 
                    folder="airtableautomation",
                    public_id=f"{region.lower().replace(' ', '')}{capture_date.replace('-', '')}"
                )
                
                captured_data.append({
                    "region": region,
                    "url": upload_res["secure_url"],
                    "local_file": filename,
                    "date": capture_date,
                    "header_id": header_title,
                    "height": clip_height
                })
                status_placeholder.write(f"✅ **{region}** captured at {clip_height}px height.")
                
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

    def get_url(region_name):
        for item in data_list:
            if item["region"] == region_name:
                return item["url"]
        return ""

    all_attachments = [{"url": item["url"]} for item in data_list]
    
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
    
    payload = {"records": [{"fields": fields}]}

    response = requests.post(url, headers=headers, json=payload)
    if response.status_code == 200:
        st.success(f"🎉 Consolidated record created with Cloud ID mappings!")
    else:
        st.error(f"❌ Sync Error: {response.text}")
    return response.json()

# --- 4. USER INTERFACE ---

st.set_page_config(page_title="Airtable Bi-Weekly Report Capture", layout="wide")
st.title("🗺️ Bi-Weekly Report Capture")
st.caption("Now with dynamic cropping based on 'In Progress' section contents.")

url_input = st.text_input(
    "Airtable Interface URL",
    value="https://airtable.com/appyOEewUQye37FCb/shr9NiIaM2jisKHiK?tTPqb=sfsTkRwjWXEAjyRGj"
)

if st.button("🚀 Run Dynamic Capture & Sync"):
    if url_input:
        results = capture_regional_images(url_input)
        if results:
            sync_to_airtable(results)
            st.divider()
            cols = st.columns(len(results))
            for idx, item in enumerate(results):
                with cols[idx]:
                    st.image(item["local_file"], caption=f"{item['region']} ({item['height']}px)")
                    if os.path.exists(item["local_file"]):
                        os.remove(item["local_file"])
