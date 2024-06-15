import os
import base64
import requests
import json
import shutil
import logging
from unidecode import unidecode
import re
from datetime import datetime
import unicodedata

# Function to read API key and aliases from tcg.cfg file
def read_config():
    config_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'tcg.cfg')
    if not os.path.exists(config_file):
        print("Configuration file 'tcg.cfg' not found.")
        return None, {}, 'WARNING'
    
    api_key = None
    aliases = {}
    logging_level = 'WARNING'
    
    with open(config_file, 'r') as file:
        for line in file:
            if line.startswith("api_key="):
                api_key = line.split("=", 1)[1].strip()
            elif line.startswith("aliases="):
                alias_string = line.split("=", 1)[1].strip()
                alias_pairs = [pair for pair in alias_string.split(";") if pair]
                for pair in alias_pairs:
                    if ":" in pair:
                        key, values = pair.split(":")
                        key = key.strip()
                        key_lower = key.lower()
                        values = [v.strip().lower() for v in values.split(",")]
                        aliases[key_lower] = key  # Ensure the primary alias is mapped to itself
                        for value in values:
                            aliases[value] = key  # Map each alias to the primary key
                    else:
                        logging.warning(f"Invalid alias pair: {pair}")
            elif line.startswith("logging_level="):
                logging_level = line.split("=", 1)[1].strip().upper()
    
    if not api_key:
        logging.error("API key not found in 'tcg.cfg'.")
    
    return api_key, aliases, logging_level

API_KEY, ALIASES, LOGGING_LEVEL = read_config()

# Set up logging
log_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'log.txt')
logging.basicConfig(level=getattr(logging, LOGGING_LEVEL, logging.WARNING), format='%(asctime)s %(levelname)s:%(message)s', handlers=[logging.FileHandler(log_file), logging.StreamHandler()])

def encode_image(image_path):
    try:
        with open(image_path, "rb") as image_file:
            return base64.b64encode(image_file.read()).decode('utf-8')
    except Exception as e:
        log_error(f"Failed to encode image {image_path}: {str(e)}")
        return None

def log_error(message):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"{timestamp} - Error occurred. Check log.txt for details.")
    logging.error(message)

def sanitize_filename(name):
    # Replace '&' with 'and'
    name = name.replace('&', 'and')
    # Normalize Unicode characters
    nfkd_form = unicodedata.normalize('NFD', name)
    sanitized_name = ''.join([c for c in nfkd_form if not unicodedata.combining(c)])
    # Remove special characters except spaces, hyphens, and periods
    sanitized_name = re.sub(r'[^\w\s.-]', '', sanitized_name)
    # Replace multiple spaces with a single space
    sanitized_name = re.sub(r'\s+', ' ', sanitized_name).strip()
    return sanitized_name

def resolve_alias(name, aliases):
    name_lower = unidecode(name.lower())  # Normalize name to handle special characters
    resolved_name = aliases.get(name_lower, name)
    logging.debug(f"Resolving alias for '{name}': '{resolved_name}'")
    return resolved_name

def process_image(image_path):
    if not API_KEY:
        log_error("Missing API key. Skipping image processing.")
        return None, None, None
    
    base64_image = encode_image(image_path)
    if not base64_image:
        log_error(f"Image encoding failed for {image_path}")
        return None, None, None
    
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {API_KEY}"
    }
    
    payload = {
        "model": "gpt-4o",
        "messages": [
            {"role": "system", "content": "You are a trading card game expert that responds in JSON."},
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "Please identify this card. Return the card name, set name, and TCG name."
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{base64_image}"
                        }
                    }
                ]
            }
        ],
        "max_tokens": 300
    }
    
    response = requests.post("https://api.openai.com/v1/chat/completions", headers=headers, json=payload)
    
    if response.status_code == 200:
        response_data = response.json()
        logging.debug(f"Received response for {image_path}: {response_data}")
        try:
            content = response_data['choices'][0]['message']['content']
            content_json = json.loads(content.strip('```json\n'))
            return content_json.get("card_name"), content_json.get("set_name"), content_json.get("tcg_name")
        except (KeyError, json.JSONDecodeError) as e:
            log_error(f"Error parsing JSON response for {image_path}: {str(e)}")
            return None, None, None
    else:
        log_error(f"API request failed for {image_path} with status code {response.status_code}")
        logging.debug(f"Response content: {response.content}")
        return None, None, None

def move_file(src, dest_dir, new_name):
    if not os.path.exists(dest_dir):
        os.makedirs(dest_dir)
    
    base_name, extension = os.path.splitext(new_name)
    new_path = os.path.join(dest_dir, new_name)
    count = 1
    
    while os.path.exists(new_path):
        new_name = f"{base_name}_{count}{extension}"
        new_path = os.path.join(dest_dir, new_name)
        count += 1
    
    shutil.move(src, new_path)
    return new_name

def process_directory(import_directory, sorted_directory):
    for root, dirs, files in os.walk(import_directory):
        for file in files:
            if file.lower().endswith(('.jpg', '.jpeg', '.png')):
                file_path = os.path.join(root, file)
                card_name, set_name, tcg_name = process_image(file_path)
                
                if card_name and set_name and tcg_name:
                    # Resolve aliases for TCG name
                    tcg_name = resolve_alias(tcg_name, ALIASES)
                    
                    sanitized_tcg_name = sanitize_filename(tcg_name)
                    dest_dir = os.path.join(sorted_directory, sanitized_tcg_name)
                    
                    original_name = file
                    new_name = f"{sanitize_filename(card_name)} - {sanitize_filename(set_name)}{os.path.splitext(file)[1]}"
                    new_name = move_file(file_path, dest_dir, new_name)
                    relative_original_path = os.path.relpath(file_path, import_directory)
                    relative_new_path = os.path.relpath(os.path.join(dest_dir, new_name), sorted_directory)
                    print(f"Renamed '{relative_original_path}' to '{relative_new_path}'")
                    logging.info(f"Renamed '{relative_original_path}' to '{relative_new_path}'")
                else:
                    log_error(f"Failed OCR recognition for '{file}'")

if __name__ == "__main__":
    print("OpenAI TCG Sorter is running!")
    base_directory = os.path.dirname(os.path.abspath(__file__))
    import_directory = os.path.join(base_directory, "Import")
    sorted_directory = os.path.join(base_directory, "Sorted")
    process_directory(import_directory, sorted_directory)
    logging.info("Processing complete. Press Enter to exit.")
    input("Processing complete. Press Enter to exit.")
