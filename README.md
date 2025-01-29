# Intune to Snipe-IT Sync Azure Function

An Azure Function that synchronizes hardware details (e.g., storage, memory, IMEI) from Microsoft Intune to Snipe-IT assets. The function runs on a Timer Trigger and uses the Microsoft Graph API to fetch Intune device data.

## Overview
- Retrieves an access token from Microsoft Graph using a client credentials flow
- Collects device hardware data from Intune
- Maps storage sizes to standard capacity labels (e.g., `256 GB`, `512 GB`)
- Updates the matching Snipe-IT assets by either serial number or IMEI
- Logs all activity using Python’s built-in logging

## Timer Schedule
```json
{
  "scriptFile": "__init__.py",
  "bindings": [
    {
      "name": "mytimer",
      "type": "timerTrigger",
      "direction": "in",
      "schedule": "0 0 11 * * 5"
    }
  ]
}
```

This schedule triggers the function every Friday at 11:00 UTC.

## Environment Variables
The function references the following:

- **SNIPE_API_URL**: Base URL for Snipe-IT
- **SNIPE_API_KEY**: Snipe-IT API key
- **AZURE_TENANT_ID**: Azure Active Directory Tenant ID
- **DEVICES_GRAPH_ID**: Azure app (client) ID
- **DEVICES_GRAPH_SECRET**: Azure app client secret

## Required Graph Permissions
The Azure AD application used by this function needs the following entitlements for Intune device data:

- **DeviceManagementManagedDevices.Read.All**

## Deployment
1. Publish the function app with the provided `__init__.py` and the above cron schedule.
2. Configure the environment variables in Azure.
3. Confirm the assigned permissions in Azure AD for Intune device access.

## Logging
Logs are printed to standard output and can be monitored in Azure’s Application Insights or the Azure Portal logs.

## Additional Details
- Storage sizes are standardized to marketing labels.
- IMEI is used to locate assets in certain categories (e.g., mobile devices).
- Serial number is used if no IMEI is found.
- Snipe-IT asset updates are performed via PATCH requests.

For further customization, edit the function code in `__init__.py` to suit specific field mappings or additional Snipe-IT attributes.

