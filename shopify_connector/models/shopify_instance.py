# -*- coding: utf-8 -*-

from odoo import api, fields, models, _
from odoo.exceptions import UserError
import logging
import requests
import certifi

_logger = logging.getLogger(__name__)


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
    api_key = fields.Char('API Key')
    api_secret = fields.Char('API Secret', password=True)
    access_token = fields.Char('Access Token', required=True, password=True)
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

    def _get_base_url(self):
        self.ensure_one()
        # Clean up shop_url - remove .myshopify.com if already present
        shop_url = self.shop_url.replace('.myshopify.com', '').strip()
        return f"https://{shop_url}.myshopify.com/admin/api/{self.api_version}"

    def _get_headers(self):
        self.ensure_one()
        return {
            'Content-Type': 'application/json',
            'X-Shopify-Access-Token': self.access_token,
        }

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
