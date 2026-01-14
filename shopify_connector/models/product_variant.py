# -*- coding: utf-8 -*-

from odoo import api, fields, models, _
from odoo.exceptions import UserError
import logging

_logger = logging.getLogger(__name__)


class ProductProduct(models.Model):
    _inherit = 'product.product'

    shopify_variant_id = fields.Char('Shopify Variant ID', readonly=True, copy=False)
    shopify_inventory_item_id = fields.Char('Shopify Inventory Item ID', readonly=True, copy=False)
    shopify_sku = fields.Char('Shopify SKU', copy=False)
    shopify_barcode = fields.Char('Shopify Barcode', copy=False)
    shopify_position = fields.Integer('Shopify Position', default=1)
    shopify_weight = fields.Float('Shopify Weight (grams)')
    shopify_weight_unit = fields.Selection([
        ('g', 'Grams'),
        ('kg', 'Kilograms'),
        ('oz', 'Ounces'),
        ('lb', 'Pounds'),
    ], string='Weight Unit', default='g')

    shopify_requires_shipping = fields.Boolean('Requires Shipping', default=True)
    shopify_taxable = fields.Boolean('Taxable', default=True)
    shopify_inventory_policy = fields.Selection([
        ('deny', 'Do not allow sales when out of stock'),
        ('continue', 'Allow sales when out of stock'),
    ], string='Inventory Policy', default='deny')

    shopify_fulfillment_service = fields.Char('Fulfillment Service', default='manual')
    shopify_inventory_management = fields.Char('Inventory Management', default='shopify')

    def sync_variant_to_shopify(self):
        """Sync variant inventory and price to Shopify"""
        self.ensure_one()

        if not self.shopify_variant_id or not self.product_tmpl_id.shopify_instance_id:
            return

        try:
            import requests
            import certifi

            instance = self.product_tmpl_id.shopify_instance_id

            # Update variant details
            variant_data = {
                'variant': {
                    'id': int(self.shopify_variant_id),
                    'price': str(self.lst_price),
                    'sku': self.default_code or '',
                    'barcode': self.barcode or '',
                    'weight': self.weight or 0.0,
                    'inventory_policy': self.shopify_inventory_policy,
                }
            }

            url = f"{instance._get_base_url()}/variants/{self.shopify_variant_id}.json"
            response = requests.put(
                url,
                headers=instance._get_headers(),
                json=variant_data,
                timeout=30,
                verify=certifi.where()
            )

            if response.status_code == 200:
                _logger.info(f'Variant {self.shopify_variant_id} synced successfully')
                return True
            else:
                _logger.error(f'Failed to sync variant: {response.status_code} - {response.text}')
                return False

        except Exception as e:
            _logger.error(f'Error syncing variant: {str(e)}')
            return False
