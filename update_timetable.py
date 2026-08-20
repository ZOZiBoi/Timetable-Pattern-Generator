#!/usr/bin/env python3
import urllib.request
import re
import os
import ssl
import subprocess

URL = "https://lhr.nu.edu.pk/fsc/studentForms/"
BASE_URL = "https://lhr.nu.edu.pk"
TIMETABLE_DIR = "timetable"
CONFIG_FILE = "config.py"

def main():
    print(f"Checking {URL} for new timetable...")
    
    # Ignore SSL certificate errors just in case
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    
    try:
        req = urllib.request.Request(URL, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, context=ctx) as response:
            html = response.read().decode('utf-8')
    except Exception as e:
        print(f"Error fetching URL: {e}")
        return

    # Find link matching FSC timetable xlsx
    # Example: href="/media/Resources/FSC_F26_TT_v1.0.4_11082026.xlsx" or href=/media/Resources/...
    match = re.search(r'href=["\']?(/media/Resources/FSC_[^\s"\'>]+\.xlsx)["\']?', html, re.IGNORECASE)
    if not match:
        print("Could not find a timetable link on the page.")
        return

    link = match.group(1)
    filename = os.path.basename(link)
    download_url = BASE_URL + link
    
    print(f"Found latest timetable: {filename}")
    
    # Read current config to see if we already have it
    current_filename = None
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, 'r') as f:
            content = f.read()
            config_match = re.search(r'TIMETABLE_FILENAME\s*=\s*["\']timetable/([^"\']+)["\']', content)
            if config_match:
                current_filename = config_match.group(1)
    
    if current_filename == filename:
        print("You already have the latest timetable. No update needed.")
        return
        
    print(f"New timetable found! (Current: {current_filename} -> New: {filename})")
    
    # Download the new file
    if not os.path.exists(TIMETABLE_DIR):
        os.makedirs(TIMETABLE_DIR)
        
    filepath = os.path.join(TIMETABLE_DIR, filename)
    print(f"Downloading {download_url} to {filepath}...")
    
    try:
        req = urllib.request.Request(download_url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, context=ctx) as response, open(filepath, 'wb') as out_file:
            out_file.write(response.read())
        print("Download complete.")
    except Exception as e:
        print(f"Error downloading file: {e}")
        return
        
    # Update config.py
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, 'r') as f:
            content = f.read()
        
        # Replace the TIMETABLE_FILENAME line
        new_content = re.sub(
            r'TIMETABLE_FILENAME\s*=\s*["\'].*?["\']',
            f'TIMETABLE_FILENAME = "timetable/{filename}"',
            content
        )
        
        with open(CONFIG_FILE, 'w') as f:
            f.write(new_content)
            
        print("Updated config.py successfully.")
        
        # Git commit and push
        print("Committing and pushing to git...")
        try:
            version_match = re.search(r'(v[\d\.]+)', filename, re.IGNORECASE)
            version_str = version_match.group(1) if version_match else filename
            subprocess.run(["git", "add", filepath, CONFIG_FILE], check=True)
            subprocess.run(["git", "commit", "-m", f"Update timetable to {version_str}"], check=True)
            subprocess.run(["git", "push"], check=True)
            print("Successfully pushed to git! This will trigger a deploy on Render.")
        except Exception as e:
            print(f"Error during git operations: {e}")
    else:
        print("config.py not found. Could not update the filename automatically.")

if __name__ == "__main__":
    main()
