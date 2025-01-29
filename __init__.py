"""
Azure Function that synchronizes device information between Microsoft Intune and Snipe-IT.
This function runs on a timer trigger and performs the following steps:
1. Authenticates with Microsoft Graph API
2. Retrieves device information from Intune
3. Processes and standardizes the device data
4. Updates corresponding assets in Snipe-IT with the latest hardware information

The function focuses on updating specific hardware details like memory, storage, and IMEI numbers.

The following Graph API permissions (entitlements) are typically required to access Intune device data:
- DeviceManagementManagedDevices.Read.All: Needed to read managed device information from Intune
"""

# Standard library imports
import os
import sys
import time
import logging
import requests
from urllib.parse import urljoin
import azure.functions as func
from datetime import datetime

# Configure root logger
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Create console handler and set level to info
console_handler = logging.StreamHandler(stream=sys.stdout)
console_handler.setLevel(logging.INFO)

# Create formatter
formatter = logging.Formatter('%(asctime)s %(levelname)s %(message)s')

# Add formatter to console handler
console_handler.setFormatter(formatter)

# Add console handler to logger
if not logger.handlers:
    logger.addHandler(console_handler)

def map_storage_size(storage_size_bytes):
    """
    Maps raw storage sizes to standardized capacity labels.
    Example: 549755813888 bytes -> '512 GB'
    
    This standardization helps maintain consistent storage reporting across the inventory
    by mapping various raw sizes to common marketing capacities.
    """
    try:
        size_in_bytes = int(storage_size_bytes)
    except (ValueError, TypeError):
        logging.warning(f"Invalid storage size: {storage_size_bytes}")
        return ""

    # Define ranges for standard sizes
    standard_sizes = [
        (0, 192 * 1024 ** 3, '128 GB'),
        (192 * 1024 ** 3, 384 * 1024 ** 3, '256 GB'),
        (384 * 1024 ** 3, 768 * 1024 ** 3, '512 GB'),
        (768 * 1024 ** 3, 1536 * 1024 ** 3, '1 TB'),
        (1536 * 1024 ** 3, 3072 * 1024 ** 3, '2 TB'),
        (3072 * 1024 ** 3, 6144 * 1024 ** 3, '4 TB'),
        (6144 * 1024 ** 3, 12288 * 1024 ** 3, '8 TB')
    ]

    for min_size, max_size, label in standard_sizes:
        if min_size < size_in_bytes <= max_size:
            return label

    logging.warning(f"Storage size {size_in_bytes} bytes does not match standard sizes.")
    return ""

def get_graph_access_token():
    """
    Handles Microsoft Graph API authentication using client credentials flow.
    
    Environment Variables Required:
    - AZURE_TENANT_ID: The Azure AD tenant ID
    - DEVICES_GRAPH_ID: The application (client) ID
    - DEVICES_GRAPH_SECRET: The client secret for authentication
    
    The token obtained is used for all subsequent Intune API calls.
    """
    # This function retrieves an OAuth2 access token from Microsoft Graph.
    # Required entitlements: DeviceManagementManagedDevices.Read.All and possibly others
    AZURE_TENANT_ID = os.getenv('AZURE_TENANT_ID')
    client_id = os.getenv('DEVICES_GRAPH_ID')
    client_secret = os.getenv('DEVICES_GRAPH_SECRET')

    if not AZURE_TENANT_ID or not client_id or not client_secret:
        logging.error("One or more environment variables (AZURE_TENANT_ID, DEVICES_GRAPH_ID, DEVICES_GRAPH_SECRET) are missing.")
        raise EnvironmentError("Missing Azure AD configuration.")

    token_url = f'https://login.microsoftonline.com/{AZURE_TENANT_ID}/oauth2/v2.0/token'
    scope = 'https://graph.microsoft.com/.default'

    payload = {
        'client_id': client_id,
        'client_secret': client_secret,
        'scope': scope,
        'grant_type': 'client_credentials'
    }

    logging.info("Requesting access token from Microsoft Graph API.")
    response = requests.post(token_url, data=payload)
    try:
        response.raise_for_status()
    except requests.exceptions.HTTPError as http_err:
        logging.error(f"HTTP error occurred during token request: {http_err} - Response: {response.text}")
        raise
    except Exception as err:
        logging.error(f"Unexpected error during token request: {err}")
        raise

    token = response.json().get('access_token')
    if not token:
        logging.error("No access token found in the response.")
        raise ValueError("Access token retrieval failed.")

    logging.info("Access token successfully acquired.")
    return token

