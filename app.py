import streamlit as st
import asyncio
from playwright.sync_api import sync_playwright
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.image import MIMEImage
import os

# --- EMAIL CONFIGURATION ---
# It's best to use environment variables for security
SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587
SENDER_EMAIL = os.getenv("SENDER_EMAIL", "your-email@gmail.com")
SENDER_PASSWORD = os.getenv("SENDER_PASSWORD", "your-app-password")

def capture_airtable(url, output_path):
    """Captures a full-page screenshot of the Airtable link."""
    with sync_playwright() as p:
        # Airtable interfaces work best in Chromium
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        
        # Navigate and wait for the page to finish loading dynamic content
        st.info("Navigating to Airtable...")
        page.goto(url, wait_until="networkidle")
        
        # Optional: Wait a few extra seconds for charts/dashboards to animate
        page.wait_for_timeout(5000) 
        
        # Capture the full scrollable area
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