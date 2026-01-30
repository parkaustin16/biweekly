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
            status_placeholder.write(f"🔄 **{region}**: Capturing Summary & Galleries...")
            
            try:
                # 1. Navigate to Tab
                tab_selector = page.locator(f'div[role="tab"]:has-text("{region}")')
                tab_selector.wait_for(state="visible", timeout=5000)
                tab_selector.click()
                page.wait_for_timeout(4000) 

                # 2. DYNAMIC SIZING LOGIC (Main Report)
                clip_height = 2420 
                
                dynamic_js = """
                () => {
                    const findBottom = () => {
                        const headers = Array.from(document.querySelectorAll('h1, h2, h3, h4, div'));
                        const targetHeader = headers.find(h => 
                            h.innerText && h.innerText.trim().toLowerCase() === 'in progress'
                        );
                        if (!targetHeader) return null;
                        let container = targetHeader.closest('[role="region"]') || targetHeader.closest('.interfaceControl') || targetHeader.parentElement;
                        const boxes = Array.from(container.querySelectorAll('.summaryCard, [class*="record"], [class*="Cell"], [role="button"]'));
                        if (boxes.length > 0) {
                            const bottoms = boxes.map(b => b.getBoundingClientRect().bottom + window.scrollY);
                            return Math.max(...bottoms) + 40;
                        }
                        return targetHeader.getBoundingClientRect().bottom + window.scrollY + 400;
                    };
                    return findBottom();
                }
                """
                
                calculated_height = page.evaluate(dynamic_js)
                if calculated_height and calculated_height > 100:
                    clip_height = min(int(calculated_height), 3400) 
                
                # Scroll to load main area
                page.mouse.wheel(0, clip_height)
                page.wait_for_timeout(1000)
                page.mouse.wheel(0, -clip_height)
                page.wait_for_timeout(800)

                # Capture Main Summary
                main_filename = f"{region.lower().replace(' ', '')}_main.png"
                page.screenshot(path=main_filename, clip={'x': 0, 'y': 0, 'width': 800, 'height': clip_height})

                upload_res = cloudinary.uploader.upload(
                    main_filename, 
                    folder="airtableautomation",
                    public_id=f"{region.lower().replace(' ', '')}_main_{capture_date.replace('-', '')}"
                )
                
                region_entry = {
                    "region": region,
                    "url": upload_res["secure_url"],
                    "local_file": main_filename,
                    "date": capture_date,
                    "header_id": header_title,
                    "height": clip_height,
                    "galleries": [] 
                }

                # 3. UPDATED GALLERY CAPTURE LOGIC (Skip for All Regions)
                if region != "All Regions":
                    gallery_count = 1
                    
                    while True:
                        status_placeholder.write(f"🔄 **{region}**: Gallery Page {gallery_count}...")
                        
                        # Find gallery dimensions and pagination state
                        gallery_js = """
                        () => {
                            const findGallery = () => {
                                const labels = Array.from(document.querySelectorAll('div, h1, h2, h3, h4'));
                                const galHeader = labels.find(h => h.innerText && h.innerText.includes('Completed Request Gallery'));
                                if (!galHeader) return null;
                                
                                const container = galHeader.closest('.interfaceControl') || galHeader.parentElement;
                                const rect = container.getBoundingClientRect();
                                
                                // Look for the "Next" button div within the gallery container
                                const nextBtn = container.querySelector('div[role="button"]:has-text("Next")');
                                
                                // In Airtable, if the button is missing or has certain classes, it's the last page
                                const isLastPage = !nextBtn || nextBtn.classList.contains('opacity-low') || nextBtn.getAttribute('aria-disabled') === 'true';
                                
                                return {
                                    x: Math.floor(rect.left),
                                    y: Math.floor(rect.top + window.scrollY),
                                    width: Math.floor(rect.width),
                                    height: Math.floor(rect.height),
                                    isLastPage: isLastPage
                                };
                            };
                            return findGallery();
                        }
                        """
                        gal_info = page.evaluate(gallery_js)
                        
                        if not gal_info:
                            status_placeholder.write(f"⚠️ **{region}**: Gallery section not found.")
                            break
                        
                        # Ensure gallery is loaded
                        page.mouse.wheel(0, gal_info['y'] - 100)
                        page.wait_for_timeout(2000)

                        # Capture current gallery page
                        gal_filename = f"{region.lower().replace(' ', '')}_gal_{gallery_count}.png"
                        page.screenshot(
                            path=gal_filename,
                            clip={
                                'x': 0, 
                                'y': gal_info['y'], 
                                'width': 800, 
                                'height': gal_info['height']
                            }
                        )
                        
                        gal_upload = cloudinary.uploader.upload(
                            gal_filename,
                            folder="airtableautomation",
                            public_id=f"{region.lower().replace(' ', '')}_gal{gallery_count}_{capture_date.replace('-', '')}"
                        )
                        
                        region_entry["galleries"].append({
                            "local": gal_filename,
                            "url": gal_upload["secure_url"]
                        })

                        # Pagination Logic
                        if gal_info['isLastPage']:
                            break

                        # Click Next button using the specific div role selector
                        next_btn_selector = 'div[role="button"]:has-text("Next")'
                        next_btn_locator = page.locator(next_btn_selector).first
                        
                        if next_btn_locator.is_visible():
                            next_btn_locator.click()
                            page.wait_for_timeout(3500) # Wait for gallery transition
                            gallery_count += 1
                        else:
                            break
                            
                        if gallery_count > 15: break 

                captured_data.append(region_entry)
                status_placeholder.write(f"✅ **{region}** captured.")
                
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

    all_attachments = []
    for item in data_list:
        all_attachments.append({"url": item["url"]})
        for gal in item.get("galleries", []):
            all_attachments.append({"url": gal["url"]})
    
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
        st.success(f"🎉 Consolidated record created with {len(all_attachments)} images!")
    else:
        st.error(f"❌ Sync Error: {response.text}")
    return response.json()

# --- 4. USER INTERFACE ---

st.set_page_config(page_title="Airtable Bi-Weekly Report Capture", layout="wide")
st.title("🗺️ Bi-Weekly Report Capture")
st.caption("Captures Summary + Multi-page Completed Request Galleries.")

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
            
            # Display results in columns
            cols = st.columns(len(results))
            for idx, item in enumerate(results):
                with cols[idx]:
                    # Main Summary
                    st.subheader(item["region"])
                    st.image(item["local_file"], caption=f"Summary ({item['height']}px)")
                    if os.path.exists(item["local_file"]):
                        os.remove(item["local_file"])
                    
                    # Gallery Pages
                    for g_idx, gal in enumerate(item.get("galleries", [])):
                        st.image(gal["local"], caption=f"Gallery Page {g_idx+1}")
                        if os.path.exists(gal["local"]):
                            os.remove(gal["local"])
