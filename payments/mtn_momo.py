"""
MTN Mobile Money Collections API client for Uganda ("UG", +256, UGX) and
South Sudan ("SS", +211, SSP). Each market is a separate MTN subscription.

API docs: https://momodeveloper.mtn.com/docs/services/collection

Flow:
1. Get OAuth2 token (POST /collection/token/)
2. Request to Pay (POST /collection/v1_0/requesttopay)
3. Poll / callback for status (GET /collection/v1_0/requesttopay/{referenceId})

Sandbox base URL: https://sandbox.momodeveloper.mtn.com
Production base URL: https://proxy.momoapi.mtn.com
"""
import base64
import logging
import uuid
from datetime import datetime, timedelta

import requests
from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)

TOKEN_CACHE_KEY = "mtn_momo_collection_token"


class MoMoError(Exception):
    """Raised when the MoMo API returns an error."""

    def __init__(self, message, status_code=None, response_body=None):
        self.status_code = status_code
        self.response_body = response_body
        super().__init__(message)


MARKET_UGANDA = "UG"
MARKET_SOUTH_SUDAN = "SS"
_MARKET_DIAL_CODES = {MARKET_UGANDA: "256", MARKET_SOUTH_SUDAN: "211"}
_MARKET_SETTING_PREFIX = {MARKET_UGANDA: "MTN_MOMO_", MARKET_SOUTH_SUDAN: "MTN_MOMO_SS_"}


def _market_setting(market, name):
    return getattr(settings, f"{_MARKET_SETTING_PREFIX[market]}{name}", "")


def market_for_msisdn(msisdn):
    """Pick the MoMo market from an MSISDN such as 211912345678 or +256771234567."""
    digits = str(msisdn or "").lstrip("+")
    for market, code in _MARKET_DIAL_CODES.items():
        if digits.startswith(code):
            return market
    return None


def market_for_currency(currency):
    if currency and currency == _market_setting(MARKET_SOUTH_SUDAN, "CURRENCY"):
        return MARKET_SOUTH_SUDAN
    return MARKET_UGANDA


def collection_currency_for_msisdn(msisdn):
    """Currency MoMo will collect in for this payer (South Sudan numbers use SSP)."""
    market = market_for_msisdn(msisdn) or MARKET_UGANDA
    return _market_setting(market, "CURRENCY")


def is_market_configured(market):
    return bool(
        _market_setting(market, "API_USER")
        and _market_setting(market, "COLLECTION_SECRET")
        and _market_setting(market, "COLLECTION_PRIMARY_KEY")
    )


