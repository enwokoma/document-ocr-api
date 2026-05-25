# Document OCR API

A comprehensive Python Flask API for extracting structured data from identity documents and financial records using advanced OCR and validation techniques.

## Features

- **Passport MRZ Extraction**: Extract Machine Readable Zone (MRZ) data from passport pages
- **NIN Processing**: Extract data from Nigerian National Identification Number (NIN) slips and cards
- **Bank Statement Analysis**: Extract key financial data from bank statement documents
- **Image Quality Detection**: Detect glare, blur, and other quality issues
- **HMAC Request Signing**: Optional request authentication with HMAC-SHA256
- **Webhook Forwarding**: Route webhook payloads to multiple destinations (optional)

## Installation

### Requirements
- Python 3.11+
- pip or conda

### Setup

1. Clone the repository:
```bash
git clone https://github.com/yourusername/document-ocr-api.git
cd document-ocr-api
```

2. Create a virtual environment:
```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

3. Install dependencies:
```bash
pip install -r requirements.txt
```

4. Create a `.env` file in the project root:
```env
OCR_SECRET_KEY=your-secret-key-here
FORWARDER_SECRET=your-webhook-secret
FORWARDER_TARGET_1_URL=https://endpoint1.example.com/webhook
FORWARDER_TARGET_2_URL=https://endpoint2.example.com/webhook
FORWARDER_TARGET_3_URL=https://endpoint3.example.com/webhook
```

## Running the API

```bash
python app.py
```

The API will start on `http://localhost:5005`.

API documentation is available at `http://localhost:5005/api-docs`.

## API Endpoints

### Health Check
- **GET** `/` - API health status

### Passport OCR
- **POST** `/api/scan-passport` - Extract MRZ from passport image (legacy endpoint)
- **POST** `/api/passport` - Extract passport data from image

### NIN Processing
- **POST** `/api/nin` - Extract data from NIN slip or card

### Bank Statements
- **POST** `/api/bank-statement` - Extract summary data from bank statement (PDF or image)

### Webhook Forwarding (Optional)
- **POST** `/api/webhooks/forward` - Receive and forward webhook payloads

## Configuration

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `OCR_SECRET_KEY` | Secret key for HMAC request signing | `dev-secret-change-in-production` |
| `FORWARDER_SECRET` | Secret for signing forwarded webhooks | (empty) |
| `FORWARDER_TARGET_1_URL` | First webhook target URL | (empty) |
| `FORWARDER_TARGET_2_URL` | Second webhook target URL | (empty) |
| `FORWARDER_TARGET_3_URL` | Third webhook target URL | (empty) |

## License

This project is available under the MIT License. See `LICENSE` file for details.

