"""
Example usage of the Document OCR API.

This file demonstrates common use cases and API workflows.
"""

import requests
import json
import hmac
import hashlib
import time
from pathlib import Path

# Configuration
API_BASE_URL = "http://localhost:5005"
OCR_SECRET_KEY = "your-secret-key-here"


def sign_request(path: str, secret: str) -> tuple:
    """Generate HMAC signature headers for request authentication."""
    timestamp = int(time.time())
    payload = f"{timestamp}.{path}"
    signature = hmac.new(
        secret.encode('utf-8'),
        payload.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()
    return timestamp, signature


def example_health_check():
    """Example: Check API health status."""
    print("📋 Example 1: Health Check")
    print("-" * 50)

    response = requests.get(f"{API_BASE_URL}/")
    print(f"Status Code: {response.status_code}")
    print(f"Response: {json.dumps(response.json(), indent=2)}")
    print()


def example_extract_passport(image_path: str):
    """Example: Extract passport data from image."""
    print("📋 Example 2: Extract Passport Data")
    print("-" * 50)

    if not Path(image_path).exists():
        print(f"⚠️  Image file not found: {image_path}")
        print("   Please provide a valid passport image path\n")
        return

    # Prepare request with authentication headers
    timestamp, signature = sign_request("/api/passport", OCR_SECRET_KEY)
    headers = {
        "X-Timestamp": str(timestamp),
        "X-Signature": signature,
    }

    with open(image_path, 'rb') as f:
        files = {'file': f}
        response = requests.post(
            f"{API_BASE_URL}/api/passport",
            files=files,
            headers=headers
        )

    print(f"Status Code: {response.status_code}")
    result = response.json()
    print(f"Response: {json.dumps(result, indent=2)}")

    if result.get('success'):
        print("\n✅ Passport extraction successful!")
        print(f"   Name: {result['data'].get('surname')}, {result['data'].get('given_names')}")
        print(f"   Passport #: {result['data'].get('passport_number')}")
        print(f"   Country: {result['verification'].get('issuing_country')}")
    else:
        print(f"\n❌ Extraction failed: {result.get('message')}")
    print()


def example_extract_nin(image_path: str):
    """Example: Extract NIN data from image."""
    print("📋 Example 3: Extract NIN Data")
    print("-" * 50)

    if not Path(image_path).exists():
        print(f"⚠️  Image file not found: {image_path}")
        print("   Please provide a valid NIN image path\n")
        return

    # Request without authentication (currently disabled in auth.py)
    with open(image_path, 'rb') as f:
        files = {'file': f}
        response = requests.post(
            f"{API_BASE_URL}/api/nin",
            files=files
        )

    print(f"Status Code: {response.status_code}")
    result = response.json()
    print(f"Response: {json.dumps(result, indent=2)}")

    if result.get('success'):
        print("\n✅ NIN extraction successful!")
        data = result['data']
        print(f"   NIN: {data.get('nin')}")
        print(f"   Name: {data.get('full_name')}")
        print(f"   Type: {result.get('document_type')}")
    else:
        print(f"\n❌ Extraction failed: {result.get('message')}")
    print()


def example_extract_bank_statement(document_path: str):
    """Example: Extract bank statement data from PDF or image."""
    print("📋 Example 4: Extract Bank Statement Data")
    print("-" * 50)

    if not Path(document_path).exists():
        print(f"⚠️  Document file not found: {document_path}")
        print("   Please provide a valid bank statement path\n")
        return

    is_pdf = document_path.lower().endswith('.pdf')

    with open(document_path, 'rb') as f:
        files = {'file': f}
        response = requests.post(
            f"{API_BASE_URL}/api/bank-statement",
            files=files
        )

    print(f"Status Code: {response.status_code}")
    result = response.json()
    print(f"Response: {json.dumps(result, indent=2)}")

    if result.get('success'):
        print("\n✅ Bank statement extraction successful!")
        data = result['data']
        print(f"   Account: {data.get('account_name')}")
        print(f"   Bank: {data.get('bank_name')}")
        print(f"   Period: {data.get('start_date')} to {data.get('end_date')}")
        print(f"   Closing Balance: {data.get('closing_balance')}")
    else:
        print(f"\n❌ Extraction failed: {result.get('message')}")
    print()


def example_webhook_forwarding(webhook_secret: str, target_urls: list):
    """Example: Forward a webhook to multiple endpoints."""
    print("📋 Example 5: Webhook Forwarding")
    print("-" * 50)

    # Prepare webhook payload
    webhook_payload = {
        "event_type": "document_processed",
        "timestamp": int(time.time()),
        "document": {
            "type": "passport",
            "status": "success",
            "nin": "12345678901"
        }
    }

    # Prepare request with authentication
    timestamp, signature = sign_request("/api/webhooks/forward", webhook_secret)
    headers = {
        "X-Timestamp": str(timestamp),
        "X-Signature": signature,
        "X-Request-Id": "req-12345",
        "Content-Type": "application/json"
    }

    response = requests.post(
        f"{API_BASE_URL}/api/webhooks/forward",
        data=json.dumps(webhook_payload),
        headers=headers
    )

    print(f"Status Code: {response.status_code}")
    result = response.json()
    print(f"Response: {json.dumps(result, indent=2)}")

    if result.get('success'):
        print("\n✅ Webhook forwarding successful!")
        for forward in result.get('forwarded', []):
            print(f"   {forward['name']}: {'✅' if forward['ok'] else '❌'} ({forward.get('status_code', 'N/A')})")
    else:
        print(f"\n❌ Forwarding failed: {result.get('message')}")
    print()


def example_error_handling(bad_file_path: str = "nonexistent.txt"):
    """Example: Handle API errors gracefully."""
    print("📋 Example 6: Error Handling")
    print("-" * 50)

    try:
        # Missing file
        files = {'file': (bad_file_path, b'')}
        response = requests.post(f"{API_BASE_URL}/api/passport", files=files)
        result = response.json()

        if not result.get('success'):
            print(f"⚠️  API returned error: {result.get('message')}")
        else:
            print("✅ Request succeeded")
    except requests.exceptions.ConnectionError:
        print("❌ Connection Error: API server is not running")
        print("   Start the server with: python app.py")
    except requests.exceptions.RequestException as e:
        print(f"❌ Request Error: {e}")
    print()


def main():
    """Run all examples."""
    print("=" * 50)
    print("Document OCR API - Usage Examples")
    print("=" * 50)
    print()

    # 1. Health Check
    try:
        example_health_check()
    except requests.exceptions.ConnectionError:
        print("❌ Connection Error: API server is not running")
        print("   Start the server with: python app.py")
        print()
        return

    # 2-4. Document Processing (requires actual image files)
    print("⚠️  Document processing examples require actual image/PDF files.")
    print("    Uncomment and update these examples to use real files:\n")

    print("# example_extract_passport('path/to/passport.jpg')")
    print("# example_extract_nin('path/to/nin.jpg')")
    print("# example_extract_bank_statement('path/to/statement.pdf')")
    print()

    # 5. Webhook Forwarding (mock)
    print("🔄 Webhook forwarding requires configured target URLs.")
    print("   Set environment variables:")
    print("   - FORWARDER_SECRET")
    print("   - FORWARDER_TARGET_1_URL")
    print("   - FORWARDER_TARGET_2_URL")
    print("   - FORWARDER_TARGET_3_URL")
    print()

    # 6. Error Handling
    example_error_handling()

    print("=" * 50)
    print("For more information, see the README.md file")
    print("=" * 50)


if __name__ == '__main__':
    main()