class MoMoClient:
    """MTN MoMo Collections API client for one market."""

    def __init__(self, market=MARKET_UGANDA):
        self.market = market
        self.base_url = _market_setting(market, "BASE_URL").rstrip("/")
        self.subscription_key = _market_setting(market, "COLLECTION_PRIMARY_KEY")
        self.api_user = _market_setting(market, "API_USER")
        self.api_secret = _market_setting(market, "COLLECTION_SECRET")
        self.environment = _market_setting(market, "ENVIRONMENT")
        self.currency = _market_setting(market, "CURRENCY")
        self.callback_url = _market_setting(market, "CALLBACK_URL")
        self.token_cache_key = TOKEN_CACHE_KEY if market == MARKET_UGANDA else f"{TOKEN_CACHE_KEY}:{market}"

    # ── OAuth2 Token ─────────────────────────────────────────────────────

    def _get_basic_auth(self):
        """Base64-encode api_user:api_secret for Basic auth."""
        credentials = f"{self.api_user}:{self.api_secret}"
        return base64.b64encode(credentials.encode()).decode()

    def get_token(self):
        """
        Get an OAuth2 access token for the Collections product.
        Tokens are cached for their lifetime minus a 60-second buffer.
        """
        cached = cache.get(self.token_cache_key)
        if cached:
            return cached

        url = f"{self.base_url}/collection/token/"
        headers = {
            "Authorization": f"Basic {self._get_basic_auth()}",
            "Ocp-Apim-Subscription-Key": self.subscription_key,
        }

        resp = requests.post(url, headers=headers, timeout=30)
        if resp.status_code != 200:
            logger.error(f"MoMo token request failed: {resp.status_code} {resp.text}")
            raise MoMoError(
                "Failed to obtain MoMo access token",
                status_code=resp.status_code,
                response_body=resp.text,
            )

        data = resp.json()
        token = data["access_token"]
        expires_in = int(data.get("expires_in", 3600))
        # Cache with 60s buffer
        cache.set(self.token_cache_key, token, max(expires_in - 60, 60))
        logger.info("MoMo access token obtained and cached.")
        return token

    # ── Request to Pay ───────────────────────────────────────────────────

    def request_to_pay(self, amount, phone_number, external_id, payer_message="", payee_note=""):
        """
        Initiate a Request to Pay (collection).

        Args:
            amount: Amount to collect (string or number, no decimals for UGX).
            phone_number: MSISDN of the payer in E.164 without '+' (e.g. '256771234567').
            external_id: Your internal reference (e.g. payment ID or booking ref).
            payer_message: Message shown on the payer's phone.
            payee_note: Internal note for the payee.

        Returns:
            reference_id (UUID string) — use this to check status later.

        Raises:
            MoMoError on API failure.
        """
        reference_id = str(uuid.uuid4())
        token = self.get_token()

        url = f"{self.base_url}/collection/v1_0/requesttopay"
        headers = {
            "Authorization": f"Bearer {token}",
            "X-Reference-Id": reference_id,
            "X-Target-Environment": self.environment,
            "Ocp-Apim-Subscription-Key": self.subscription_key,
            "Content-Type": "application/json",
        }
        if self.callback_url:
            headers["X-Callback-Url"] = self.callback_url

        # MTN expects amount as a string
        body = {
            "amount": str(int(amount)),  # UGX has no decimals
            "currency": self.currency,
            "externalId": str(external_id),
            "payer": {
                "partyIdType": "MSISDN",
                "partyId": phone_number.lstrip("+"),
            },
            "payerMessage": payer_message or f"EcoBus ticket payment #{external_id}",
            "payeeNote": payee_note or f"Payment for booking {external_id}",
        }

        logger.info(f"MoMo Request to Pay: ref={reference_id}, amount={body['amount']} {self.currency}, phone={phone_number}")

        resp = requests.post(url, json=body, headers=headers, timeout=30)

        # 202 Accepted = success (async processing)
        if resp.status_code == 202:
            logger.info(f"MoMo Request to Pay accepted: ref={reference_id}")
            return reference_id

        logger.error(f"MoMo Request to Pay failed: {resp.status_code} {resp.text}")
        raise MoMoError(
            f"Request to Pay failed ({resp.status_code})",
            status_code=resp.status_code,
            response_body=resp.text,
        )

    # ── Check Payment Status ─────────────────────────────────────────────

    def get_payment_status(self, reference_id):
        """
        Check the status of a Request to Pay.

        Returns dict with keys: status, financialTransactionId, reason, etc.
        Status values: PENDING, SUCCESSFUL, FAILED, REJECTED, TIMEOUT, EXPIRED.
        """
        token = self.get_token()
        url = f"{self.base_url}/collection/v1_0/requesttopay/{reference_id}"
        headers = {
            "Authorization": f"Bearer {token}",
            "X-Target-Environment": self.environment,
            "Ocp-Apim-Subscription-Key": self.subscription_key,
        }

        resp = requests.get(url, headers=headers, timeout=30)
        if resp.status_code != 200:
            logger.error(f"MoMo status check failed: {resp.status_code} {resp.text}")
            raise MoMoError(
                f"Status check failed ({resp.status_code})",
                status_code=resp.status_code,
                response_body=resp.text,
            )

        data = resp.json()
        logger.info(f"MoMo payment status for {reference_id}: {data.get('status')}")
        return data

    # ── Validate Account Holder ──────────────────────────────────────────

    def validate_account_holder(self, phone_number):
        """
        Check if a phone number is registered on MTN MoMo.
        Returns True if active, False otherwise.
        """
        token = self.get_token()
        msisdn = phone_number.lstrip("+")
        url = f"{self.base_url}/collection/v1_0/accountholder/msisdn/{msisdn}/active"
        headers = {
            "Authorization": f"Bearer {token}",
            "X-Target-Environment": self.environment,
            "Ocp-Apim-Subscription-Key": self.subscription_key,
        }

        resp = requests.get(url, headers=headers, timeout=15)
        if resp.status_code == 200:
            return resp.json().get("result", False)
        logger.warning(f"MoMo account validation failed for {msisdn}: {resp.status_code}")
        return False

    # ── Get Account Balance ──────────────────────────────────────────────

    def get_balance(self):
        """Get the collection account balance."""
        token = self.get_token()
        url = f"{self.base_url}/collection/v1_0/account/balance"
        headers = {
            "Authorization": f"Bearer {token}",
            "X-Target-Environment": self.environment,
            "Ocp-Apim-Subscription-Key": self.subscription_key,
        }

        resp = requests.get(url, headers=headers, timeout=15)
        if resp.status_code != 200:
            raise MoMoError(
                f"Balance check failed ({resp.status_code})",
                status_code=resp.status_code,
                response_body=resp.text,
            )
        return resp.json()


def get_client(market=MARKET_UGANDA):
    """Return a MoMoClient for the given market ("UG" or "SS")."""
    return MoMoClient(market)


def get_client_for_payment(payment):
    """Client for the market a payment was collected in (from its frozen currency)."""
    return MoMoClient(market_for_currency(getattr(payment, "currency", "")))
