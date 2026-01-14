# -*- coding: utf-8 -*-

from odoo import api, fields, models, _
from odoo.exceptions import UserError
import logging
import requests
import certifi

_logger = logging.getLogger(__name__)


class ShopifyInventoryLocation(models.Model):
    _name = 'shopify.inventory.location'
    _description = 'Shopify Inventory by Location'
    _order = 'location_name'

    name = fields.Char('Name', compute='_compute_name', store=True)
    shopify_instance_id = fields.Many2one('shopify.instance', string='Shopify Instance', required=True, ondelete='cascade')
    shopify_location_id = fields.Char('Shopify Location ID', readonly=True)

    location_name = fields.Char('Location Name', required=True)
    location_type = fields.Selection([
        ('store', 'Retail Store'),
        ('warehouse', 'Warehouse'),
        ('dropship', 'Drop Shipping'),
        ('other', 'Other'),
    ], string='Location Type')

    address = fields.Char('Address')
    city = fields.Char('City')
    province = fields.Char('State/Province')
    country = fields.Char('Country')
    zip_code = fields.Char('ZIP/Postal Code')

    active_location = fields.Boolean('Active', default=True)

    inventory_line_ids = fields.One2many('shopify.inventory.location.line', 'location_id', string='Inventory Lines')

    # Computed totals
    total_products = fields.Integer('Total Products', compute='_compute_totals')
    total_quantity = fields.Integer('Total Quantity', compute='_compute_totals')

    @api.depends('inventory_line_ids', 'inventory_line_ids.available')
    def _compute_totals(self):
        for record in self:
            record.total_products = len(record.inventory_line_ids)
            record.total_quantity = sum(record.inventory_line_ids.mapped('available'))

    @api.depends('location_name', 'shopify_instance_id')
    def _compute_name(self):
        for record in self:
            record.name = f"{record.location_name} ({record.shopify_instance_id.name if record.shopify_instance_id else 'No Instance'})"

    @api.model
    def sync_locations_from_shopify(self, instance_id):
        """Import locations from Shopify"""
        instance = self.env['shopify.instance'].browse(instance_id)
        if not instance:
            raise UserError(_('Shopify instance not found'))

        try:
            url = f"{instance._get_base_url()}/locations.json"
            response = requests.get(url, headers=instance._get_headers(), timeout=30, verify=certifi.where())

            if response.status_code == 200:
                locations = response.json().get('locations', [])

                for location_data in locations:
                    self._create_or_update_location(location_data, instance)

                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': _('Success'),
                        'message': _('Synced %s locations') % len(locations),
                        'type': 'success',
                    }
                }
            else:
                raise UserError(_('Failed to fetch locations: %s') % response.text)

        except Exception as e:
            _logger.error(f'Error syncing locations: {str(e)}')
            raise UserError(_('Failed to sync locations: %s') % str(e))

    def _create_or_update_location(self, location_data, instance):
        """Create or update location"""
        shopify_id = str(location_data.get('id'))

        existing = self.search([
            ('shopify_location_id', '=', shopify_id),
            ('shopify_instance_id', '=', instance.id)
        ], limit=1)

        vals = {
            'shopify_instance_id': instance.id,
            'shopify_location_id': shopify_id,
            'location_name': location_data.get('name', 'Unnamed Location'),
            'address': location_data.get('address1', ''),
            'city': location_data.get('city', ''),
            'province': location_data.get('province', ''),
            'country': location_data.get('country', ''),
            'zip_code': location_data.get('zip', ''),
            'active_location': location_data.get('active', True),
        }

        if existing:
            existing.write(vals)
            return existing
        else:
            return self.create(vals)

    def action_sync_inventory(self):
        """Sync inventory for this location with product details"""
        self.ensure_one()
        import time

        try:
            instance = self.shopify_instance_id
            all_inventory_levels = []
            page_number = 1

            # Fetch all inventory levels - use simpler approach without pagination cursor
            # to avoid Cloudflare issues
            url = f"{instance._get_base_url()}/inventory_levels.json"
            params = {'location_ids': self.shopify_location_id, 'limit': 50}

            _logger.info(f'Fetching inventory for location {self.location_name} (ID: {self.shopify_location_id})')

            # Try with retry logic
            max_retries = 3
            last_error = None
            success = False

            for attempt in range(max_retries):
                try:
                    response = requests.get(
                        url,
                        headers=instance._get_headers(),
                        params=params,
                        timeout=30,
                        verify=certifi.where()
                    )

                    if response.status_code == 200:
                        # Check if response is actually JSON (not Cloudflare HTML)
                        content_type = response.headers.get('Content-Type', '')
                        if 'application/json' not in content_type and 'text/html' in content_type:
                            _logger.warning(f'Received HTML instead of JSON (attempt {attempt + 1}/{max_retries})')
                            last_error = 'Shopify returned an error page. This may be temporary.'
                            time.sleep(2)
                            continue

                        inventory_levels = response.json().get('inventory_levels', [])
                        all_inventory_levels.extend(inventory_levels)
                        _logger.info(f'Fetched {len(inventory_levels)} inventory levels')
                        success = True
                        break
                    elif response.status_code == 429:  # Rate limited
                        _logger.warning('Rate limited, waiting 2 seconds...')
                        last_error = 'Rate limited by Shopify API'
                        time.sleep(2)
                        continue
                    elif response.status_code >= 500:
                        _logger.warning(f'Server error {response.status_code}, attempt {attempt + 1}/{max_retries}')
                        last_error = f'Shopify server error (HTTP {response.status_code})'
                        time.sleep(2)
                        continue
                    else:
                        raise UserError(_('Failed to fetch inventory: HTTP %s') % response.status_code)

                except requests.exceptions.Timeout:
                    _logger.warning(f'Request timeout, attempt {attempt + 1}/{max_retries}')
                    last_error = 'Request timed out'
                    if attempt < max_retries - 1:
                        time.sleep(1)
                        continue
                    raise UserError(_('Request timed out after %s attempts. Please try again later.') % max_retries)

            # Check if all retries failed
            if not success and not all_inventory_levels:
                raise UserError(_('Failed to fetch inventory after %s attempts. %s. Please try again later.') % (max_retries, last_error or 'Unknown error'))

            if not all_inventory_levels:
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': _('Info'),
                        'message': _('No inventory data found for this location'),
                        'type': 'warning',
                    }
                }

            _logger.info(f'Total inventory levels fetched: {len(all_inventory_levels)}')

            # Clear existing lines
            self.inventory_line_ids.unlink()

            # Create inventory lines - match products from Odoo database
            created_count = 0
            linked_count = 0

            for level in all_inventory_levels:
                inventory_item_id = str(level.get('inventory_item_id'))
                available = level.get('available') or 0

                # Try to find product variant by inventory_item_id
                variant = self.env['product.product'].search([
                    ('shopify_inventory_item_id', '=', inventory_item_id)
                ], limit=1)

                line_vals = {
                    'location_id': self.id,
                    'inventory_item_id': inventory_item_id,
                    'available': available,
                }

                if variant:
                    line_vals['product_variant_id'] = variant.id
                    line_vals['product_id'] = variant.product_tmpl_id.id
                    linked_count += 1

                self.env['shopify.inventory.location.line'].create(line_vals)
                created_count += 1

            # Commit to save progress
            self.env.cr.commit()

            message = _('Synced %s items (%s linked to products)') % (created_count, linked_count)
            if linked_count < created_count:
                message += _('\n\nTip: Sync products first to link inventory items to products.')

            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Success'),
                    'message': message,
                    'type': 'success',
                    'sticky': True,
                }
            }

        except UserError:
            raise
        except Exception as e:
            _logger.error(f'Error syncing inventory: {str(e)}')
            raise UserError(_('Failed to sync inventory: %s') % str(e))

    def action_sync_inventory_with_products(self):
        """Sync inventory and try to fetch product details from Shopify"""
        self.ensure_one()
        import time

        try:
            instance = self.shopify_instance_id

            # First sync basic inventory
            self.action_sync_inventory()

            # Then try to link unlinked items to products
            unlinked_lines = self.inventory_line_ids.filtered(lambda l: not l.product_variant_id)
            if not unlinked_lines:
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': _('Success'),
                        'message': _('All inventory items are already linked to products'),
                        'type': 'success',
                    }
                }

            # Try to fetch inventory items info in small batches
            inventory_item_ids = unlinked_lines.mapped('inventory_item_id')
            item_to_variant_map = self._fetch_inventory_items_info(instance, inventory_item_ids)

            # Update lines with product info
            linked_count = 0
            for line in unlinked_lines:
                if line.inventory_item_id in item_to_variant_map:
                    variant_info = item_to_variant_map[line.inventory_item_id]
                    if variant_info.get('variant_id'):
                        variant = self.env['product.product'].search([
                            ('shopify_variant_id', '=', str(variant_info['variant_id']))
                        ], limit=1)
                        if variant:
                            variant.write({'shopify_inventory_item_id': line.inventory_item_id})
                            line.write({
                                'product_variant_id': variant.id,
                                'product_id': variant.product_tmpl_id.id,
                            })
                            linked_count += 1

            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Success'),
                    'message': _('Linked %s additional items to products') % linked_count,
                    'type': 'success',
                }
            }

        except Exception as e:
            _logger.error(f'Error syncing inventory with products: {str(e)}')
            raise UserError(_('Failed to sync: %s') % str(e))

    def _fetch_inventory_items_info(self, instance, inventory_item_ids):
        """Fetch inventory item details from Shopify to get variant mapping"""
        import time
        item_to_variant_map = {}

        if not inventory_item_ids:
            return item_to_variant_map

        # Process in smaller batches of 10 to avoid API issues
        batch_size = 10
        for i in range(0, len(inventory_item_ids), batch_size):
            batch_ids = inventory_item_ids[i:i + batch_size]
            ids_param = ','.join(batch_ids)

            url = f"{instance._get_base_url()}/inventory_items.json"
            params = {'ids': ids_param}

            try:
                response = requests.get(
                    url,
                    headers=instance._get_headers(),
                    params=params,
                    timeout=30,
                    verify=certifi.where()
                )

                if response.status_code == 200:
                    items = response.json().get('inventory_items', [])
                    for item in items:
                        item_id = str(item.get('id'))
                        item_to_variant_map[item_id] = {
                            'variant_id': item.get('variant_id'),
                            'sku': item.get('sku', ''),
                            'tracked': item.get('tracked', True),
                        }
                elif response.status_code == 429:
                    _logger.warning('Rate limited on inventory_items, skipping batch')
                    time.sleep(1)
                else:
                    _logger.warning(f'Failed to fetch inventory items batch: {response.status_code}')

                # Small delay between batches to avoid rate limiting
                time.sleep(0.5)

            except Exception as e:
                _logger.warning(f'Error fetching inventory items batch: {str(e)}')
                continue

        return item_to_variant_map


class ShopifyInventoryLocationLine(models.Model):
    _name = 'shopify.inventory.location.line'
    _description = 'Shopify Inventory Location Line'
    _order = 'product_name, inventory_item_id'

    location_id = fields.Many2one('shopify.inventory.location', string='Location', required=True, ondelete='cascade')
    inventory_item_id = fields.Char('Inventory Item ID', required=True)
    product_id = fields.Many2one('product.template', string='Product')
    product_variant_id = fields.Many2one('product.product', string='Product Variant')
    product_name = fields.Char('Product Name', compute='_compute_product_info', store=True)
    product_sku = fields.Char('SKU', compute='_compute_product_info', store=True)
    available = fields.Integer('Available Quantity')

    @api.depends('product_id', 'product_variant_id')
    def _compute_product_info(self):
        for record in self:
            if record.product_variant_id:
                record.product_name = record.product_variant_id.display_name
                record.product_sku = record.product_variant_id.default_code or ''
            elif record.product_id:
                record.product_name = record.product_id.name
                record.product_sku = record.product_id.default_code or ''
            else:
                record.product_name = f'Unknown (Item ID: {record.inventory_item_id})'
                record.product_sku = ''

