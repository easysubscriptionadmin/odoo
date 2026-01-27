# -*- coding: utf-8 -*-

from odoo import api, fields, models, _
from odoo.exceptions import UserError
import logging
import requests
import certifi
import base64
from dateutil import parser as date_parser

_logger = logging.getLogger(__name__)


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    shopify_instance_id = fields.Many2one('shopify.instance', string='Shopify Instance', ondelete='cascade')
    shopify_product_id = fields.Char('Shopify Product ID', readonly=True, copy=False)
    is_shopify_product = fields.Boolean('Is Shopify Product', default=False, copy=False)
    shopify_product_status = fields.Selection([
        ('active', 'Active'),
        ('draft', 'Draft'),
        ('archived', 'Archived')
    ], string='Shopify Status', default='active')
    shopify_product_type = fields.Char('Shopify Product Type')
    shopify_vendor = fields.Char('Shopify Vendor')
    shopify_tags = fields.Char('Shopify Tags')
    shopify_published_at = fields.Datetime('Published At')
    shopify_created_at = fields.Datetime('Shopify Created At')
    shopify_updated_at = fields.Datetime('Shopify Updated At')

    def import_shopify_products(self, instance_id, batch_size=25, skip_images=True):
        """Import products from Shopify with batch processing

        Args:
            instance_id: Shopify instance ID
            batch_size: Number of products to process per batch (default 25 for stability)
            skip_images: Skip image download during bulk import for speed (default True)
        """
        instance = self.env['shopify.instance'].browse(instance_id)
        if not instance:
            raise UserError(_('Shopify instance not found'))

        try:
            url = f"{instance._get_base_url()}/products.json"
            params = {'limit': 250}
            headers = instance._get_headers()

            created_count = 0
            updated_count = 0
            total_fetched = 0
            page_info = None
            page_number = 1

            while True:
                # When using page_info, only pass page_info parameter (Shopify API requirement)
                if page_info:
                    request_params = {'page_info': page_info}
                else:
                    request_params = params

                _logger.info(f'Fetching products page {page_number}...')
                response = requests.get(url, headers=headers, params=request_params, timeout=60, verify=certifi.where())

                if response.status_code != 200:
                    raise UserError(_('Failed to fetch products: %s - %s') % (response.status_code, response.text))

                data = response.json()
                products = data.get('products', [])

                if not products:
                    _logger.info('No more products to fetch')
                    break

                total_fetched += len(products)
                _logger.info(f'Page {page_number}: Fetched {len(products)} products (Total: {total_fetched})')

                # Process products in smaller batches
                for i in range(0, len(products), batch_size):
                    batch = products[i:i + batch_size]
                    batch_num = (i // batch_size) + 1
                    _logger.info(f'Processing batch {batch_num} of {(len(products) + batch_size - 1) // batch_size}...')

                    try:
                        batch_created, batch_updated = self._process_product_batch(batch, instance, skip_images=skip_images)
                        created_count += batch_created
                        updated_count += batch_updated

                        # Commit after each batch to save progress
                        self.env.cr.commit()
                        _logger.info(f'Batch {batch_num} complete: Created {batch_created}, Updated {batch_updated}')
                    except Exception as batch_error:
                        _logger.error(f'Error processing batch {batch_num}: {str(batch_error)}')
                        # Rollback failed batch and continue
                        self.env.cr.rollback()
                        continue

                # Check for pagination
                link_header = response.headers.get('Link', '')
                if 'rel="next"' in link_header:
                    for link in link_header.split(','):
                        if 'rel="next"' in link:
                            page_info = link.split('page_info=')[1].split('>')[0]
                            break
                    page_number += 1
                else:
                    break

            _logger.info(f'Product import complete! Total: {total_fetched}, Created: {created_count}, Updated: {updated_count}')

            # Update sync timestamp
            instance.write({'last_product_sync': fields.Datetime.now()})

            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Products Imported'),
                    'message': _('Total: %s, Created: %s, Updated: %s') % (total_fetched, created_count, updated_count),
                    'type': 'success',
                }
            }

        except Exception as e:
            _logger.error(f'Error importing products: {str(e)}')
            raise UserError(_('Error importing products: %s') % str(e))

    def _process_product_batch(self, products_batch, instance, skip_images=True):
        """Process a batch of products

        Args:
            products_batch: List of product data from Shopify
            instance: Shopify instance record
            skip_images: Skip image download for faster processing
        """
        created_count = 0
        updated_count = 0

        for product_data in products_batch:
            try:
                product_vals = self._prepare_product_vals(product_data, instance, skip_images=skip_images)
                existing_product = self.search([
                    ('shopify_product_id', '=', str(product_data['id'])),
                    ('shopify_instance_id', '=', instance.id)
                ], limit=1)

                if existing_product:
                    existing_product.write(product_vals)
                    updated_count += 1
                    _logger.debug(f'Updated product: {product_data.get("title")}')
                else:
                    self.create(product_vals)
                    created_count += 1
                    _logger.debug(f'Created product: {product_data.get("title")}')
            except Exception as e:
                _logger.error(f'Error processing product {product_data.get("id")} ({product_data.get("title")}): {str(e)}')
                continue

        return created_count, updated_count

    def _parse_shopify_datetime(self, datetime_str):
        """Parse Shopify datetime string to Odoo datetime"""
        if not datetime_str:
            return False
        try:
            # Parse ISO 8601 format and convert to naive datetime (remove timezone)
            dt = date_parser.parse(datetime_str)
            return dt.replace(tzinfo=None)
        except:
            return False

    def _download_image(self, image_url):
        """Download image from URL and return base64 encoded data"""
        try:
            if not image_url:
                return False

            response = requests.get(image_url, timeout=30, verify=certifi.where())
            if response.status_code == 200:
                return base64.b64encode(response.content)
            return False
        except Exception as e:
            _logger.warning(f'Failed to download image from {image_url}: {str(e)}')
            return False

    def _prepare_product_vals(self, product_data, instance, skip_images=True):
        """Prepare product values from Shopify data

        Args:
            product_data: Product data from Shopify API
            instance: Shopify instance record
            skip_images: Skip image download for faster bulk processing
        """
        # Safely get price from variants
        price = 0.0
        sku = ''
        variants = product_data.get('variants', [])
        if variants and len(variants) > 0:
            first_variant = variants[0]
            price_str = first_variant.get('price', '0')
            if isinstance(price_str, str):
                price = float(price_str) if price_str else 0.0
            else:
                price = float(price_str) if price_str else 0.0
            sku = first_variant.get('sku', '') or ''

        vals = {
            'name': product_data.get('title', 'Unnamed Product'),
            'description': product_data.get('body_html', ''),
            'shopify_instance_id': instance.id,
            'shopify_product_id': str(product_data['id']),
            'is_shopify_product': True,
            'shopify_product_status': product_data.get('status', 'active'),
            'shopify_product_type': product_data.get('product_type', ''),
            'shopify_vendor': product_data.get('vendor', ''),
            'shopify_tags': product_data.get('tags', ''),
            'shopify_published_at': self._parse_shopify_datetime(product_data.get('published_at')),
            'shopify_created_at': self._parse_shopify_datetime(product_data.get('created_at')),
            'shopify_updated_at': self._parse_shopify_datetime(product_data.get('updated_at')),
            'list_price': price,
            'default_code': sku,
            'sale_ok': True,
            'purchase_ok': True,
        }

        # Set currency from instance if available
        if instance.currency_id:
            vals['currency_id'] = instance.currency_id.id

        # Download and set product image (skip during bulk import for speed)
        if not skip_images:
            images = product_data.get('images', [])
            if images and len(images) > 0:
                main_image_url = images[0].get('src')
                if main_image_url:
                    image_data = self._download_image(main_image_url)
                    if image_data:
                        vals['image_1920'] = image_data

        return vals

    def export_product_to_shopify(self):
        """Export a single product to Shopify"""
        self.ensure_one()

        if not self.shopify_instance_id:
            raise UserError(_('Please select a Shopify instance first'))

        instance = self.shopify_instance_id

        try:
            # Check if product has variants
            has_variants = len(self.product_variant_ids) > 1

            product_data = {
                'product': {
                    'title': self.name,
                    'body_html': self.description or '',
                    'vendor': self.shopify_vendor or 'Odoo',
                    'product_type': self.shopify_product_type or '',
                    'tags': self.shopify_tags or '',
                    'status': self.shopify_product_status or 'active',
                }
            }

            # Add options (required by Shopify even for products without variants)
            if has_variants:
                # Get variant attributes
                product_data['product']['options'] = []
                for attribute_line in self.attribute_line_ids:
                    product_data['product']['options'].append({
                        'name': attribute_line.attribute_id.name,
                        'values': [v.name for v in attribute_line.value_ids]
                    })

                # Add all variants
                product_data['product']['variants'] = []
                for variant in self.product_variant_ids:
                    variant_data = {
                        'price': str(variant.lst_price),
                        'sku': variant.default_code or '',
                        'inventory_management': 'shopify',
                        'option1': variant.product_template_attribute_value_ids[0].name if len(variant.product_template_attribute_value_ids) > 0 else 'Default',
                    }
                    if len(variant.product_template_attribute_value_ids) > 1:
                        variant_data['option2'] = variant.product_template_attribute_value_ids[1].name
                    if len(variant.product_template_attribute_value_ids) > 2:
                        variant_data['option3'] = variant.product_template_attribute_value_ids[2].name

                    product_data['product']['variants'].append(variant_data)
            else:
                # Single variant product - add default option
                product_data['product']['options'] = [{'name': 'Title', 'values': ['Default Title']}]
                product_data['product']['variants'] = [{
                    'price': str(self.list_price),
                    'sku': self.default_code or '',
                    'inventory_management': 'shopify',
                    'option1': 'Default Title',
                }]

            headers = instance._get_headers()

            if self.shopify_product_id:
                # Update existing product
                url = f"{instance._get_base_url()}/products/{self.shopify_product_id}.json"
                response = requests.put(url, headers=headers, json=product_data, timeout=30, verify=certifi.where())
            else:
                # Create new product
                url = f"{instance._get_base_url()}/products.json"
                response = requests.post(url, headers=headers, json=product_data, timeout=30, verify=certifi.where())

            if response.status_code in [200, 201]:
                result_data = response.json().get('product', {})

                # Parse Shopify datetime (ISO 8601 format with timezone) and convert to naive datetime
                updated_at = result_data.get('updated_at')
                if updated_at:
                    try:
                        # Parse the datetime with timezone info
                        parsed_dt = date_parser.parse(updated_at)
                        # Convert to naive datetime (remove timezone info) for Odoo
                        updated_at = parsed_dt.replace(tzinfo=None)
                    except Exception as e:
                        _logger.warning(f'Failed to parse updated_at: {updated_at}, error: {str(e)}')
                        updated_at = fields.Datetime.now()
                else:
                    updated_at = fields.Datetime.now()

                self.write({
                    'shopify_product_id': str(result_data['id']),
                    'is_shopify_product': True,
                    'shopify_updated_at': updated_at,
                })
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': _('Success'),
                        'message': _('Product exported to Shopify successfully'),
                        'type': 'success',
                    }
                }
            else:
                raise UserError(_('Failed to export product: %s - %s') % (response.status_code, response.text))

        except Exception as e:
            _logger.error(f'Error exporting product: {str(e)}')
            raise UserError(_('Error exporting product: %s') % str(e))


