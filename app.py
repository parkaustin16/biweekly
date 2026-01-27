import streamlit as st
import asyncio
from playwright.sync_api import sync_playwright
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.image import MIMEImage
import os
import subprocess

# This function runs the playwright install command if it hasn't been run yet
@st.cache_resource
def install_browser_binaries():
    subprocess.run(["playwright", "install", "chromium"])

install_browser_binaries()

# Now import playwright and start your app logic
from playwright.sync_api import sync_playwright

# Check if chromium is already installed; if not, install it
if not os.path.exists("/home/appuser/.cache/ms-playwright/"):
    subprocess.run(["playwright", "install", "chromium"])
    subprocess.run(["playwright", "install-deps"])

# --- EMAIL CONFIGURATION ---
# It's best to use environment variables for security
SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587
SENDER_EMAIL = os.getenv("SENDER_EMAIL", "your-email@gmail.com")
SENDER_PASSWORD = os.getenv("SENDER_PASSWORD", "your-app-password")

def capture_airtable(url, output_path):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        # Increase the default timeout to 60 seconds
        context = browser.new_context(viewport={'width': 1920, 'height': 1080})
        page = context.new_page()
        page.set_default_timeout(60000) 
        
        st.info("Navigating to Airtable...")
        
        # 1. Change 'networkidle' to 'load' (much faster/more reliable)
        page.goto(url, wait_until="load")
        
        # 2. Wait for a specific Airtable element to ensure the UI is rendered
        # 'div[role="main"]' or '.sharedView' are common Airtable selectors
        try:
            page.wait_for_selector(".viewContainer", timeout=15000)
        except:
            # Fallback if the selector isn't found
            page.wait_for_timeout(5000) 
            
        # 3. Give it a brief moment for animations to settle
        page.wait_for_timeout(3000)
        
        # Capture
        page.screenshot(path=output_path, full_page=True)
        browser.close()

def send_email(recipient_list, image_path):
    """Sends the captured image as an attachment."""
    msg = MIMEMultipart()
    msg['Subject'] = "Automated Airtable Interface Export"
    msg['From'] = SENDER_EMAIL
    msg['To'] = ", ".join(recipient_list)

    # Email body
    text = MIMEText("Attached is the latest snapshot of the Airtable interface.")
    msg.attach(text)

    # Attach the image
    with open(image_path, 'rb') as f:
        img = MIMEImage(f.read())
        img.add_header('Content-Disposition', 'attachment', filename="airtable_capture.png")
        msg.attach(img)

    # Connect and send
    with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
        server.starttls()
        server.login(SENDER_EMAIL, SENDER_PASSWORD)
        server.send_message(msg)

# --- STREAMLIT UI ---
st.title("📸 Airtable Automated Capturer")
st.write("Enter an Airtable Interface link to capture and email it.")

target_url = st.text_input("Airtable Interface URL")
emails = st.text_area("Recipient Emails (comma separated)").split(",")

if st.button("Capture and Send"):
    if target_url and emails:
        try:
            filename = "capture.png"
            with st.spinner("Capturing full page..."):
                capture_airtable(target_url, filename)
            
            with st.spinner("Sending emails..."):
                send_email([e.strip() for e in emails], filename)
            
            st.success(f"Successfully sent to {len(emails)} recipients!")
            st.image(filename, caption="Preview of captured interface")
        except Exception as e:
            st.error(f"An error occurred: {e}")
    else:
        st.warning("Please provide both a URL and at least one email.")