def fetch_intune_device_data(access_token):
    """
    Retrieves managed device information from Intune via Microsoft Graph API.
    
    Specifically requests these device properties:
    - Basic info: ID, name, manufacturer, model
    - OS details: OS type and version
    - Hardware info: Serial number, memory, storage, IMEI
    
    This raw data forms the basis for synchronization with Snipe-IT.
    """
    # Fetches the list of managed devices from Intune via Microsoft Graph using the provided access token.
    # Uses the "/deviceManagement/managedDevices" endpoint to retrieve hardware details like memory/storage.
    # Required entitlements: DeviceManagementManagedDevices.Read.All
    graph_url = "https://graph.microsoft.com/v1.0/deviceManagement/managedDevices"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json"
    }
    params = {
        '$select': 'id,deviceName,manufacturer,model,operatingSystem,osVersion,serialNumber,physicalMemoryInBytes,totalStorageSpaceInBytes,imei'
    }

    try:
        response = requests.get(graph_url, headers=headers, params=params, timeout=30)
        response.raise_for_status()
        data = response.json()
        devices = data.get('value', [])
        logging.debug(f"Retrieved devices data from Intune: {devices}")
        return devices
    except requests.exceptions.HTTPError as http_err:
        logging.error(f"Graph API HTTP error: {http_err} - Response: {response.text}")
        raise
    except requests.exceptions.RequestException as e:
        logging.error(f"Graph API request error: {e}")
        raise

def process_device_data(devices):
    """
    Transforms raw Intune device data into a format compatible with Snipe-IT.
    
    Key transformations:
    1. Converts memory from bytes to GB
    2. Maps storage sizes to standard marketing capacities
    3. Extracts relevant fields for Snipe-IT asset updates
    4. Handles missing or null values appropriately
    
    This standardization ensures consistent data format in Snipe-IT.
    """
    # Processes the raw device data from Intune:
    # 1) Converts memory from bytes to GB
    # 2) Maps raw storage sizes to standardized capacity labels
    # 3) Extracts essential fields (IMEI, manufacturer, etc.) for Snipe-IT synchronization
    models = []
    for idx, device in enumerate(devices, start=1):
        serial_number = device.get("serialNumber")
        imei = device.get("imei", None)

        # Convert memory from bytes to GB
            "hostname": device.get("deviceName", None),
            "model_description": device.get("model", None),
            "os_version": device.get("operatingSystem", None),
            "manufacturer": device.get("manufacturer", None),
            "memory": memory_gb,
            "storage": storage_mapped,  # Use mapped storage
            "imei": imei
            # Removed "architecture" as it's not a valid property
        }

        logging.debug(f"Device {idx}: Retrieved from Intune: {hardware_details}")
        models.append(hardware_details)
    
    logging.info(f"Processed {len(models)} devices from Intune.")
    return models

def get_snipeit_asset_by_serial(snipeit_api_url, snipeit_key, serial_number):
    """
    Retrieves an asset from Snipe-IT by its serial number.

    Args:
        snipeit_api_url (str): Base URL of Snipe-IT.
        snipeit_key (str): API key for Snipe-IT authentication.
        serial_number (str): Serial number of the asset.

    Returns:
        dict: Asset data if found, else None.

    Raises:
        HTTPError: If the API request fails.
    """
    # Looks up a Snipe-IT asset matching the given serial number.
    # This lets us properly update the correct hardware entry in Snipe-IT.
    headers = {
        "Authorization": f"Bearer {snipeit_key}",
        "Accept": "application/json"
    }
    search_endpoint = f"/api/v1/hardware/byserial/{serial_number}?deleted=false"
    search_url = urljoin(snipeit_api_url, search_endpoint)
    logging.debug(f"Searching for asset with Serial Number: {serial_number} at {search_url}")

    try:
        response = requests.get(search_url, headers=headers, timeout=10)
        response.raise_for_status()
        data = response.json()
        if data.get('total', 0) > 0:
            asset = data['rows'][0]
            logging.debug(f"Found asset ID {asset['id']} for Serial Number: {serial_number}")
            logging.debug(f"Asset details: {asset}")
            return asset
        else:
            logging.warning(f"No asset found with Serial Number: {serial_number}")
            return None
    except requests.exceptions.HTTPError as http_err:
        logging.error(f"Snipe-IT API error during asset search: {http_err} - Response: {response.text}")
        raise
    except requests.exceptions.RequestException as e:
        logging.error(f"Snipe-IT asset search request error: {e}")
        raise

