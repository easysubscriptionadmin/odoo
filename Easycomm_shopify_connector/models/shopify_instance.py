# -*- coding: utf-8 -*-

from odoo import api, fields, models, _
from odoo.exceptions import UserError
from datetime import timedelta
import logging
import requests
import certifi

_logger = logging.getLogger(__name__)

# Refresh the token this many seconds before it actually expires.
TOKEN_REFRESH_MARGIN = 60


class ShopifyInstance(models.Model):
    _name = 'shopify.instance'
    _description = 'Shopify Instance'

    @api.model
    def _valid_field_parameter(self, field, name):
        # Allow 'password' parameter for Char fields to mask sensitive data in UI
        if name == 'password':
            return True
        return super()._valid_field_parameter(field, name)

    name = fields.Char('Name', required=True)
    shop_url = fields.Char('Shop URL', required=True, help="Your Shopify store name (e.g., mystore)")
    api_key = fields.Char('Client ID', help="Shopify app Client ID (also called API Key)")
    api_secret = fields.Char('Client Secret', password=True, help="Shopify app Client Secret (also called API Secret Key)")
    access_token = fields.Char(
        'Access Token', password=True, copy=False,
        help="Admin API access token. If you don't have one, fill in the Client ID/Secret "
             "and click 'Generate Access Token' — it is fetched automatically from Shopify "
             "(client credentials grant) and refreshed when it expires.")
    token_expires_at = fields.Datetime(
        'Token Expires At', copy=False, readonly=True,
        help="When the current access token expires. It is refreshed automatically before then.")
    api_version = fields.Char('API Version', required=True, default='2024-01')
    active = fields.Boolean('Active', default=True)
    currency_id = fields.Many2one('res.currency', string='Store Currency', help='Currency used in Shopify store')

    # Sync tracking
    last_product_sync = fields.Datetime('Last Product Sync')
    last_customer_sync = fields.Datetime('Last Customer Sync')
    last_order_sync = fields.Datetime('Last Order Sync')

    # Stats
    total_products_synced = fields.Integer('Total Products', compute='_compute_totals')
    total_customers_synced = fields.Integer('Total Customers', compute='_compute_totals')
    total_orders_synced = fields.Integer('Total Orders', compute='_compute_totals')

    def _compute_totals(self):
        for record in self:
            record.total_products_synced = self.env['product.template'].search_count([
                ('shopify_instance_id', '=', record.id)
            ])
            record.total_customers_synced = self.env['res.partner'].search_count([
                ('shopify_instance_id', '=', record.id)
            ])
            record.total_orders_synced = self.env['sale.order'].search_count([
                ('shopify_instance_id', '=', record.id)
            ])

    def _get_shop_domain(self):
        """Return the canonical shop domain, e.g. 'mystore.myshopify.com'."""
        self.ensure_one()
        shop = (self.shop_url or '').replace('https://', '').replace('http://', '')
        shop = shop.replace('.myshopify.com', '').strip().strip('/')
        return f"{shop}.myshopify.com"

    def _get_base_url(self):
        self.ensure_one()
        return f"https://{self._get_shop_domain()}/admin/api/{self.api_version}"

    def _get_graphql_url(self):
        self.ensure_one()
        return f"https://{self._get_shop_domain()}/admin/api/{self.api_version}/graphql.json"

    def _get_headers(self):
        self.ensure_one()
        return {
            'Content-Type': 'application/json',
            'X-Shopify-Access-Token': self._ensure_access_token(),
        }

    def _execute_graphql(self, query, variables=None):
        """Execute a GraphQL mutation or query against Shopify Admin API"""
        self.ensure_one()
        url = self._get_graphql_url()
        payload = {'query': query}
        if variables:
            payload['variables'] = variables

        response = requests.post(
            url,
            headers=self._get_headers(),
            json=payload,
            timeout=30,
            verify=certifi.where()
        )

        if response.status_code != 200:
            raise UserError(_('GraphQL request failed: %s - %s') % (response.status_code, response.text))

        result = response.json()
        if result.get('errors'):
            error_msgs = [e.get('message', str(e)) for e in result['errors']]
            raise UserError(_('Shopify GraphQL errors: %s') % ', '.join(error_msgs))

        return result.get('data', {})

    def fetch_metafield_definitions(self, owner_type):
        """Return all metafield definitions for an owner type so we can show
        every field (even empty ones) like the Shopify admin does.

        owner_type: 'PRODUCT' or 'PRODUCTVARIANT'.
        Returns a list of dicts: {namespace, key, name, type, description}.
        Fails soft (returns []) if the token lacks access to definitions.
        """
        self.ensure_one()
        definitions = []
        after = None
        query = """
        query($ownerType: MetafieldOwnerType!, $after: String) {
          metafieldDefinitions(first: 250, ownerType: $ownerType, after: $after) {
            edges {
              node { name namespace key description type { name } }
            }
            pageInfo { hasNextPage endCursor }
          }
        }
        """
        try:
            while True:
                data = self._execute_graphql(query, {'ownerType': owner_type, 'after': after})
                block = data.get('metafieldDefinitions', {})
                for edge in block.get('edges', []):
                    node = edge.get('node', {})
                    definitions.append({
                        'namespace': node.get('namespace') or '',
                        'key': node.get('key') or '',
                        'name': node.get('name') or '',
                        'type': (node.get('type') or {}).get('name') or '',
                        'description': node.get('description') or '',
                    })
                page = block.get('pageInfo', {})
                if page.get('hasNextPage'):
                    after = page.get('endCursor')
                else:
                    break
        except Exception as e:
            _logger.warning('[Shopify] Could not fetch %s metafield definitions: %s', owner_type, e)
        return definitions

    def test_connection(self):
        self.ensure_one()
        try:
            url = f"{self._get_base_url()}/shop.json"
            response = requests.get(url, headers=self._get_headers(), timeout=10, verify=certifi.where())

            if response.status_code == 200:
                shop_data = response.json().get('shop', {})
                shop_name = shop_data.get('name', 'Unknown')

                # Fetch and store currency
                currency_code = shop_data.get('currency', 'USD')
                currency = self._fetch_and_activate_currency(currency_code)
                self.currency_id = currency.id

                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': _('Connection Successful'),
                        'message': _('Successfully connected to %s (Currency: %s)') % (shop_name, currency_code),
                        'type': 'success',
                    }
                }
            else:
                raise UserError(_('Connection failed: %s - %s') % (response.status_code, response.text))
        except Exception as e:
            raise UserError(_('Connection error: %s') % str(e))

    def _fetch_and_activate_currency(self, currency_code):
        """Fetch currency from Odoo and activate it if needed"""
        self.ensure_one()

        # Search for currency
        currency = self.env['res.currency'].with_context(active_test=False).search([('name', '=', currency_code)], limit=1)

        if not currency:
            # Currency doesn't exist in Odoo, use company currency as fallback
            _logger.warning(f'Currency {currency_code} not found in Odoo database, using company currency')
            return self.env.company.currency_id

        # If currency is inactive, activate it
        if not currency.active:
            try:
                currency.active = True
                _logger.info(f'Activated currency {currency_code} for Shopify store')
            except Exception as e:
                _logger.warning(f'Could not activate currency {currency_code}: {str(e)}')

        return currency

    # ──────────────────────────────────────────────────────────────────────
    # Access token — Shopify "client credentials" grant
    #   POST /admin/oauth/access_token  (client_id + client_secret only)
    #   https://shopify.dev/docs/apps/build/authentication-authorization/client-secrets
    # ──────────────────────────────────────────────────────────────────────

    def _fetch_access_token(self):
        """Fetch a fresh Admin API access token from Shopify using the
        client-credentials grant and store it (with its expiry) on the record.

        Returns the new access token string.
        """
        self.ensure_one()
        if not self.shop_url:
            raise UserError(_('Please set the Shop URL first.'))
        if not self.api_key or not self.api_secret:
            raise UserError(_('Please set both the Client ID and Client Secret first.'))

        url = f"https://{self._get_shop_domain()}/admin/oauth/access_token"
        try:
            response = requests.post(
                url,
                data={
                    'grant_type': 'client_credentials',
                    'client_id': self.api_key,
                    'client_secret': self.api_secret,
                },
                timeout=30,
                verify=certifi.where(),
            )
        except Exception as e:
            raise UserError(_('Could not reach Shopify to get a token: %s') % e)

        if response.status_code != 200:
            detail = self._humanize_token_error(response)
            if 'app_not_installed' in (response.text or '').lower():
                raise UserError(_(
                    "Shopify rejected the request: the app is not installed on this store.\n\n"
                    "The client-credentials grant only works after the app is installed on the shop. "
                    "Install your app on '%(shop)s' first, then click Generate Access Token again.\n\n"
                    "If this is a Custom App (Shopify admin → Settings → Apps and sales channels → "
                    "Develop apps → your app), just copy its Admin API access token and paste it into "
                    "the Access Token field — no need to generate it here."
                ) % {'shop': self._get_shop_domain()})
            raise UserError(_('Token request failed (HTTP %(code)s): %(detail)s') % {
                'code': response.status_code, 'detail': detail,
            })

        try:
            data = response.json()
        except ValueError:
            raise UserError(_('Unexpected response from Shopify: %s') % self._humanize_token_error(response))
        token = data.get('access_token')
        if not token:
            raise UserError(_('Shopify did not return an access token: %s') % self._humanize_token_error(response))

        self.access_token = token
        expires_in = data.get('expires_in')
        self.token_expires_at = (
            fields.Datetime.now() + timedelta(seconds=int(expires_in)) if expires_in else False
        )
        _logger.info('[Shopify] Fetched access token for instance %s (expires_in=%s)',
                     self.id, expires_in)
        return token

    @staticmethod
    def _humanize_token_error(response):
        """Turn Shopify's token-endpoint error (JSON or full HTML page) into a
        short human-readable message instead of a raw HTML dump."""
        import re
        text = response.text or ''
        # 1) JSON error body
        try:
            data = response.json()
            if isinstance(data, dict):
                msg = data.get('error_description') or data.get('error') or data.get('errors')
                if msg:
                    return str(msg)
        except ValueError:
            pass
        # 2) Shopify HTML error page, e.g. "Oauth error app_not_installed: ..."
        m = re.search(r'Oauth error\s+([a-z_]+)\s*:\s*([^<\n]+)', text, re.I)
        if m:
            return f"{m.group(1).strip()} — {m.group(2).strip()}"
        m = re.search(r'<title>\s*(.*?)\s*</title>', text, re.I | re.S)
        if m:
            return m.group(1).strip()
        # 3) Fallback: strip tags / whitespace, truncate
        plain = re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', text)).strip()
        return (plain[:200] or ('HTTP %s' % response.status_code))

    def _ensure_access_token(self):
        """Return a valid access token, fetching/refreshing it automatically
        when it is missing or about to expire."""
        self.ensure_one()
        needs_refresh = not self.access_token
        if self.token_expires_at:
            cutoff = fields.Datetime.now() + timedelta(seconds=TOKEN_REFRESH_MARGIN)
            if self.token_expires_at <= cutoff:
                needs_refresh = True
        # If we have a token but no recorded expiry, assume it's a manually
        # pasted (long-lived) token and keep using it.
        if needs_refresh and self.api_key and self.api_secret:
            return self._fetch_access_token()
        if not self.access_token:
            raise UserError(_('No access token. Set Client ID/Secret and click "Generate Access Token".'))
        return self.access_token

    def action_generate_access_token(self):
        """Button: fetch (or refresh) the access token right now."""
        self.ensure_one()
        self._fetch_access_token()
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Access Token Generated'),
                'message': _('A new Shopify access token was fetched and saved.'),
                'type': 'success',
                'next': {'type': 'ir.actions.act_window_close'},
            },
        }
