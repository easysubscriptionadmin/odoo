# -*- coding: utf-8 -*-

from odoo import api, fields, models, _
from odoo.exceptions import UserError
import logging
import requests
import certifi

_logger = logging.getLogger(__name__)


class ShopifyInventorySync(models.Model):
    _name = 'shopify.inventory.sync'
    _description = 'Shopify Inventory Sync'
    _order = 'sync_date desc'

    name = fields.Char('Sync Reference', required=True, default='New')
    shopify_instance_id = fields.Many2one('shopify.instance', string='Shopify Instance', required=True)
    sync_date = fields.Datetime('Sync Date', default=fields.Datetime.now, readonly=True)
    sync_type = fields.Selection([
        ('manual', 'Manual'),
        ('automatic', 'Automatic'),
    ], string='Sync Type', default='manual', readonly=True)
    state = fields.Selection([
        ('draft', 'Draft'),
        ('in_progress', 'In Progress'),
        ('done', 'Done'),
        ('failed', 'Failed'),
    ], string='Status', default='draft', readonly=True)
    products_synced = fields.Integer('Products Synced', readonly=True)
    errors = fields.Text('Errors', readonly=True)

    @api.model_create_multi
    def create(self, vals_list):
        """Override create to generate sequence numbers"""
        for vals in vals_list:
            if vals.get('name', 'New') == 'New':
                vals['name'] = self.env['ir.sequence'].next_by_code('shopify.inventory.sync') or 'New'
        return super(ShopifyInventorySync, self).create(vals_list)

    def sync_inventory_to_shopify(self):
        """Sync inventory quantities from Odoo to Shopify"""
        self.ensure_one()

        self.write({'state': 'in_progress'})

        try:
            instance = self.shopify_instance_id
            products = self.env['product.template'].search([
                ('shopify_instance_id', '=', instance.id),
                ('is_shopify_product', '=', True),
                ('shopify_product_id', '!=', False)
            ])

            synced_count = 0
            errors = []

            for product in products:
                try:
                    # Get inventory from Odoo
                    if product.product_variant_id:
                        qty_available = product.product_variant_id.qty_available

                        # Update in Shopify
                        self._update_shopify_inventory(instance, product, qty_available)
                        synced_count += 1
                except Exception as e:
                    error_msg = f"Product {product.name}: {str(e)}"
                    errors.append(error_msg)
                    _logger.error(error_msg)

            self.write({
                'state': 'done',
                'products_synced': synced_count,
                'errors': '\n'.join(errors) if errors else False
            })

            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Inventory Synced'),
                    'message': _('Successfully synced %s products') % synced_count,
                    'type': 'success',
                }
            }

        except Exception as e:
            self.write({
                'state': 'failed',
                'errors': str(e)
            })
            raise UserError(_('Inventory sync failed: %s') % str(e))

    def _update_shopify_inventory(self, instance, product, quantity):
        """Update inventory quantity in Shopify"""
        # First, get the inventory item ID
        variant_id = product.shopify_product_id
        url = f"{instance._get_base_url()}/products/{product.shopify_product_id}.json"
        response = requests.get(url, headers=instance._get_headers(), timeout=10, verify=certifi.where())

        if response.status_code == 200:
            product_data = response.json().get('product', {})
            variants = product_data.get('variants', [])
            if variants:
                inventory_item_id = variants[0].get('inventory_item_id')
                location_id = self._get_shopify_location_id(instance)

                # Update inventory level
                inventory_url = f"{instance._get_base_url()}/inventory_levels/set.json"
                inventory_data = {
                    'location_id': location_id,
                    'inventory_item_id': inventory_item_id,
                    'available': int(quantity)
                }

                inv_response = requests.post(
                    inventory_url,
                    headers=instance._get_headers(),
                    json=inventory_data,
                    timeout=10,
                    verify=certifi.where()
                )

                if inv_response.status_code not in [200, 201]:
                    raise UserError(_('Failed to update inventory: %s') % inv_response.text)

    def _get_shopify_location_id(self, instance):
        """Get the primary Shopify location ID"""
        url = f"{instance._get_base_url()}/locations.json"
        response = requests.get(url, headers=instance._get_headers(), timeout=10, verify=certifi.where())

        if response.status_code == 200:
            locations = response.json().get('locations', [])
            if locations:
                return locations[0].get('id')

        raise UserError(_('No Shopify location found'))
