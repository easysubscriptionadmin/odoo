# -*- coding: utf-8 -*-

import base64
import hashlib
import hmac
import json
import logging

from odoo import http
from odoo.http import request
try:
    from odoo.http import Response
except ImportError:
    from werkzeug.wrappers import Response

_logger = logging.getLogger(__name__)


class ShopifyWebhookController(http.Controller):
    """HTTP controller that receives all incoming Shopify webhook POST requests."""

    @http.route('/shopify/webhook', type='http', auth='public', methods=['POST'], csrf=False)
    def receive_webhook(self, **kwargs):
        """Single endpoint for all Shopify webhook topics.

        Shopify sends:
          X-Shopify-Topic        e.g. "orders/create"
          X-Shopify-Shop-Domain  e.g. "mystore.myshopify.com"
          X-Shopify-Hmac-SHA256  base64(HMAC-SHA256(body, api_secret))
          Content-Type           application/json
        """
        topic = ''
        try:
            topic = request.httprequest.headers.get('X-Shopify-Topic', '').strip()
            shop_domain = request.httprequest.headers.get('X-Shopify-Shop-Domain', '').strip()
            hmac_header = request.httprequest.headers.get('X-Shopify-Hmac-SHA256', '').strip()
            raw_body = request.httprequest.get_data(as_text=False)

            # ── Step 1: Log receipt ────────────────────────────────────────
            _logger.info(
                f'[ShopifyWebhook] ▶ Received — topic={topic!r}  '
                f'domain={shop_domain!r}  '
                f'hmac={"present" if hmac_header else "ABSENT"}  '
                f'body={len(raw_body)} bytes'
            )

            if not topic or not shop_domain:
                _logger.warning(
                    f'[ShopifyWebhook] ✘ Rejected — missing required headers: '
                    f'topic={topic!r}  domain={shop_domain!r}  (HTTP 400)'
                )
                return Response('Bad Request', status=400)

            # ── Step 2: HMAC verification ──────────────────────────────────
            _logger.info(
                f'[ShopifyWebhook] ▶ Starting HMAC verification for domain={shop_domain!r}'
            )
            hmac_ok = self._verify_hmac(raw_body, hmac_header, shop_domain)

            if not hmac_ok:
                _logger.warning(
                    f'[ShopifyWebhook] ✘ HMAC MISMATCH — rejecting '
                    f'topic={topic!r}  domain={shop_domain!r}  (HTTP 401)'
                )
                return Response('Unauthorized', status=401)

            _logger.info(
                f'[ShopifyWebhook] ✔ HMAC verified OK for domain={shop_domain!r}'
            )

            # ── Step 3: Parse JSON body ────────────────────────────────────
            _logger.info(
                f'[ShopifyWebhook] ▶ Parsing JSON body ({len(raw_body)} bytes)'
            )
            try:
                data = json.loads(raw_body.decode('utf-8')) if raw_body else {}
                top_keys = ', '.join(list(data.keys())[:8]) if isinstance(data, dict) else type(data).__name__
                _logger.info(
                    f'[ShopifyWebhook] ✔ JSON parsed OK — top-level keys: {top_keys}'
                )
            except Exception as parse_err:
                _logger.error(
                    f'[ShopifyWebhook] ✘ JSON parse FAILED for topic={topic!r}: {parse_err}'
                )
                return Response('Bad Request', status=400)

            # ── Step 4: Dispatch to model ──────────────────────────────────
            _logger.info(
                f'[ShopifyWebhook] ▶ Dispatching to process_webhook — topic={topic!r}'
            )
            result = request.env['shopify.webhook'].sudo().process_webhook(
                topic, data, shop_domain
            )
            _logger.info(
                f'[ShopifyWebhook] ✔ process_webhook completed for topic={topic!r} '
                f'result={result!r} — responding HTTP 200'
            )
            return Response('OK', status=200)

        except Exception as e:
            _logger.error(
                f'[ShopifyWebhook] ✘ Unexpected error processing topic={topic!r}: {e}',
                exc_info=True
            )
            return Response('Internal Server Error', status=500)

    # ──────────────────────────────────────────────────────────────────────
    # HMAC verification
    # ──────────────────────────────────────────────────────────────────────

    def _verify_hmac(self, raw_body, hmac_header, shop_domain):
        """Verify the webhook signature sent by Shopify.

        Returns True when the signature matches or when no api_secret is
        configured (development / test environments).
        """
        if not hmac_header:
            _logger.warning(
                f'[ShopifyWebhook._verify_hmac] No HMAC header for '
                f'domain={shop_domain!r} — allowing through (no signature to verify)'
            )
            return True

        try:
            _logger.info(
                f'[ShopifyWebhook._verify_hmac] ▶ Looking up Shopify instance '
                f'for domain={shop_domain!r}'
            )
            instance = request.env['shopify.instance'].sudo().search([
                '|',
                ('shop_url', 'ilike', shop_domain.replace('.myshopify.com', '')),
                ('shop_url', '=', shop_domain),
            ], limit=1)

            if not instance:
                _logger.warning(
                    f'[ShopifyWebhook._verify_hmac] No Shopify instance found for '
                    f'domain={shop_domain!r} — skipping HMAC check (allowing through)'
                )
                return True

            if not instance.api_secret:
                _logger.warning(
                    f'[ShopifyWebhook._verify_hmac] Instance "{instance.name}" has no '
                    f'api_secret configured — skipping HMAC check (allowing through)'
                )
                return True

            _logger.info(
                f'[ShopifyWebhook._verify_hmac] ▶ Instance found: "{instance.name}" — '
                f'computing expected HMAC-SHA256'
            )
            expected = base64.b64encode(
                hmac.new(
                    instance.api_secret.encode('utf-8'),
                    raw_body,
                    hashlib.sha256,
                ).digest()
            ).decode('utf-8')

            match = hmac.compare_digest(expected, hmac_header)
            if match:
                _logger.info(
                    f'[ShopifyWebhook._verify_hmac] ✔ HMAC digest matches — '
                    f'instance="{instance.name}"'
                )
            else:
                _logger.warning(
                    f'[ShopifyWebhook._verify_hmac] ✘ HMAC digest MISMATCH — '
                    f'instance="{instance.name}"  domain={shop_domain!r}'
                )
            return match

        except Exception as e:
            _logger.error(
                f'[ShopifyWebhook._verify_hmac] ✘ Unexpected error: {e} — '
                f'allowing through to avoid blocking legitimate webhooks',
                exc_info=True
            )
            return True  # Fail open on unexpected errors