def get_snipeit_asset_by_imei(snipeit_api_url, snipeit_key, imei):
    """
    Retrieves an asset from Snipe-IT by its IMEI number within specific categories.

    Args:
        snipeit_api_url (str): Base URL of Snipe-IT.
        snipeit_key (str): API key for Snipe-IT authentication.
        imei (str): IMEI number of the asset.

    Returns:
        dict: Asset data if found, else None.

    Raises:
        HTTPError: If the API request fails.
    """
    # Searches for an asset by IMEI within specific hardware categories in Snipe-IT.
    # Used especially for mobile devices or similar equipment.
    headers = {
        "Authorization": f"Bearer {snipeit_key}",
        "Accept": "application/json"
    }
    search_endpoint = "/api/v1/hardware"
    search_url = urljoin(snipeit_api_url, search_endpoint)
    categories = [4, 5]  # Categories where IMEI is relevant
    logging.debug(f"Searching for asset with IMEI: {imei} in categories: {categories}")

    for category_id in categories:
        params = {
            "search": imei,
            "category_id": category_id,
            "deleted": "false"
        }
        logging.debug(f"Searching in category ID {category_id} with params: {params}")

        try:
            response = requests.get(search_url, headers=headers, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()

            if data.get('total', 0) > 0:
                # Iterate through results to find exact IMEI match
                for asset in data.get('rows', []):
                    # Assuming '_snipeit_imei_36' is the custom field for IMEI
                    asset_imei = asset.get('_snipeit_imei_36')
                    if asset_imei and asset_imei.strip() == imei.strip():
                        logging.debug(f"Found asset ID {asset['id']} for IMEI: {imei} in category {category_id}")
                        logging.debug(f"Asset details: {asset}")
                        return asset
                logging.warning(f"No exact asset found with IMEI: {imei} in category {category_id}")
            else:
                logging.info(f"No assets found with IMEI: {imei} in category {category_id}")
        except requests.exceptions.HTTPError as http_err:
            logging.error(f"Snipe-IT API HTTP error during asset search by IMEI in category {category_id}: {http_err} - Response: {response.text}")
            # Optionally, continue to the next category or re-raise the exception
            continue
        except requests.exceptions.RequestException as e:
            logging.error(f"Snipe-IT asset search request error by IMEI in category {category_id}: {e}")
            # Optionally, continue to the next category or re-raise the exception
            continue

    logging.warning(f"No asset found with IMEI: {imei} in specified categories.")
    return None

def update_snipeit_asset(snipeit_api_url, snipeit_key, asset_id, hardware_details):
    """
    Updates an asset in Snipe-IT with detailed hardware information using PATCH.

    Args:
        snipeit_api_url (str): Base URL of Snipe-IT.
        snipeit_key (str): API key for Snipe-IT authentication.
        asset_id (int): ID of the asset to update.
        hardware_details (dict): Dictionary containing hardware details.

    Raises:
        HTTPError: If the API request fails.
    """
    # Updates the specified asset in Snipe-IT with new hardware information (memory/storage/IMEI).
    # Uses a PATCH request with a minimal payload containing only updated fields.
    headers = {
        "Authorization": f"Bearer {snipeit_key}",
        "Accept": "application/json",
        "Content-Type": "application/json"
    }
    update_endpoint = f"/api/v1/hardware/{asset_id}"
    update_url = urljoin(snipeit_api_url, update_endpoint)

    # Map hardware details to Snipe-IT fields
    field_mapping = {
        "_snipeit_memory_9": hardware_details.get("memory", None),
        "_snipeit_storage_10": hardware_details.get("storage", None),
        "_snipeit_imei_36": hardware_details.get("imei", None)
    }

    # Build payload, skipping empty values
    payload = {key: value for key, value in field_mapping.items() if value not in [None, '', 'N/A']}

    if not payload:
        logging.info(f"No valid hardware details to update for asset ID {asset_id}. Skipping update.")
        return

    # Log the payload being sent to Snipe-IT
    logging.debug(f"Payload for asset ID {asset_id}: {payload}")

    logging.info(f"Updating Snipe-IT asset ID {asset_id} with new hardware details.")
    try:
        response = requests.patch(update_url, headers=headers, json=payload, timeout=10)
        response.raise_for_status()
        response_data = response.json()

        logging.info(f"Successfully updated asset ID {asset_id}. Response: {response_data}")

        # Log the updated asset details
        logging.debug(f"Updated asset ID {asset_id} with payload: {payload}")
    except requests.exceptions.HTTPError as http_err:
        logging.error(f"Snipe-IT API HTTP error during asset update: {http_err} - Response: {response.text}")
        raise
    except requests.exceptions.RequestException as req_err:
        logging.error(f"Snipe-IT asset update request error: {req_err}")
        raise

def send_data_to_snipeit(snipeit_api_url, snipeit_key, models, test_serials=None):
    """
    Sends hardware data from Intune to Snipe-IT assets by updating technical details.

    Args:
        snipeit_api_url (str): Base URL of Snipe-IT.
        snipeit_key (str): API key for Snipe-IT authentication.
        models (list): List of dictionaries containing hardware details.
        test_serials (set, optional): Set of serial numbers to process for testing. Defaults to None.

    Raises:
        Exception: If the process fails.
    """
    # Iterates over the processed device list and tries to match each device to a Snipe-IT asset.
    # If found, updates the asset with memory, storage, and IMEI details.
    for idx, model in enumerate(models, start=1):
        serial_number = model.get("serial_number")
        imei = model.get("imei")

        if imei:
            # Attempt to find asset by IMEI within relevant categories
            asset = get_snipeit_asset_by_imei(snipeit_api_url, snipeit_key, imei)
            identifier = f"IMEI {imei}"
        elif serial_number:
            # Fallback to searching by Serial Number
            asset = get_snipeit_asset_by_serial(snipeit_api_url, snipeit_key, serial_number)
            identifier = f"Serial Number {serial_number}"
        else:
            logging.warning(f"No Serial Number or IMEI found for device {idx}. Skipping.")
            continue

        if asset:
            asset_id = asset['id']
            logging.debug(f"Asset found for {identifier}: {asset}")

            # Prepare hardware details to update
            hardware_details = {
                "memory": model.get("memory"),
                "storage": model.get("storage"),
                "imei": imei
            }

            # Log the data being sent to Snipe-IT
            logging.debug(f"Hardware details for asset ID {asset_id}: {hardware_details}")

            try:
                # Update the asset with hardware details
                update_snipeit_asset(snipeit_api_url, snipeit_key, asset_id, hardware_details)
            except Exception as e:
                logging.error(f"Failed to update asset ID {asset_id}: {e}")
                continue

            # Introduce a delay to comply with API throttling (e.g., 0.5 seconds)
            time.sleep(0.5)  # Adjust the duration as needed
        else:
            logging.warning(f"Asset with {identifier} not found in Snipe-IT. Skipping device {idx}.")

def main(mytimer: func.TimerRequest) -> None:
    """
    Azure Function entry point triggered by a timer.

    Args:
        mytimer (func.TimerRequest): The timer trigger.
    """
    # The main entry point for this Azure Function:
    # 1) Validates environment variables for Snipe-IT API
    # 2) Acquires the Microsoft Graph access token
    # 3) Retrieves Intune device data
    # 4) Processes the data into Snipe-IT-friendly format
    # 5) Updates matching Snipe-IT assets with the new hardware details
    # 6) Logs and handles errors gracefully
    logging.info('Intune to Snipe-IT Sync Function triggered.')

    try:
        # Load environment variables
        snipeit_api_url = os.getenv("SNIPE_API_URL")
        snipeit_api_key = os.getenv("SNIPE_API_KEY")

        if not snipeit_api_url or not snipeit_api_key:
            logging.error("SNIPE_API_URL and/or SNIPE_API_KEY environment variables are not set.")
            raise EnvironmentError("Missing Snipe-IT configuration.")

        # Authenticate with Microsoft Graph API
        access_token = get_graph_access_token()

        # Fetch device data from Intune
        devices = fetch_intune_device_data(access_token)

        # Process and map device data
        models = process_device_data(devices)

        if not models:
            logging.info("No devices fetched from Intune. Exiting function.")
            return

        # Send data to Snipe-IT
        send_data_to_snipeit(snipeit_api_url, snipeit_api_key, models)

        logging.info("Asset data synchronization completed successfully.")

    except Exception as e:
        logging.exception("An error occurred during the function execution.")
        raise