"""
REFERENCE ONLY — existing production download script as of 2026-03-23.
Do not modify this file. It is preserved here for context during Phase 1 development.
Source: Ubuntu streaming PC at WDBX station.

Known limitations (addressed by the new toolbox):
- URL construction only; no archive listing scrape; restart fragments missed
- No fragment reassembly
- No deduplication
- No schedule change detection
- No alerting beyond log file
- Cron-driven (cron config not included here)
"""

import requests
import time
from datetime import datetime, timedelta
import calendar
import os
import logging
import shutil

# CONFIGURATION - Toggle between local and network saving
SAVE_TO_NETWORK = True  # Set to True for network drive, False for local only
SAVE_TO_BOTH = False    # Set to True to save to both locations

# Network share configuration
NETWORK_SHARE = "//192.168.2.185/Add to Share"
NETWORK_MOUNT_POINT = "/mnt/wdbx-share/Shows/AutoArchive"  # Full path including subdirectories

class Show:
    def __init__(self, day_of_week, show_time, show_name, full_show_name, download_flag):
        self.day_of_week = day_of_week
        self.show_time = datetime.strptime(show_time, '%H%M%S')
        self.show_name = show_name
        self.full_show_name = full_show_name
        self.download_flag = download_flag

def generate_url(show):
    base_url = "https://archive.wdbx.org/mp3/wdbx_"
    date = (datetime.now() - timedelta(days=1)).strftime('%y%m%d')
    time = show.show_time.strftime('%H%M%S')
    show_name = show.show_name
    return base_url + date + "_" + time + show_name + ".mp3"

def unsanitize_text(text):
    return text.replace("&#39;", "'")

def check_network_mount():
    """Check if network drive is accessible and mounted"""
    # Check if the base mount exists
    base_mount = "/mnt/wdbx-share"
    if not os.path.exists(base_mount):
        logging.error(f"Mount point {base_mount} does not exist!")
        return False

    # Check if Shows/AutoArchive path exists, create if not
    if not os.path.exists(NETWORK_MOUNT_POINT):
        try:
            logging.info(f"Creating Shows/AutoArchive directory at {NETWORK_MOUNT_POINT}")
            os.makedirs(NETWORK_MOUNT_POINT, exist_ok=True)
        except Exception as e:
            logging.error(f"Failed to create {NETWORK_MOUNT_POINT}: {e}")
            return False

    try:
        # Test if we can write to the location
        test_file = os.path.join(NETWORK_MOUNT_POINT, '.write_test')
        with open(test_file, 'w') as f:
            f.write('test')
        os.remove(test_file)
        logging.info(f"Network mount {NETWORK_MOUNT_POINT} is writable")
        return True
    except Exception as e:
        logging.error(f"Cannot write to {NETWORK_MOUNT_POINT}: {e}")
        return False

def get_save_paths():
    """Returns the appropriate save path(s) based on configuration"""
    local_path = "/home/wdbx/Desktop/Download-Folder/"
    network_path = NETWORK_MOUNT_POINT + "/"

    paths = []

    if SAVE_TO_BOTH:
        paths = [local_path, network_path]
    elif SAVE_TO_NETWORK:
        # Check if network is accessible
        if check_network_mount():
            paths = [network_path]
        else:
            logging.error("Network mount not accessible! Falling back to local save.")
            paths = [local_path]
    else:
        paths = [local_path]

    return paths

def download_show(show):
    save_paths = get_save_paths()
    day_of_week = calendar.day_name[yesterday.weekday()]

    url = generate_url(show)
    filename = f"{unsanitize_text(show.full_show_name)} - {(datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')}.mp3"

    try:
        logging.info(f"Downloading {show.show_name} from {url}")
        response = requests.get(url, stream=True)
        response.raise_for_status()

        # Save to each configured location
        for base_path in save_paths:
            download_path = os.path.join(base_path, show.day_of_week, unsanitize_text(show.full_show_name))

            try:
                logging.info(f"Creating directory: {download_path}")
                os.makedirs(download_path, exist_ok=True)

                full_file_path = os.path.join(download_path, filename)
                logging.info(f"Saving to: {full_file_path}")

                with open(full_file_path, 'wb') as f:
                    f.write(response.content)

                # Verify file was written
                if os.path.exists(full_file_path):
                    file_size = os.path.getsize(full_file_path)
                    logging.info(f"Successfully saved {show.show_name} to {full_file_path} (Size: {file_size} bytes)")
                else:
                    logging.error(f"File was not created at {full_file_path}")

            except OSError as e:
                logging.error(f"Failed to save to {base_path}: {e}")
            except Exception as e:
                logging.error(f"Unexpected error saving to {base_path}: {e}")

    except requests.exceptions.HTTPError as errh:
        logging.error(f"HTTP Error: {errh}")
    except requests.exceptions.ConnectionError as errc:
        logging.error(f"Error Connecting: {errc}")
    except requests.exceptions.Timeout as errt:
        logging.error(f"Timeout Error: {errt}")
    except requests.exceptions.RequestException as err:
        logging.error(f"Something went wrong: {err}")

# Set up logging with more detailed output
logging.basicConfig(
    filename='/home/wdbx/Desktop/Download-Folder/log.txt',
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

logging.info("="*50)
logging.info("Script started")
logging.info(f"SAVE_TO_NETWORK: {SAVE_TO_NETWORK}")
logging.info(f"SAVE_TO_BOTH: {SAVE_TO_BOTH}")
logging.info(f"Network mount point: {NETWORK_MOUNT_POINT}")

# Read shows from file
shows = []
with open('/home/wdbx/Desktop/Download-Folder/showst.txt', 'r') as file:
    for line in file:
        parts = line.strip().split(',')
        show = Show(*parts)
        shows.append(show)

# Calculate yesterday
yesterday = datetime.now() - timedelta(days=1)
yesterday_day_of_week = calendar.day_name[yesterday.weekday()]

logging.info(f"Looking for shows from {yesterday_day_of_week}")

for show in shows:
    if show.day_of_week == yesterday_day_of_week and show.download_flag == "1":
        download_show(show)

logging.info("Script completed")
