# -*- coding: utf-8 -*-

from odoo import api, fields, models, _
from odoo.exceptions import UserError
import logging
import requests
import certifi

_logger = logging.getLogger(__name__)


class ShopifyCollection(models.Model):
    _name = 'shopify.collection'
    _description = 'Shopify Product Collection'
    _order = 'name'

    name = fields.Char('Collection Name', required=True)
    shopify_instance_id = fields.Many2one('shopify.instance', string='Shopify Instance', required=True, ondelete='cascade')
    shopify_collection_id = fields.Char('Shopify Collection ID', readonly=True)
    collection_type = fields.Selection([
        ('smart', 'Smart Collection'),
        ('custom', 'Custom Collection'),
    ], string='Type', default='custom')

    description = fields.Html('Description')
    published = fields.Boolean('Published', default=True)
    published_scope = fields.Selection([
        ('web', 'Online Store'),
        ('global', 'Online Store and Point of Sale'),
    ], string='Published Scope', default='web')

    sort_order = fields.Selection([
        ('alpha-asc', 'Alphabetically, A-Z'),
        ('alpha-desc', 'Alphabetically, Z-A'),
        ('best-selling', 'Best Selling'),
        ('created', 'Created (oldest first)'),
        ('created-desc', 'Created (newest first)'),
        ('manual', 'Manual'),
        ('price-asc', 'Price, low to high'),
        ('price-desc', 'Price, high to low'),
    ], string='Sort Order', default='manual')

    product_ids = fields.Many2many('product.template', string='Products')
    product_count = fields.Integer('Product Count', compute='_compute_product_count')

    image_url = fields.Char('Image URL')

    shopify_created_at = fields.Datetime('Created At (Shopify)', readonly=True)
    shopify_updated_at = fields.Datetime('Updated At (Shopify)', readonly=True)

    @api.depends('product_ids')
    def _compute_product_count(self):
        for record in self:
            record.product_count = len(record.product_ids)

    def sync_from_shopify(self):
        """Import collections from Shopify"""
        self.ensure_one()

        try:
            instance = self.shopify_instance_id
            total_collections = 0

            # Fetch custom collections
            url = f"{instance._get_base_url()}/custom_collections.json"
            response = requests.get(url, headers=instance._get_headers(), timeout=30, verify=certifi.where())

            if response.status_code == 200:
                collections = response.json().get('custom_collections', [])

                for collection_data in collections:
                    collection = self._create_or_update_collection(collection_data, instance, 'custom')
                    # Fetch products for this collection
                    if collection and collection.shopify_collection_id:
                        self._fetch_collection_products(collection, instance)
                    total_collections += 1

            # Fetch smart collections
            url = f"{instance._get_base_url()}/smart_collections.json"
            response = requests.get(url, headers=instance._get_headers(), timeout=30, verify=certifi.where())

            if response.status_code == 200:
                collections = response.json().get('smart_collections', [])

                for collection_data in collections:
                    collection = self._create_or_update_collection(collection_data, instance, 'smart')
                    # Fetch products for this collection
                    if collection and collection.shopify_collection_id:
                        self._fetch_collection_products(collection, instance)
                    total_collections += 1

            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Success'),
                    'message': _('Synced %s collections with products') % total_collections,
                    'type': 'success',
                }
            }

        except Exception as e:
            _logger.error(f'Error syncing collections: {str(e)}')
            raise UserError(_('Failed to sync collections: %s') % str(e))

    def _fetch_collection_products(self, collection, instance):
        """Fetch products that belong to a collection from Shopify"""
        try:
            url = f"{instance._get_base_url()}/collections/{collection.shopify_collection_id}/products.json"
            params = {'limit': 250}
            all_product_ids = []

            while True:
                response = requests.get(url, headers=instance._get_headers(), params=params, timeout=60, verify=certifi.where())

                if response.status_code != 200:
                    _logger.warning(f'Failed to fetch products for collection {collection.name}: {response.status_code}')
                    break

                products_data = response.json().get('products', [])
                if not products_data:
                    break

                # Find matching products in Odoo
                for product_data in products_data:
                    shopify_product_id = str(product_data.get('id'))
                    odoo_product = self.env['product.template'].search([
                        ('shopify_product_id', '=', shopify_product_id),
                        ('shopify_instance_id', '=', instance.id)
                    ], limit=1)

                    if odoo_product:
                        all_product_ids.append(odoo_product.id)

                # Check for pagination
                link_header = response.headers.get('Link', '')
                if 'rel="next"' in link_header:
                    for link in link_header.split(','):
                        if 'rel="next"' in link:
                            page_info = link.split('page_info=')[1].split('>')[0]
                            params = {'page_info': page_info}
                            break
                else:
                    break

            # Update collection with products
            if all_product_ids:
                collection.write({'product_ids': [(6, 0, all_product_ids)]})
                _logger.info(f'Collection "{collection.name}" linked to {len(all_product_ids)} products')

        except Exception as e:
            _logger.error(f'Error fetching products for collection {collection.name}: {str(e)}')

    def action_fetch_products(self):
        """Manual action to fetch products for this collection"""
        self.ensure_one()
        if not self.shopify_collection_id:
            raise UserError(_('This collection has not been synced from Shopify yet.'))

        self._fetch_collection_products(self, self.shopify_instance_id)

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Success'),
                'message': _('Fetched %s products for this collection') % self.product_count,
                'type': 'success',
            }
        }

    def _create_or_update_collection(self, collection_data, instance, collection_type):
        """Create or update collection and return the record"""
        shopify_id = str(collection_data.get('id'))

        existing = self.search([
            ('shopify_collection_id', '=', shopify_id),
            ('shopify_instance_id', '=', instance.id)
        ], limit=1)

        vals = {
            'name': collection_data.get('title', 'Untitled'),
            'shopify_instance_id': instance.id,
            'shopify_collection_id': shopify_id,
            'collection_type': collection_type,
            'description': collection_data.get('body_html', ''),
            'published': collection_data.get('published', True),
            'sort_order': collection_data.get('sort_order', 'manual'),
        }

        if collection_data.get('image'):
            vals['image_url'] = collection_data['image'].get('src', '')

        if existing:
            existing.write(vals)
            return existing
        else:
            return self.create(vals)
