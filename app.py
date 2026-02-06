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
        # UPDATED: Increased viewport width to 3840px (double the previous 1920px)
        # We maintain device_scale_factor=2 for high-density rendering
        context = browser.new_context(
            viewport={'width': 3840, 'height': 5000},
            device_scale_factor=2 
        )
        page = context.new_page()
        
        st.info("🔗 Connecting to Airtable Interface...")
        page.goto(target_url, wait_until="networkidle")
        
        # --- CLEANUP ---
        page.evaluate("""
            () => {
                const removeSelectors = [
                    '#onetrust-banner-sdk', 
                    '.onetrust-pc-dark-filter',
                    '[id*="cookie"]', 
                    '[class*="cookie"]',
                    '.banner-content',
                    'header.flex.flex-none.items-center.width-full',
                    '.flex.items-center.py2.px2-and-half.border-bottom'
                ];
                removeSelectors.forEach(selector => {
                    const elements = document.querySelectorAll(selector);
                    elements.forEach(el => el.remove());
                });
                // Force a larger zoom if needed to fill the space
                document.body.style.zoom = "1.2"; 
            }
        """)
        page.wait_for_timeout(1000)

        try:
            header_selector = 'h2.font-family-display-updated, h1, .interfaceTitle'
            header_locator = page.locator(header_selector).first
            header_locator.wait_for(state="visible", timeout=10000)
            raw_header = header_locator.inner_text()
            header_title = raw_header.split("|")[0].strip() if "|" in raw_header else raw_header.strip()
        except Exception:
            pass 

        for region in regions:
            status_placeholder = st.empty()
            status_placeholder.write(f"🔄 **{region}**: In Progress...")
            
            try:
                tab_selector = page.locator(f'div[role="tab"]:has-text("{region}")')
                tab_selector.wait_for(state="visible", timeout=5000)
                tab_selector.click()
                
                page.evaluate("window.scrollTo(0, 1000)")
                page.wait_for_timeout(1000)
                page.evaluate("window.scrollTo(0, 0)")
                page.wait_for_timeout(2000)

                # Hide galleries for main summary
                page.evaluate("""
                    () => {
                        const hideElements = (labelText) => {
                            const el = document.querySelector(`[aria-label*="${labelText}"]`);
                            if (el) {
                                el.style.display = 'none';
                                const container = el.closest('.width-full.rounded-big');
                                if (container) container.style.display = 'none';
                            }
                        };
                        hideElements("Completed Request Gallery");
                        hideElements("In Progress");
                    }
                """)

                # UPDATED: Layout calculation optimized for 2x size
                calculated_layout = page.evaluate("""
                () => {
                    const mainContent = document.querySelector('.interfaceContent') || document.body;
                    const rect = mainContent.getBoundingClientRect();
                    
                    const headers = Array.from(document.querySelectorAll('h1, h2, h3, h4, div'));
                    const target = headers.find(h => h.innerText && h.innerText.toLowerCase().includes('master banner usage breakdown'));
                    
                    let bottom = 3000;
                    if(target) {
                        const sectionContainer = target.closest('.width-full.rounded-big') || target.closest('[role="region"]') || target.parentElement;
                        const items = Array.from(sectionContainer.querySelectorAll('.chartContainer, .legend, svg, canvas, [role="listitem"], .recordList'));
                        
                        if (items.length > 0) {
                            const bottoms = items.map(el => el.getBoundingClientRect().bottom + window.scrollY);
                            bottom = Math.max(...bottoms) + 120; 
                        } else {
                            bottom = sectionContainer.getBoundingClientRect().bottom + window.scrollY + 50;
                        }
                    }

                    return {
                        x: Math.max(0, rect.left),
                        y: 0,
                        width: rect.width > 1000 ? rect.width : 2200, // Doubled fallback width
                        height: Math.floor(bottom)
                    };
                }
                """)

                safe_region = region.lower().replace(' ', '-')
                main_filename = f"{safe_region}-main.jpg"
                
                page.screenshot(
                    path=main_filename, 
                    clip=calculated_layout,
                    type="jpeg",
                    quality=100 # Set to max quality
                )

                safe_date = capture_date.replace('-', '')
                upload_res = cloudinary.uploader.upload(
                    main_filename, 
                    folder="airtableautomation",
                    public_id=f"{safe_region}-main-{safe_date}",
                    fetch_format="auto",
                    quality="auto:best" # Changed from good to best
                )
                
                region_entry = {
                    "region": region,
                    "url": upload_res["secure_url"],
                    "local_file": main_filename,
                    "date": capture_date,
                    "header_id": header_title,
                    "in_progress_pages": [],
                    "completed_gallery_pages": [] 
                }

                def capture_paged_gallery(gallery_label, storage_key):
                    page.evaluate(f"""
                        () => {{
                            const el = document.querySelector('[aria-label*="{gallery_label}"]');
                            if (el) {{
                                el.style.display = 'block';
                                const container = el.closest('.width-full.rounded-big');
                                if (container) container.style.display = 'block';
                            }
                        }}
                    """)
                    
                    page_idx = 1
                    while True:
                        container_js = f"""
                        () => {{
                            const el = document.querySelector('[aria-label*="{gallery_label}"]');
                            if (!el) return null;
                            const rect = el.getBoundingClientRect();
                            return {{
                                x: Math.floor(rect.left),
                                y: Math.floor(rect.top + window.scrollY),
                                width: Math.floor(rect.width),
                                height: Math.floor(rect.height)
                            }};
                        }}
                        """
                        gal_info = page.evaluate(container_js)
                        if not gal_info: break
                        
                        page.mouse.wheel(0, gal_info['y'] - 100)
                        page.wait_for_timeout(1000)

                        safe_label = gallery_label.lower().replace(' ', '-')
                        gal_filename = f"{safe_region}-{safe_label}-{page_idx}.jpg"
                        page.screenshot(
                            path=gal_filename, 
                            clip=gal_info,
                            type="jpeg",
                            quality=100 # Max gallery quality
                        )
                        
                        gal_upload = cloudinary.uploader.upload(
                            gal_filename,
                            folder="airtableautomation",
                            public_id=f"{safe_region}-{safe_label}{page_idx}-{safe_date}",
                            fetch_format="auto",
                            quality="auto:best"
                        )

                        region_entry[storage_key].append({
                            "local": gal_filename,
                            "url": gal_upload["secure_url"]
                        })

                        next_btn = page.locator(f'[aria-label*="{gallery_label}"] div[role="button"]:has(path[d*="m4.64.17"])').first
                        if next_btn.is_visible():
                            is_disabled = next_btn.evaluate("el => el.getAttribute('aria-disabled') === 'true' || window.getComputedStyle(el).opacity === '0.5'")
                            if not is_disabled:
                                next_btn.click()
                                page_idx += 1
                                page.wait_for_timeout(1500)
                            else: break
                        else: break
                        if page_idx > 5: break

                if region != "All Regions":
                    capture_paged_gallery("Completed Request Gallery", "completed_gallery_pages")
                    capture_paged_gallery("In Progress", "in_progress_pages")

                captured_data.append(region_entry)
                status_placeholder.write(f"✅ **{region}** captured at 2x size.")
                
            except Exception as e:
                st.error(f"Error on {region}: {e}")

        browser.close()
    return captured_data

# ... rest of the sync_to_airtable and UI logic remains the same ...
