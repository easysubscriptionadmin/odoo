# -*- coding: utf-8 -*-

from odoo import api, fields, models, _
from odoo.exceptions import UserError
import logging
import requests
import certifi
from dateutil import parser as date_parser

_logger = logging.getLogger(__name__)


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    shopify_instance_id = fields.Many2one('shopify.instance', string='Shopify Instance', ondelete='cascade')
    shopify_order_id = fields.Char('Shopify Order ID', readonly=True, copy=False)
    shopify_order_number = fields.Char('Shopify Order Number', readonly=True, copy=False)
    is_shopify_order = fields.Boolean('Is Shopify Order', default=False, copy=False)
    shopify_financial_status = fields.Selection([
        ('pending', 'Pending'),
        ('authorized', 'Authorized'),
        ('partially_paid', 'Partially Paid'),
        ('paid', 'Paid'),
        ('partially_refunded', 'Partially Refunded'),
        ('refunded', 'Refunded'),
        ('voided', 'Voided')
    ], string='Financial Status')
    shopify_fulfillment_status = fields.Selection([
        ('fulfilled', 'Fulfilled'),
        ('partial', 'Partial'),
        ('unfulfilled', 'Unfulfilled')
    ], string='Fulfillment Status')
    shopify_total_tax = fields.Float('Shopify Total Tax')
    shopify_total_shipping = fields.Float('Shopify Total Shipping')
    shopify_currency = fields.Char('Shopify Currency')
    shopify_created_at = fields.Datetime('Shopify Created At')
    shopify_updated_at = fields.Datetime('Shopify Updated At')
    shopify_closed_at = fields.Datetime('Shopify Closed At')
    shopify_cancelled_at = fields.Datetime('Shopify Cancelled At')
    shopify_cancel_reason = fields.Char('Cancel Reason')

    def import_shopify_orders(self, instance_id, date_from=None, batch_size=50):
        """Import orders from Shopify with batch processing for large data"""
        instance = self.env['shopify.instance'].browse(instance_id)
        if not instance:
            raise UserError(_('Shopify instance not found'))

        try:
            url = f"{instance._get_base_url()}/orders.json"
            params = {'limit': 250, 'status': 'any'}

            if date_from:
                params['created_at_min'] = date_from.isoformat()

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

                _logger.info(f'Fetching orders page {page_number}...')
                response = requests.get(url, headers=headers, params=request_params, timeout=60, verify=certifi.where())

                if response.status_code != 200:
                    raise UserError(_('Failed to fetch orders: %s - %s') % (response.status_code, response.text))

                data = response.json()
                orders = data.get('orders', [])

                if not orders:
                    break

                total_fetched += len(orders)
                _logger.info(f'Page {page_number}: Fetched {len(orders)} orders (Total: {total_fetched})')

                # Process orders in batches
                for i in range(0, len(orders), batch_size):
                    batch = orders[i:i + batch_size]
                    batch_created, batch_updated = self._process_order_batch(batch, instance)
                    created_count += batch_created
                    updated_count += batch_updated

                    # Commit after each batch to save progress and free memory
                    self.env.cr.commit()
                    _logger.info(f'Batch processed: Created {batch_created}, Updated {batch_updated}')

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

            _logger.info(f'Total orders processed: {total_fetched} (Created: {created_count}, Updated: {updated_count})')

            # Update sync timestamp
            instance.write({'last_order_sync': fields.Datetime.now()})

            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Orders Imported'),
                    'message': _('Total: %s, Created: %s, Updated: %s') % (total_fetched, created_count, updated_count),
                    'type': 'success',
                }
            }

        except Exception as e:
            _logger.error(f'Error importing orders: {str(e)}')
            raise UserError(_('Error importing orders: %s') % str(e))

    def _process_order_batch(self, orders_batch, instance):
        """Process a batch of orders"""
        created_count = 0
        updated_count = 0

        for order_data in orders_batch:
            try:
                order_vals = self._prepare_order_vals(order_data, instance)
                existing_order = self.search([
                    ('shopify_order_id', '=', str(order_data['id'])),
                    ('shopify_instance_id', '=', instance.id)
                ], limit=1)

                if existing_order:
                    # Update only if order is not confirmed yet
                    if existing_order.state in ['draft', 'sent']:
                        existing_order.write(order_vals)
                        # Create order lines if they don't exist
                        if not existing_order.order_line:
                            self._create_order_lines(existing_order, order_data.get('line_items', []))
                        updated_count += 1
                else:
                    new_order = self.create(order_vals)
                    # Create order lines
                    self._create_order_lines(new_order, order_data.get('line_items', []))
                    created_count += 1

            except Exception as e:
                _logger.error(f'Error processing order {order_data.get("id")}: {str(e)}')
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

    def _prepare_order_vals(self, order_data, instance):
        """Prepare order values from Shopify data"""
        # Get or create customer
        customer = self._get_or_create_customer(order_data.get('customer', {}), instance)

        vals = {
            'partner_id': customer.id,
            'shopify_instance_id': instance.id,
            'shopify_order_id': str(order_data['id']),
            'shopify_order_number': str(order_data.get('order_number', '')),
            'is_shopify_order': True,
            'shopify_financial_status': order_data.get('financial_status', 'pending'),
            'shopify_fulfillment_status': order_data.get('fulfillment_status') or 'unfulfilled',
            'shopify_total_tax': float(order_data.get('total_tax', 0.0)),
            'shopify_total_shipping': sum(float(line.get('price', 0.0)) for line in order_data.get('shipping_lines', [])),
            'shopify_currency': order_data.get('currency', 'USD'),
            'shopify_created_at': self._parse_shopify_datetime(order_data.get('created_at')),
            'shopify_updated_at': self._parse_shopify_datetime(order_data.get('updated_at')),
            'shopify_closed_at': self._parse_shopify_datetime(order_data.get('closed_at')),
            'shopify_cancelled_at': self._parse_shopify_datetime(order_data.get('cancelled_at')),
            'shopify_cancel_reason': order_data.get('cancel_reason', ''),
            'client_order_ref': order_data.get('name', ''),
            'note': order_data.get('note', ''),
            'date_order': self._parse_shopify_datetime(order_data.get('created_at')),
        }

        # Set currency from instance if available, otherwise try to get from Shopify currency code
        if instance.currency_id:
            vals['currency_id'] = instance.currency_id.id
        else:
            currency_code = order_data.get('currency', 'USD')
            currency = self.env['res.currency'].search([('name', '=', currency_code)], limit=1)
            if currency:
                vals['currency_id'] = currency.id

        # Set pricelist based on currency if possible
        if instance.currency_id:
            pricelist = self.env['product.pricelist'].search([('currency_id', '=', instance.currency_id.id)], limit=1)
            if pricelist:
                vals['pricelist_id'] = pricelist.id

        # Add shipping address if available
        shipping_address = order_data.get('shipping_address')
        if shipping_address:
            shipping_partner = self._get_or_create_shipping_address(shipping_address, customer)
            vals['partner_shipping_id'] = shipping_partner.id

        return vals

    def _get_or_create_customer(self, customer_data, instance):
        """Get or create customer from Shopify data"""
        if not customer_data:
            # Return a default customer or create anonymous
            return self.env.ref('base.public_partner')

        partner_obj = self.env['res.partner']

        # Try to find existing customer by Shopify ID
        if customer_data.get('id'):
            partner = partner_obj.search([
                ('shopify_customer_id', '=', str(customer_data['id'])),
                ('shopify_instance_id', '=', instance.id)
            ], limit=1)
            if partner:
                return partner

        # Try to find by email
        if customer_data.get('email'):
            partner = partner_obj.search([
                ('email', '=', customer_data['email'])
            ], limit=1)
            if partner:
                # Update with Shopify info
                partner.write({
                    'shopify_customer_id': str(customer_data['id']),
                    'shopify_instance_id': instance.id,
                    'is_shopify_customer': True,
                })
                return partner

        # Create new customer
        customer_vals = partner_obj._prepare_customer_vals(customer_data, instance)
        return partner_obj.create(customer_vals)

    def _get_or_create_shipping_address(self, shipping_address, parent_partner):
        """Get or create shipping address"""
        partner_obj = self.env['res.partner']

        # Check if address already exists as child
        existing = partner_obj.search([
            ('parent_id', '=', parent_partner.id),
            ('type', '=', 'delivery'),
            ('street', '=', shipping_address.get('address1', '')),
        ], limit=1)

        if existing:
            return existing

        # Create new shipping address
        vals = {
            'parent_id': parent_partner.id,
            'type': 'delivery',
            'name': f"{shipping_address.get('first_name', '')} {shipping_address.get('last_name', '')}".strip() or parent_partner.name,
            'street': shipping_address.get('address1', ''),
            'street2': shipping_address.get('address2', ''),
            'city': shipping_address.get('city', ''),
            'zip': shipping_address.get('zip', ''),
            'phone': shipping_address.get('phone', ''),
            'country_id': partner_obj._get_country_id(shipping_address.get('country_code')),
            'state_id': partner_obj._get_state_id(shipping_address.get('province_code'), shipping_address.get('country_code')),
        }

        return partner_obj.create(vals)

    def _create_order_lines(self, order, line_items):
        """Create order lines from Shopify line items"""
        order_line_obj = self.env['sale.order.line']

        _logger.info(f"Creating order lines for order {order.name}, line_items count: {len(line_items)}")

        for line_item in line_items:
            try:
                # Find or create product
                product = self._get_or_create_product(line_item, order.shopify_instance_id)

                if not product:
                    _logger.warning(f"Could not find/create product for line item: {line_item.get('title')}")
                    # Create a generic product if not found
                    product = self._create_generic_product(line_item, order.shopify_instance_id)

                if not product:
                    _logger.error(f"Failed to create product for line item: {line_item}")
                    continue

                # Get price - Shopify returns price as string
                price = line_item.get('price', '0')
                if isinstance(price, str):
                    price = float(price) if price else 0.0
                else:
                    price = float(price) if price else 0.0

                quantity = line_item.get('quantity', 1)
                if isinstance(quantity, str):
                    quantity = float(quantity) if quantity else 1.0
                else:
                    quantity = float(quantity) if quantity else 1.0

                line_vals = {
                    'order_id': order.id,
                    'product_id': product.id,
                    'name': line_item.get('title') or line_item.get('name') or product.name,
                    'product_uom_qty': quantity,
                    'price_unit': price,
                }

                order_line_obj.create(line_vals)
                _logger.info(f"Created order line: {line_item.get('title')} - Qty: {quantity} - Price: {price}")

            except Exception as e:
                _logger.error(f"Error creating order line for {line_item.get('title')}: {str(e)}")
                continue

    def _create_generic_product(self, line_item, instance):
        """Create a generic product when product cannot be found"""
        try:
            product_obj = self.env['product.template']

            price = line_item.get('price', '0')
            if isinstance(price, str):
                price = float(price) if price else 0.0

            product_vals = {
                'name': line_item.get('title') or line_item.get('name') or 'Shopify Product',
                'default_code': line_item.get('sku', ''),
                'list_price': price,
                'type': 'consu',  # Consumable product
                'shopify_instance_id': instance.id,
                'is_shopify_product': True,
            }

            product = product_obj.create(product_vals)
            _logger.info(f"Created generic product: {product.name}")
            return product.product_variant_id
        except Exception as e:
            _logger.error(f"Error creating generic product: {str(e)}")
            return None

    def _get_or_create_product(self, line_item, instance):
        """Get or create product from line item"""
        product_obj = self.env['product.template']

        # Try to find by Shopify product ID
        product_id = line_item.get('product_id')
        if product_id:
            product = product_obj.search([
                ('shopify_product_id', '=', str(product_id)),
                ('shopify_instance_id', '=', instance.id)
            ], limit=1)
            if product:
                return product.product_variant_id

        # Try to find by SKU
        sku = line_item.get('sku')
        if sku:
            product = product_obj.search([('default_code', '=', sku)], limit=1)
            if product:
                return product.product_variant_id

        # Get price - handle string from Shopify API
        price = line_item.get('price', '0')
        if isinstance(price, str):
            price = float(price) if price else 0.0
        else:
            price = float(price) if price else 0.0

        # Create new product
        product_vals = {
            'name': line_item.get('title') or line_item.get('name') or 'Unknown Product',
            'default_code': line_item.get('sku') or '',
            'list_price': price,
            'type': 'consu',  # Consumable product
            'shopify_instance_id': instance.id,
            'shopify_product_id': str(product_id) if product_id else False,
            'is_shopify_product': True,
        }

        try:
            product = product_obj.create(product_vals)
            _logger.info(f"Created product from order line: {product.name} - Price: {price}")
            return product.product_variant_id
        except Exception as e:
            _logger.error(f"Error creating product: {str(e)}")
            return None

    def export_order_to_shopify(self):
        """Export order to Shopify (create draft order)"""
        self.ensure_one()

        if not self.shopify_instance_id:
            raise UserError(_('Please select a Shopify instance first'))

        instance = self.shopify_instance_id

        try:
            # Prepare line items
            line_items = []
            for line in self.order_line:
                line_items.append({
                    'title': line.product_id.name,
                    'price': str(line.price_unit),
                    'quantity': int(line.product_uom_qty),
                    'sku': line.product_id.default_code or '',
                })

            order_data = {
                'draft_order': {
                    'line_items': line_items,
                    'customer': {
                        'id': int(self.partner_id.shopify_customer_id) if self.partner_id.shopify_customer_id else None,
                    } if self.partner_id.shopify_customer_id else None,
                    'note': self.note or '',
                    'email': self.partner_id.email or '',
                }
            }

            headers = instance._get_headers()
            url = f"{instance._get_base_url()}/draft_orders.json"
            response = requests.post(url, headers=headers, json=order_data, timeout=30, verify=certifi.where())

            if response.status_code == 201:
                result_data = response.json().get('draft_order', {})
                self.write({
                    'shopify_order_id': str(result_data['id']),
                    'is_shopify_order': True,
                })
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': _('Success'),
                        'message': _('Draft order created in Shopify successfully'),
                        'type': 'success',
                    }
                }
            else:
                raise UserError(_('Failed to export order: %s - %s') % (response.status_code, response.text))

        except Exception as e:
            _logger.error(f'Error exporting order: {str(e)}')
            raise UserError(_('Error exporting order: %s') % str(e))
