"""
Safaricom M-Pesa Daraja API client — STK Push (Lipa Na M-Pesa Online).

API docs: https://developer.safaricom.co.ke/APIs/MpesaExpressSimulate

Flow:
1. Get OAuth2 token (GET /oauth/v1/generate?grant_type=client_credentials)
2. STK Push (POST /mpesa/stkpush/v1/processrequest) — customer gets a PIN prompt
3. Query STK Push status (POST /mpesa/stkpushquery/v1/query)
4. Callback from Safaricom with the result

Sandbox base URL: https://sandbox.safaricom.co.ke
Production base URL: https://api.safaricom.co.ke
"""
import base64
import logging
from datetime import datetime

import requests
from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)

MPESA_TOKEN_CACHE_KEY = "mpesa_daraja_access_token"


class MpesaError(Exception):
    """Raised when the M-Pesa Daraja API returns an error."""

    def __init__(self, message, status_code=None, response_body=None):
        self.status_code = status_code
        self.response_body = response_body
        super().__init__(message)


class MpesaClient:
    """Safaricom M-Pesa Daraja API client (STK Push / Lipa Na M-Pesa Online)."""

    def __init__(self):
        self.base_url = settings.MPESA_BASE_URL.rstrip("/")
        self.consumer_key = settings.MPESA_CONSUMER_KEY
        self.consumer_secret = settings.MPESA_CONSUMER_SECRET
        self.shortcode = settings.MPESA_SHORTCODE
        self.passkey = settings.MPESA_PASSKEY
        self.callback_url = settings.MPESA_CALLBACK_URL
        self.environment = settings.MPESA_ENVIRONMENT  # sandbox | production

    # ── OAuth2 Token ─────────────────────────────────────────────────────

    def get_token(self):
        """
        Obtain an OAuth2 access token from Safaricom Daraja.
        Tokens are cached for their lifetime minus a 60-second buffer.
        """
        cached = cache.get(MPESA_TOKEN_CACHE_KEY)
        if cached:
            return cached

        url = f"{self.base_url}/oauth/v1/generate?grant_type=client_credentials"
        credentials = base64.b64encode(
            f"{self.consumer_key}:{self.consumer_secret}".encode()
        ).decode()
        headers = {"Authorization": f"Basic {credentials}"}

        resp = requests.get(url, headers=headers, timeout=30)
        if resp.status_code != 200:
            logger.error(f"M-Pesa token request failed: {resp.status_code} {resp.text}")
            raise MpesaError(
                "Failed to obtain M-Pesa access token",
                status_code=resp.status_code,
                response_body=resp.text,
            )

        data = resp.json()
        token = data["access_token"]
        expires_in = int(data.get("expires_in", 3599))
        cache.set(MPESA_TOKEN_CACHE_KEY, token, max(expires_in - 60, 60))
        logger.info("M-Pesa access token obtained and cached.")
        return token

    # ── Generate Password ────────────────────────────────────────────────

    def _generate_password(self, timestamp):
        """
        Generate the STK Push password.
        Password = Base64(BusinessShortCode + Passkey + Timestamp)
        """
        raw = f"{self.shortcode}{self.passkey}{timestamp}"
        return base64.b64encode(raw.encode()).decode()

    def _get_timestamp(self):
        """Return current timestamp in YYYYMMDDHHmmss format."""
        return datetime.now().strftime("%Y%m%d%H%M%S")

    # ── STK Push (Lipa Na M-Pesa Online) ─────────────────────────────────

    def stk_push(self, amount, phone_number, account_reference, transaction_desc=""):
        """
        Initiate an STK Push (Lipa Na M-Pesa Online) payment.

        Args:
            amount: Amount to collect (integer, KES has no decimals in STK push).
            phone_number: Customer phone in 254XXXXXXXXX format.
            account_reference: Reference shown on customer's phone (max 12 chars).
            transaction_desc: Description (max 13 chars).

        Returns:
            dict with MerchantRequestID, CheckoutRequestID, ResponseCode, etc.

        Raises:
            MpesaError on API failure.
        """
        token = self.get_token()
        timestamp = self._get_timestamp()
        password = self._generate_password(timestamp)

        url = f"{self.base_url}/mpesa/stkpush/v1/processrequest"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        body = {
            "BusinessShortCode": self.shortcode,
            "Password": password,
            "Timestamp": timestamp,
            "TransactionType": "CustomerPayBillOnline",
            "Amount": str(int(amount)),
            "PartyA": phone_number,
            "PartyB": self.shortcode,
            "PhoneNumber": phone_number,
            "CallBackURL": self.callback_url,
            "AccountReference": account_reference[:12],
            "TransactionDesc": (transaction_desc or f"Payment {account_reference}")[:13],
        }

        logger.info(
            f"M-Pesa STK Push: amount={body['Amount']} KES, "
            f"phone={phone_number}, ref={account_reference}"
        )

        resp = requests.post(url, json=body, headers=headers, timeout=30)

        if resp.status_code != 200:
            logger.error(f"M-Pesa STK Push failed: {resp.status_code} {resp.text}")
            raise MpesaError(
                f"STK Push request failed ({resp.status_code})",
                status_code=resp.status_code,
                response_body=resp.text,
            )

        data = resp.json()
        response_code = data.get("ResponseCode")
        if response_code != "0":
            error_msg = data.get("ResponseDescription", "Unknown error")
            logger.error(f"M-Pesa STK Push rejected: {error_msg}")
            raise MpesaError(
                f"STK Push rejected: {error_msg}",
                response_body=data,
            )

        logger.info(
            f"M-Pesa STK Push accepted: "
            f"MerchantRequestID={data.get('MerchantRequestID')}, "
            f"CheckoutRequestID={data.get('CheckoutRequestID')}"
        )
        return data

    # ── Query STK Push Status ────────────────────────────────────────────

    def query_stk_status(self, checkout_request_id):
        """
        Query the status of an STK Push transaction.

        Args:
            checkout_request_id: The CheckoutRequestID from the STK push response.

        Returns:
            dict with ResultCode, ResultDesc, etc.

        Raises:
            MpesaError on API failure.
        """
        token = self.get_token()
        timestamp = self._get_timestamp()
        password = self._generate_password(timestamp)

        url = f"{self.base_url}/mpesa/stkpushquery/v1/query"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        body = {
            "BusinessShortCode": self.shortcode,
            "Password": password,
            "Timestamp": timestamp,
            "CheckoutRequestID": checkout_request_id,
        }

        resp = requests.post(url, json=body, headers=headers, timeout=30)

        if resp.status_code != 200:
            logger.error(f"M-Pesa STK query failed: {resp.status_code} {resp.text}")
            raise MpesaError(
                f"STK query failed ({resp.status_code})",
                status_code=resp.status_code,
                response_body=resp.text,
            )

        data = resp.json()
        logger.info(
            f"M-Pesa STK query for {checkout_request_id}: "
            f"ResultCode={data.get('ResultCode')}"
        )
        return data


def get_client():
    """Return a configured MpesaClient instance."""
    return MpesaClient()
