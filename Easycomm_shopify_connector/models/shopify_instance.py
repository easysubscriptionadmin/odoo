# -*- coding: utf-8 -*-

from odoo import api, fields, models, _
from odoo.exceptions import UserError
from datetime import timedelta
import logging
import threading
import requests
import certifi

_logger = logging.getLogger(__name__)

# Guard against double-starting the definitions/metaobjects fetch.
_defs_lock = threading.Lock()
_defs_running = {}

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
              node { name namespace key description type { name } validations { name value } }
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
                    # metaobject-reference definitions carry the metaobject
                    # definition id in their validations.
                    metaobject_gid = ''
                    for v in node.get('validations') or []:
                        if v.get('name') in ('metaobject_definition_id', 'metaobject_definition_ids'):
                            raw = v.get('value') or ''
                            if raw.startswith('['):
                                try:
                                    import json as _json
                                    ids = _json.loads(raw)
                                    raw = ids[0] if ids else ''
                                except Exception:
                                    raw = ''
                            metaobject_gid = raw
                            break
                    definitions.append({
                        'namespace': node.get('namespace') or '',
                        'key': node.get('key') or '',
                        'name': node.get('name') or '',
                        'type': (node.get('type') or {}).get('name') or '',
                        'description': node.get('description') or '',
                        'metaobject_definition_gid': metaobject_gid,
                    })
                page = block.get('pageInfo', {})
                if page.get('hasNextPage'):
                    after = page.get('endCursor')
                else:
                    break
        except Exception as e:
            _logger.warning('[Shopify] Could not fetch %s metafield definitions: %s', owner_type, e)
        return definitions

    def _upsert_definition(self, owner_type, namespace, key, name='', mtype='',
                           description='', metaobject_definition_gid=''):
        """Create or update a single metafield definition record. Returns True
        if a new record was created."""
        Definition = self.env['shopify.metafield.definition']
        vals = {
            'name': name or key or '',
            'namespace': namespace or '',
            'key': key or '',
            'type': mtype or '',
            'description': description or '',
            'owner_type': owner_type,
            'shopify_instance_id': self.id,
            'metaobject_definition_gid': metaobject_definition_gid or '',
        }
        existing = Definition.search([
            ('shopify_instance_id', '=', self.id),
            ('owner_type', '=', owner_type),
            ('namespace', '=', vals['namespace']),
            ('key', '=', vals['key']),
        ], limit=1)
        if existing:
            # never blank an already-known metaobject link (e.g. when a later
            # derive-from-metafields pass has no validations data)
            if not vals['metaobject_definition_gid']:
                vals.pop('metaobject_definition_gid')
            existing.write(vals)
            return False
        Definition.create(vals)
        return True

    def action_fetch_metafield_definitions(self):
        """Populate shopify.metafield.definition records for the Field dropdown.

        Two sources are combined so the dropdown is always usable:
          1. Shopify's metafield DEFINITIONS via GraphQL (needs the read access).
          2. The metafields already imported onto products/variants (works even
             when the definitions API is restricted).
        """
        self.ensure_one()
        created = 0

        # Runs in the BACKGROUND: fetching all definitions + metaobject values
        # exceeds the 120s web-request limit on stores with many metaobjects
        # ("Connection lost"). Progress arrives via notification toasts.
        instance_id = self.id
        instance_name = self.name
        uid = self.env.uid
        registry = self.env.registry
        run_key = (self.env.cr.dbname, instance_id)

        with _defs_lock:
            if _defs_running.get(run_key):
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': _('Already Running'),
                        'message': _('A metafield fetch for %s is already in progress.') % instance_name,
                        'type': 'warning',
                    },
                }
            _defs_running[run_key] = True

        def run_fetch():
            import odoo
            try:
                with registry.cursor() as cr:
                    env = odoo.api.Environment(cr, uid, {})
                    env['shopify.instance'].browse(instance_id)._run_definitions_sync(notify_uid=uid)
                    cr.commit()
            except Exception as exc:
                _logger.error('Background metafield fetch failed: %s', exc, exc_info=True)
                try:
                    with registry.cursor() as cr:
                        env = odoo.api.Environment(cr, uid, {})
                        partner = env['res.users'].browse(uid).partner_id
                        env['bus.bus']._sendone(partner, 'simple_notification', {
                            'title': _('Metafield Fetch Failed'),
                            'message': str(exc),
                            'type': 'danger',
                        })
                        cr.commit()
                except Exception:
                    pass
            finally:
                with _defs_lock:
                    _defs_running.pop(run_key, None)

        threading.Thread(target=run_fetch, daemon=True).start()

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Metafield Fetch Started'),
                'message': _('Fetching definitions and metaobject values from %s in the '
                             'background — you will get progress notifications.') % instance_name,
                'type': 'info',
            },
        }

    def _run_definitions_sync(self, notify_uid=None):
        """Fetch definitions + derive + metaobject values, committing along the
        way and sending progress toasts. Runs in a background thread."""
        self.ensure_one()

        def _notify(title, message, msg_type='info'):
            if not notify_uid:
                return
            try:
                partner = self.env['res.users'].browse(notify_uid).partner_id
                self.env['bus.bus']._sendone(partner, 'simple_notification', {
                    'title': title, 'message': message, 'type': msg_type,
                })
                self.env.cr.commit()
            except Exception as bus_err:
                _logger.warning('Bus notification failed: %s', bus_err)

        created = 0
        # 1) From Shopify definitions API
        for owner_type, gql_type in (('product', 'PRODUCT'), ('variant', 'PRODUCTVARIANT')):
            for d in self.fetch_metafield_definitions(gql_type):
                if self._upsert_definition(owner_type, d.get('namespace'), d.get('key'),
                                           d.get('name'), d.get('type'), d.get('description'),
                                           d.get('metaobject_definition_gid')):
                    created += 1
            self.env.cr.commit()

        # 2) Derive from already-imported metafields (distinct namespace/key/type)
        created += self._derive_definitions_from_metafields()
        self.env.cr.commit()
        _notify(_('Metafield Definitions'),
                _('Definitions ready (%s newly added). Fetching metaobject values...') % created)

        # 3) Fetch the metaobject ENTRIES (the selectable values, e.g. all
        #    'Diaper Size' options) so merchants can pick them on the product.
        entries = self._fetch_metaobjects(notify=_notify)

        total = self.env['shopify.metafield.definition'].search_count([
            ('shopify_instance_id', '=', self.id)])
        _notify(_('Metafield Fetch Complete'),
                _('All done! %s definitions available, %s metaobject values fetched.') % (total, entries),
                'success' if entries or total else 'warning')
        return total, entries

    def _fetch_metaobjects(self, notify=None):
        """Fetch all metaobject entries of the store (paginated) and store them
        as shopify.metaobject records. Commits per metaobject type so progress
        is never lost. Returns the number of entries seen."""
        self.ensure_one()
        Metaobject = self.env['shopify.metaobject']
        count = 0
        last_notified = 0

        # 1) All metaobject definitions (type handles)
        defs = []
        after = None
        q_defs = """
        query($after: String) {
          metaobjectDefinitions(first: 100, after: $after) {
            edges { node { id type name } }
            pageInfo { hasNextPage endCursor }
          }
        }
        """
        try:
            while True:
                data = self._execute_graphql(q_defs, {'after': after})
                block = data.get('metaobjectDefinitions', {})
                defs += [e.get('node', {}) for e in block.get('edges', [])]
                page = block.get('pageInfo', {})
                if page.get('hasNextPage'):
                    after = page.get('endCursor')
                else:
                    break
        except Exception as e:
            _logger.warning('[metaobjects] could not fetch definitions: %s', e)
            return 0

        # 2) All entries per definition type
        q_objs = """
        query($type: String!, $after: String) {
          metaobjects(type: $type, first: 250, after: $after) {
            edges { node { id displayName handle type } }
            pageInfo { hasNextPage endCursor }
          }
        }
        """
        for d in defs:
            mtype = d.get('type')
            def_gid = d.get('id') or ''
            if not mtype:
                continue
            after = None
            try:
                while True:
                    data = self._execute_graphql(q_objs, {'type': mtype, 'after': after})
                    block = data.get('metaobjects', {})
                    for edge in block.get('edges', []):
                        node = edge.get('node', {})
                        gid = node.get('id')
                        if not gid:
                            continue
                        vals = {
                            'name': node.get('displayName') or node.get('handle') or gid,
                            'handle': node.get('handle') or '',
                            'shopify_gid': gid,
                            'metaobject_type': node.get('type') or mtype,
                            'definition_gid': def_gid,
                            'shopify_instance_id': self.id,
                        }
                        existing = Metaobject.search([
                            ('shopify_instance_id', '=', self.id),
                            ('shopify_gid', '=', gid),
                        ], limit=1)
                        if existing:
                            existing.write(vals)
                        else:
                            Metaobject.create(vals)
                        count += 1
                    page = block.get('pageInfo', {})
                    if page.get('hasNextPage'):
                        after = page.get('endCursor')
                    else:
                        break
                # Persist each finished type immediately; report progress.
                self.env.cr.commit()
                if notify and count - last_notified >= 200:
                    last_notified = count
                    notify(_('Metaobject Values'), _('%s values fetched so far...') % count)
            except Exception as e:
                _logger.warning('[metaobjects] fetch failed for type %s: %s', mtype, e)
                self.env.cr.rollback()
                continue
        _logger.info('[metaobjects] %s entries fetched for instance %s', count, self.name)
        return count

    def _derive_definitions_from_metafields(self):
        """Create definition records from the distinct metafields already
        imported on this instance's products/variants. Returns count created."""
        self.ensure_one()
        rows = self.env['shopify.product.metafield'].search_read(
            [('shopify_instance_id', '=', self.id)],
            ['owner_type', 'namespace', 'key', 'type', 'label'],
        )
        created = 0
        seen = set()
        for r in rows:
            owner = r.get('owner_type') or 'product'
            ns = r.get('namespace') or ''
            key = r.get('key') or ''
            combo = (owner, ns, key)
            if combo in seen or not key:
                continue
            seen.add(combo)
            if self._upsert_definition(owner, ns, key, r.get('label'), r.get('type')):
                created += 1
        return created

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
