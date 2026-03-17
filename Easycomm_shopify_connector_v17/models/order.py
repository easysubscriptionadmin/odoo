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
    shopify_fulfillment_id = fields.Char('Shopify Fulfillment ID', readonly=True, copy=False)
    shopify_refund_id = fields.Char('Shopify Refund ID', readonly=True, copy=False)

    def import_shopify_orders(self, instance_id, date_from=None, date_to=None,
                              batch_size=20, notify_uid=None):
        """Import orders from Shopify.

        Fetches pages of up to 250 orders from Shopify and processes them in
        sub-batches of `batch_size` (default 20).  After every batch a bus
        notification is sent to `notify_uid` so the user sees live progress
        toasts without blocking the UI.
        """
        instance = self.env['shopify.instance'].browse(instance_id)
        if not instance:
            raise UserError(_('Shopify instance not found'))

        def _notify(title, message, msg_type='info'):
            """Push a toast to the requesting user via the Odoo bus."""
            if not notify_uid:
                return
            try:
                partner = self.env['res.users'].browse(notify_uid).partner_id
                self.env['bus.bus']._sendone(partner, 'simple_notification', {
                    'title': title,
                    'message': message,
                    'type': msg_type,
                })
                self.env.cr.commit()
            except Exception as bus_err:
                _logger.warning(f'Bus notification failed: {bus_err}')

        try:
            url = f"{instance._get_base_url()}/orders.json"
            params = {'limit': 250, 'status': 'any'}

            if date_from:
                params['created_at_min'] = date_from.isoformat()
            if date_to:
                params['created_at_max'] = date_to.isoformat()

            headers = instance._get_headers()

            created_count = 0
            updated_count = 0
            total_fetched = 0
            page_info = None
            page_number = 1

            while True:
                # When using page_info, only pass page_info (Shopify API requirement)
                request_params = {'page_info': page_info} if page_info else params

                _logger.info(f'Fetching orders page {page_number}...')
                response = requests.get(
                    url, headers=headers, params=request_params,
                    timeout=60, verify=certifi.where()
                )

                if response.status_code != 200:
                    raise UserError(_('Failed to fetch orders: %s - %s') % (
                        response.status_code, response.text))

                orders = response.json().get('orders', [])
                if not orders:
                    break

                total_fetched += len(orders)
                _logger.info(
                    f'Page {page_number}: fetched {len(orders)} orders '
                    f'(total so far: {total_fetched})'
                )

                # Process and notify every `batch_size` orders
                for i in range(0, len(orders), batch_size):
                    batch = orders[i:i + batch_size]
                    batch_created, batch_updated = self._process_order_batch(batch, instance)
                    created_count += batch_created
                    updated_count += batch_updated

                    # Commit after each micro-batch
                    self.env.cr.commit()
                    _logger.info(
                        f'Batch {i // batch_size + 1}: '
                        f'created={batch_created}, updated={batch_updated}'
                    )

                    # Notify user after each batch of 20
                    _notify(
                        _('Order Import Progress'),
                        _('Fetched %s orders so far — Created: %s | Updated: %s') % (
                            total_fetched, created_count, updated_count
                        ),
                        'info',
                    )

                # Pagination
                link_header = response.headers.get('Link', '')
                if 'rel="next"' in link_header:
                    for link in link_header.split(','):
                        if 'rel="next"' in link:
                            page_info = link.split('page_info=')[1].split('>')[0]
                            break
                    page_number += 1
                else:
                    break

            _logger.info(
                f'Order import complete — total={total_fetched}, '
                f'created={created_count}, updated={updated_count}'
            )
            instance.write({'last_order_sync': fields.Datetime.now()})

            _notify(
                _('Order Import Complete'),
                _('All done! Total: %s | Created: %s | Updated: %s') % (
                    total_fetched, created_count, updated_count
                ),
                'success',
            )

            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Orders Imported'),
                    'message': _('Total: %s, Created: %s, Updated: %s') % (
                        total_fetched, created_count, updated_count),
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

                ctx = {'shopify_sync_skip': True}
                if existing_order:
                    # Update only if order is not confirmed yet
                    if existing_order.state in ['draft', 'sent']:
                        existing_order.with_context(**ctx).write(order_vals)
                        # Create order lines if they don't exist
                        if not existing_order.order_line:
                            self._create_order_lines(existing_order, order_data.get('line_items', []))
                        updated_count += 1
                else:
                    new_order = self.with_context(**ctx).create(order_vals)
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
                    'shopify_line_id': str(line_item.get('id', '')),
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

    # ─────────────────────────────────────────────────────────────────────────
    # Odoo → Shopify push methods
    # ─────────────────────────────────────────────────────────────────────────

    @api.model_create_multi
    def create(self, vals_list):
        orders = super().create(vals_list)
        if self.env.context.get('shopify_sync_skip'):
            return orders
        for order in orders:
            # Auto-push to Shopify when a Shopify instance is assigned but no
            # order ID yet (prevents re-pushing orders that just came from import)
            if order.shopify_instance_id and not order.shopify_order_id:
                try:
                    order.with_context(shopify_sync_skip=True)._create_order_in_shopify()
                except Exception as e:
                    _logger.warning(f'Auto-create order in Shopify failed for {order.name}: {str(e)}')
        return orders

    def _create_order_in_shopify(self):
        """Create this sale order as a Shopify draft order and store the returned IDs."""
        self.ensure_one()
        instance = self.shopify_instance_id
        if not instance:
            return

        line_items = []
        for line in self.order_line:
            item = {
                'title': line.name or (line.product_id.name if line.product_id else 'Custom Item'),
                'quantity': int(line.product_uom_qty),
                'price': str(line.price_unit),
            }
            if line.product_id and line.product_id.shopify_variant_id:
                item['variant_id'] = int(line.product_id.shopify_variant_id)
            line_items.append(item)

        draft_order_payload = {
            'draft_order': {
                'line_items': line_items,
                'email': self.partner_id.email or '',
                'note': self.note or '',
            }
        }

        # Link Shopify customer if available
        if self.partner_id and self.partner_id.shopify_customer_id:
            draft_order_payload['draft_order']['customer'] = {
                'id': int(self.partner_id.shopify_customer_id)
            }

        # Shipping address
        ship = self.partner_shipping_id or self.partner_id
        if ship and (ship.street or ship.city):
            name_parts = (ship.name or '').split(' ', 1)
            draft_order_payload['draft_order']['shipping_address'] = {
                'first_name': name_parts[0],
                'last_name': name_parts[1] if len(name_parts) > 1 else '',
                'address1': ship.street or '',
                'address2': ship.street2 or '',
                'city': ship.city or '',
                'province': ship.state_id.name if ship.state_id else '',
                'country': ship.country_id.code if ship.country_id else '',
                'zip': ship.zip or '',
                'phone': ship.phone or ship.mobile or '',
            }

        url = f"{instance._get_base_url()}/draft_orders.json"
        response = requests.post(
            url,
            headers=instance._get_headers(),
            json=draft_order_payload,
            timeout=30,
            verify=certifi.where(),
        )

        if response.status_code == 201:
            result = response.json().get('draft_order', {})
            self.with_context(shopify_sync_skip=True).write({
                'shopify_order_id': str(result['id']),
                'is_shopify_order': True,
                'shopify_order_number': str(result.get('order_number') or result.get('id', '')),
                'shopify_created_at': self._parse_shopify_datetime(result.get('created_at')),
                'shopify_updated_at': self._parse_shopify_datetime(result.get('updated_at')),
            })
            _logger.info(f'Order {self.name} created in Shopify as draft order #{result["id"]}')
        else:
            _logger.warning(
                f'Failed to create Shopify draft order for {self.name}: '
                f'{response.status_code} - {response.text}'
            )

    def write(self, vals):
        result = super().write(vals)
        if self.env.context.get('shopify_sync_skip'):
            return result
        # Auto-sync note/reference/customer changes back to Shopify
        sync_trigger_fields = {'note', 'client_order_ref', 'partner_id'}
        if any(f in vals for f in sync_trigger_fields):
            for order in self:
                if order.is_shopify_order and order.shopify_order_id and order.shopify_instance_id:
                    _logger.info(
                        f'[SaleOrder.write] Auto-syncing order {order.name} '
                        f'(Shopify #{order.shopify_order_id}) — changed fields: '
                        f'{[f for f in sync_trigger_fields if f in vals]}'
                    )
                    try:
                        order.with_context(shopify_sync_skip=True).push_order_update_to_shopify()
                    except Exception as e:
                        _logger.warning(f'Auto-sync order to Shopify failed for {order.name}: {str(e)}')
        return result

    def action_cancel(self):
        result = super().action_cancel()
        if self.env.context.get('shopify_sync_skip'):
            return result
        for order in self:
            if not (order.is_shopify_order and order.shopify_order_id and order.shopify_instance_id):
                continue
            if order.shopify_cancelled_at:
                # Already cancelled in Shopify (e.g. via the manual button)
                continue
            _logger.info(
                f'[SaleOrder.action_cancel] Auto-cancelling Shopify order for {order.name} '
                f'(Shopify #{order.shopify_order_id})'
            )
            try:
                order.with_context(shopify_sync_skip=True).cancel_order_in_shopify()
            except Exception as e:
                _logger.warning(
                    f'[SaleOrder.action_cancel] Shopify cancel failed for {order.name}: {str(e)}'
                )
        return result

    def _build_shopify_address(self, partner):
        """Build a Shopify MailingAddressInput dict from an Odoo res.partner record."""
        if not partner:
            return {}
        name_parts = (partner.name or '').split(' ', 1)
        addr = {
            'firstName': name_parts[0] if name_parts else '',
            'lastName': name_parts[1] if len(name_parts) > 1 else '',
            'address1': partner.street or '',
            'address2': partner.street2 or '',
            'city': partner.city or '',
            'zip': partner.zip or '',
            'phone': partner.phone or partner.mobile or '',
            'company': partner.commercial_company_name or '',
        }
        if partner.country_id:
            addr['countryCode'] = partner.country_id.code
        if partner.state_id:
            addr['provinceCode'] = partner.state_id.code
        return addr

    def push_order_update_to_shopify(self):
        """Push note/reference/customer changes to Shopify using orderUpdate GraphQL mutation."""
        self.ensure_one()
        if not self.shopify_order_id or not self.shopify_instance_id:
            raise UserError(_('This order is not linked to a Shopify instance'))

        instance = self.shopify_instance_id
        order_gid = f"gid://shopify/Order/{self.shopify_order_id}"

        mutation = """
        mutation orderUpdate($input: OrderInput!) {
          orderUpdate(input: $input) {
            order {
              id
              email
              note
              updatedAt
            }
            userErrors {
              field
              message
            }
          }
        }
        """

        input_vars = {
            "id": order_gid,
            "note": self.note or "",
            "email": self.partner_id.email or "",
        }

        # Sync shipping address from the delivery partner (or invoice partner as fallback)
        shipping_partner = self.partner_shipping_id or self.partner_id
        if shipping_partner and (shipping_partner.street or shipping_partner.city):
            addr = self._build_shopify_address(shipping_partner)
            if addr:
                input_vars['shippingAddress'] = addr
                _logger.info(
                    f'[push_order_update_to_shopify] Including shippingAddress for '
                    f'partner "{shipping_partner.name}"'
                )

        variables = {"input": input_vars}

        try:
            data = instance._execute_graphql(mutation, variables)
            result = data.get('orderUpdate', {})

            user_errors = result.get('userErrors', [])
            if user_errors:
                msgs = [f"{e.get('field', '')}: {e.get('message', '')}" for e in user_errors]
                raise UserError(_('Shopify errors: %s') % ', '.join(msgs))

            order_data = result.get('order', {})
            if order_data.get('updatedAt'):
                updated_at = date_parser.parse(order_data['updatedAt']).replace(tzinfo=None)
                self.with_context(shopify_sync_skip=True).write({'shopify_updated_at': updated_at})

            _logger.info(
                f'[push_order_update_to_shopify] Order {self.name} synced — '
                f'email={order_data.get("email")!r}'
            )

        except UserError:
            raise
        except Exception as e:
            _logger.error(f'Error pushing order update to Shopify: {str(e)}')
            raise UserError(_('Error syncing order to Shopify: %s') % str(e))

        # Also sync order line changes (qty/price) if any lines are linked
        lines_synced_msg = ''
        if self.order_line.filtered(lambda l: l.shopify_line_id):
            try:
                self.with_context(shopify_sync_skip=True).sync_order_lines_to_shopify()
                lines_synced_msg = ' Order lines (qty/price) also synced.'
            except Exception as e:
                _logger.warning(f'Line sync skipped: {str(e)}')
                lines_synced_msg = ' (Line sync skipped: %s)' % str(e)

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Order Synced'),
                'message': _('Order synced to Shopify successfully.') + lines_synced_msg,
                'type': 'success',
            }
        }

    def cancel_order_in_shopify(self):
        """Cancel the order in Shopify using orderCancel GraphQL mutation"""
        self.ensure_one()
        if not self.shopify_order_id or not self.shopify_instance_id:
            raise UserError(_('This order is not linked to a Shopify instance'))

        instance = self.shopify_instance_id
        order_gid = f"gid://shopify/Order/{self.shopify_order_id}"

        mutation = """
        mutation orderCancel($orderId: ID!, $reason: OrderCancelReason!, $notifyCustomer: Boolean!, $refund: Boolean!, $restock: Boolean!) {
          orderCancel(orderId: $orderId, reason: $reason, notifyCustomer: $notifyCustomer, refund: $refund, restock: $restock) {
            job {
              id
            }
            orderCancelUserErrors {
              field
              message
              code
            }
          }
        }
        """

        variables = {
            "orderId": order_gid,
            "reason": "OTHER",
            "notifyCustomer": True,
            "refund": False,
            "restock": True,
        }

        try:
            data = instance._execute_graphql(mutation, variables)
            result = data.get('orderCancel', {})

            user_errors = result.get('orderCancelUserErrors', [])
            if user_errors:
                msgs = [f"{e.get('field', '')}: {e.get('message', '')}" for e in user_errors]
                raise UserError(_('Shopify errors: %s') % ', '.join(msgs))

            self.with_context(shopify_sync_skip=True).write({
                'shopify_cancelled_at': fields.Datetime.now(),
                'shopify_cancel_reason': 'other',
            })

            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Order Cancelled'),
                    'message': _('Order cancelled in Shopify and inventory restocked'),
                    'type': 'success',
                }
            }
        except UserError:
            raise
        except Exception as e:
            _logger.error(f'Error cancelling order in Shopify: {str(e)}')
            raise UserError(_('Error cancelling order in Shopify: %s') % str(e))

    def create_fulfillment_in_shopify(self):
        """Fulfill the order in Shopify using fulfillmentCreateV2 GraphQL mutation"""
        self.ensure_one()
        if not self.shopify_order_id or not self.shopify_instance_id:
            raise UserError(_('This order is not linked to a Shopify instance'))

        if self.shopify_fulfillment_status == 'fulfilled':
            raise UserError(_('This order is already marked as fulfilled in Shopify'))

        instance = self.shopify_instance_id

        # Step 1: Fetch open fulfillment orders via REST
        try:
            url = f"{instance._get_base_url()}/orders/{self.shopify_order_id}/fulfillment_orders.json"
            response = requests.get(url, headers=instance._get_headers(), timeout=30, verify=certifi.where())
            if response.status_code != 200:
                raise UserError(_('Could not fetch fulfillment orders from Shopify: %s') % response.text)
            fulfillment_orders = response.json().get('fulfillment_orders', [])
        except UserError:
            raise
        except Exception as e:
            raise UserError(_('Error fetching fulfillment orders: %s') % str(e))

        open_orders = [fo for fo in fulfillment_orders if fo.get('status') in ['open', 'in_progress']]
        if not open_orders:
            raise UserError(_('No open fulfillment orders found. The order may already be fulfilled or cancelled in Shopify.'))

        line_items_by_fo = [
            {"fulfillmentOrderId": f"gid://shopify/FulfillmentOrder/{fo['id']}"}
            for fo in open_orders
        ]

        # Step 2: Create fulfillment via GraphQL
        mutation = """
        mutation fulfillmentCreateV2($fulfillment: FulfillmentV2Input!) {
          fulfillmentCreateV2(fulfillment: $fulfillment) {
            fulfillment {
              id
              status
              createdAt
            }
            userErrors {
              field
              message
            }
          }
        }
        """

        variables = {
            "fulfillment": {
                "lineItemsByFulfillmentOrder": line_items_by_fo,
                "notifyCustomer": True,
            }
        }

        try:
            data = instance._execute_graphql(mutation, variables)
            result = data.get('fulfillmentCreateV2', {})

            user_errors = result.get('userErrors', [])
            if user_errors:
                msgs = [f"{e.get('field', '')}: {e.get('message', '')}" for e in user_errors]
                raise UserError(_('Shopify errors: %s') % ', '.join(msgs))

            fulfillment = result.get('fulfillment', {})
            if fulfillment.get('id'):
                fulfillment_id = fulfillment['id'].split('/')[-1]
                self.with_context(shopify_sync_skip=True).write({
                    'shopify_fulfillment_id': fulfillment_id,
                    'shopify_fulfillment_status': 'fulfilled',
                })

            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Order Fulfilled'),
                    'message': _('Fulfillment created in Shopify. Customer notified.'),
                    'type': 'success',
                }
            }
        except UserError:
            raise
        except Exception as e:
            _logger.error(f'Error creating fulfillment in Shopify: {str(e)}')
            raise UserError(_('Error creating fulfillment in Shopify: %s') % str(e))

    def _get_shopify_restock_location_id(self):
        """Return the Shopify location ID to use when restocking refund items.

        Priority:
        1. assigned_location_id from the order's fulfillment orders
        2. First active location from /locations.json
        Returns an integer location ID or None.
        """
        self.ensure_one()
        instance = self.shopify_instance_id
        if not instance:
            return None

        # Try fulfillment orders first — most accurate for this specific order
        try:
            url = f"{instance._get_base_url()}/orders/{self.shopify_order_id}/fulfillment_orders.json"
            response = requests.get(url, headers=instance._get_headers(), timeout=30, verify=certifi.where())
            if response.status_code == 200:
                fulfillment_orders = response.json().get('fulfillment_orders', [])
                for fo in fulfillment_orders:
                    loc_id = fo.get('assigned_location_id')
                    if loc_id:
                        _logger.info(f'[_get_shopify_restock_location_id] Using fulfillment order location {loc_id}')
                        return int(loc_id)
        except Exception as e:
            _logger.warning(f'[_get_shopify_restock_location_id] Error fetching fulfillment orders: {e}')

        # Fallback: first active location
        try:
            url = f"{instance._get_base_url()}/locations.json"
            response = requests.get(
                url,
                headers=instance._get_headers(),
                params={'active': True},
                timeout=30,
                verify=certifi.where(),
            )
            if response.status_code == 200:
                locations = response.json().get('locations', [])
                if locations:
                    loc_id = locations[0]['id']
                    _logger.info(f'[_get_shopify_restock_location_id] Using first active location {loc_id}')
                    return int(loc_id)
        except Exception as e:
            _logger.warning(f'[_get_shopify_restock_location_id] Error fetching locations: {e}')

        return None

    def create_refund_in_shopify(self, amount, note='', notify=True, restock=False):
        """Create a refund in Shopify via REST API.

        Strategy:
        1. Always build refund_line_items from order lines that have a shopify_line_id.
           If none are stored yet, fetch them from Shopify first.
        2. If a matching payment transaction exists, also add a transactions entry so
           Shopify actually processes the money refund.
        Shopify requires at least one of refund_line_items or transactions — this ensures
        we always satisfy that requirement.
        """
        self.ensure_one()
        if not self.shopify_order_id or not self.shopify_instance_id:
            raise UserError(_('This order is not linked to a Shopify instance'))

        instance = self.shopify_instance_id

        # ── Step 1: resolve shopify_line_id for all order lines ───────────────
        if not any(l.shopify_line_id for l in self.order_line):
            _logger.info(
                f'[create_refund_in_shopify] No shopify_line_id stored — fetching from Shopify'
            )
            self._fetch_and_store_shopify_line_ids()

        restock_type = 'return' if restock else 'no_restock'

        # When restocking, Shopify requires a location_id — fetch it from the
        # order's fulfillment orders (assigned_location_id) or fall back to
        # the first active store location.
        location_id = None
        if restock:
            location_id = self._get_shopify_restock_location_id()
            if not location_id:
                _logger.warning(
                    '[create_refund_in_shopify] Could not determine a Shopify location for '
                    'restock — falling back to no_restock'
                )
                restock_type = 'no_restock'

        # ── Build refund_line_items ────────────────────────────────────────────
        # Only needed when the user wants to restock items.
        # For a pure monetary refund (restock=False) Shopify only needs the
        # transactions entry — sending refund_line_items with the full ordered
        # quantity would fail on partially-refunded or already-refunded orders
        # with: "cannot refund more items than were purchased".
        refund_line_items = []
        if restock:
            # Fetch already-refunded quantities so we cap at what remains
            already_refunded = {}  # {line_item_id: qty already refunded}
            try:
                refunds_url = (
                    f"{instance._get_base_url()}"
                    f"/orders/{self.shopify_order_id}/refunds.json"
                )
                r = requests.get(
                    refunds_url, headers=instance._get_headers(),
                    timeout=30, verify=certifi.where(),
                )
                if r.status_code == 200:
                    for prev in r.json().get('refunds', []):
                        for rli in prev.get('refund_line_items', []):
                            lid = rli.get('line_item_id')
                            already_refunded[lid] = (
                                already_refunded.get(lid, 0) + rli.get('quantity', 0)
                            )
                    _logger.info(
                        f'[create_refund_in_shopify] Already-refunded qtys: '
                        f'{already_refunded}'
                    )
                else:
                    _logger.warning(
                        f'[create_refund_in_shopify] Could not fetch existing refunds '
                        f'({r.status_code}) — will use full ordered qty (may fail)'
                    )
            except Exception as e:
                _logger.warning(
                    f'[create_refund_in_shopify] Error fetching existing refunds: {e} '
                    f'— will use full ordered qty'
                )

            for line in self.order_line:
                if not line.shopify_line_id:
                    continue
                line_id = int(line.shopify_line_id)
                ordered_qty = int(line.product_uom_qty)
                already = already_refunded.get(line_id, 0)
                remaining = ordered_qty - already
                if remaining <= 0:
                    _logger.info(
                        f'[create_refund_in_shopify] Line {line_id} fully refunded '
                        f'already (ordered={ordered_qty}, refunded={already}) — skipping'
                    )
                    continue
                item = {
                    'line_item_id': line_id,
                    'quantity': remaining,
                    'restock_type': restock_type,
                }
                if restock_type == 'return' and location_id:
                    item['location_id'] = location_id
                refund_line_items.append(item)
                _logger.info(
                    f'[create_refund_in_shopify] Line {line_id}: '
                    f'refunding qty={remaining} (ordered={ordered_qty}, '
                    f'already_refunded={already})'
                )

        if not refund_line_items and restock:
            _logger.warning(
                f'[create_refund_in_shopify] restock=True but no refundable lines found '
                f'for order {self.name} — all lines may already be fully refunded. '
                f'Proceeding with monetary refund only.'
            )
        elif not restock:
            _logger.info(
                f'[create_refund_in_shopify] restock=False — skipping refund_line_items, '
                f'using transactions entry only for monetary refund'
            )

        refund_payload = {
            "refund": {
                "notify": notify,
                "note": note or 'Refund from Odoo',
            }
        }
        if refund_line_items:
            refund_payload['refund']['refund_line_items'] = refund_line_items

        # ── Step 2: attach payment transaction so Shopify marks order as refunded ─
        # Priority: (a) Odoo local DB, (b) Shopify API live fetch.
        # Without a transactions entry the order stays "paid" in Shopify.
        parent_transaction_id = None
        parent_gateway = 'manual'
        # Currency MUST exactly match the original transaction — Shopify rejects mismatches
        parent_currency = (
            self.shopify_currency
            or (self.currency_id.name if self.currency_id else None)
            or 'USD'
        )

        # (a) Try local Odoo records first
        local_txn = self.env['shopify.payment.transaction'].search([
            ('shopify_order_id', '=', self.shopify_order_id),
            ('shopify_instance_id', '=', instance.id),
            ('kind', 'in', ['sale', 'capture']),
            ('status', 'in', ['success', 'paid']),
        ], limit=1)

        if local_txn and local_txn.shopify_transaction_id:
            parent_transaction_id = int(local_txn.shopify_transaction_id)
            parent_gateway = local_txn.gateway or 'manual'
            if local_txn.currency_id:
                parent_currency = local_txn.currency_id.name
            _logger.info(
                f'[create_refund_in_shopify] Found local transaction {parent_transaction_id} '
                f'(gateway={parent_gateway}, currency={parent_currency})'
            )
        else:
            # (b) Fetch transactions live from Shopify API
            _logger.info(
                f'[create_refund_in_shopify] No local transaction found — fetching from Shopify API'
            )
            try:
                txn_url = f"{instance._get_base_url()}/orders/{self.shopify_order_id}/transactions.json"
                txn_resp = requests.get(
                    txn_url,
                    headers=instance._get_headers(),
                    timeout=30,
                    verify=certifi.where(),
                )
                if txn_resp.status_code == 200:
                    shopify_txns = txn_resp.json().get('transactions', [])
                    for txn in shopify_txns:
                        if (txn.get('kind') in ('sale', 'capture')
                                and txn.get('status') == 'success'):
                            parent_transaction_id = int(txn['id'])
                            parent_gateway = txn.get('gateway', 'manual')
                            # Use currency from the Shopify transaction (most accurate)
                            parent_currency = txn.get('currency') or parent_currency
                            _logger.info(
                                f'[create_refund_in_shopify] Found Shopify transaction '
                                f'{parent_transaction_id} (gateway={parent_gateway}, '
                                f'currency={parent_currency})'
                            )
                            break
                else:
                    _logger.warning(
                        f'[create_refund_in_shopify] Failed to fetch Shopify transactions: '
                        f'{txn_resp.status_code} - {txn_resp.text}'
                    )
            except Exception as e:
                _logger.warning(f'[create_refund_in_shopify] Error fetching Shopify transactions: {e}')

        if parent_transaction_id:
            refund_payload['refund']['transactions'] = [{
                'parent_id': parent_transaction_id,
                'amount': str(round(amount, 2)),
                'kind': 'refund',
                'gateway': parent_gateway,
                'currency': parent_currency,
            }]
        else:
            _logger.warning(
                f'[create_refund_in_shopify] No payment transaction found for order '
                f'{self.shopify_order_id}. Refund will be line-adjustment only (no payment processing).'
            )
            if not refund_line_items:
                raise UserError(_(
                    'Cannot create refund in Shopify: no payment transaction and no order line '
                    'items with Shopify IDs were found for order %s.'
                ) % self.name)

        try:
            url = f"{instance._get_base_url()}/orders/{self.shopify_order_id}/refunds.json"
            response = requests.post(
                url,
                headers=instance._get_headers(),
                json=refund_payload,
                timeout=30,
                verify=certifi.where(),
            )

            if response.status_code not in [200, 201]:
                raise UserError(_('Shopify refund failed: %s - %s') % (response.status_code, response.text))

            refund_data = response.json().get('refund', {})
            if refund_data.get('id'):
                new_status = 'refunded' if amount >= self.amount_total else 'partially_refunded'
                self.with_context(shopify_sync_skip=True).write({
                    'shopify_refund_id': str(refund_data['id']),
                    'shopify_financial_status': new_status,
                })

            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Refund Created'),
                    'message': _('Refund of %s created in Shopify successfully') % amount,
                    'type': 'success',
                }
            }
        except UserError:
            raise
        except Exception as e:
            _logger.error(f'Error creating refund in Shopify: {str(e)}')
            raise UserError(_('Error creating refund in Shopify: %s') % str(e))

    def action_open_refund_wizard(self):
        """Open the Shopify refund wizard"""
        self.ensure_one()
        if not self.shopify_order_id or not self.shopify_instance_id:
            raise UserError(_('This order is not linked to a Shopify instance'))
        return {
            'type': 'ir.actions.act_window',
            'name': _('Create Refund in Shopify'),
            'res_model': 'shopify.refund.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_order_id': self.id,
                'default_refund_amount': self.amount_total,
            },
        }

    def _fetch_and_store_shopify_line_ids(self):
        """Fetch line item IDs from Shopify and store them on the corresponding Odoo order lines.
        Matches by: shopify_product_id → SKU (default_code) → title (name)."""
        self.ensure_one()
        _logger.info(
            f'[_fetch_and_store_shopify_line_ids] Fetching Shopify line IDs for order {self.name} '
            f'(Shopify order_id={self.shopify_order_id})'
        )

        if not self.shopify_order_id or not self.shopify_instance_id:
            _logger.warning(
                f'[_fetch_and_store_shopify_line_ids] Skipping — shopify_order_id={self.shopify_order_id!r}, '
                f'shopify_instance_id={self.shopify_instance_id!r}'
            )
            return

        instance = self.shopify_instance_id
        url = f"{instance._get_base_url()}/orders/{self.shopify_order_id}.json"
        _logger.info(f'[_fetch_and_store_shopify_line_ids] GET {url}')
        try:
            response = requests.get(
                url,
                headers=instance._get_headers(),
                params={'fields': 'id,line_items'},
                timeout=30,
                verify=certifi.where(),
            )
            _logger.info(
                f'[_fetch_and_store_shopify_line_ids] Response status={response.status_code}'
            )
            if response.status_code != 200:
                _logger.warning(
                    f'[_fetch_and_store_shopify_line_ids] Could not fetch order {self.shopify_order_id}: '
                    f'{response.text}'
                )
                return
            shopify_lines = response.json().get('order', {}).get('line_items', [])
            _logger.info(
                f'[_fetch_and_store_shopify_line_ids] Got {len(shopify_lines)} Shopify line items: '
                f'{[(l.get("id"), l.get("title"), l.get("sku"), l.get("product_id")) for l in shopify_lines]}'
            )
        except Exception as e:
            _logger.warning(f'[_fetch_and_store_shopify_line_ids] Exception fetching order lines: {str(e)}')
            return

        # Build lookup maps from Shopify lines
        shopify_by_product_id = {str(l.get('product_id', '')): l for l in shopify_lines if l.get('product_id')}
        shopify_by_sku = {l.get('sku', ''): l for l in shopify_lines if l.get('sku')}
        shopify_by_title = {l.get('title', '').lower(): l for l in shopify_lines if l.get('title')}

        _logger.info(
            f'[_fetch_and_store_shopify_line_ids] Lookup maps — by_product_id keys={list(shopify_by_product_id.keys())}, '
            f'by_sku keys={list(shopify_by_sku.keys())}, by_title keys={list(shopify_by_title.keys())}'
        )

        for odoo_line in self.order_line:
            if odoo_line.shopify_line_id:
                _logger.info(
                    f'[_fetch_and_store_shopify_line_ids] Line "{odoo_line.name}" already has '
                    f'shopify_line_id={odoo_line.shopify_line_id} — skipping'
                )
                continue

            matched_line = None
            product_tmpl = odoo_line.product_id.product_tmpl_id

            _logger.info(
                f'[_fetch_and_store_shopify_line_ids] Trying to match Odoo line "{odoo_line.name}" — '
                f'product_id={odoo_line.product_id.id}, '
                f'shopify_product_id={product_tmpl.shopify_product_id!r}, '
                f'sku={product_tmpl.default_code!r}'
            )

            # Match 1: by Shopify product ID
            if product_tmpl.shopify_product_id:
                matched_line = shopify_by_product_id.get(product_tmpl.shopify_product_id)
                if matched_line:
                    _logger.info(f'[_fetch_and_store_shopify_line_ids] Matched by shopify_product_id={product_tmpl.shopify_product_id}')

            # Match 2: by SKU (internal reference)
            if not matched_line and product_tmpl.default_code:
                matched_line = shopify_by_sku.get(product_tmpl.default_code)
                if matched_line:
                    _logger.info(f'[_fetch_and_store_shopify_line_ids] Matched by SKU={product_tmpl.default_code}')

            # Match 3: by title
            if not matched_line:
                line_name = (odoo_line.name or odoo_line.product_id.name or '').lower()
                _logger.info(f'[_fetch_and_store_shopify_line_ids] Trying title match with line_name={line_name!r}')
                for title, sl in shopify_by_title.items():
                    if title and (title in line_name or line_name in title):
                        matched_line = sl
                        _logger.info(f'[_fetch_and_store_shopify_line_ids] Matched by title={title!r}')
                        break

            if matched_line:
                odoo_line.with_context(shopify_sync_skip=True).write({
                    'shopify_line_id': str(matched_line['id'])
                })
                _logger.info(
                    f'[_fetch_and_store_shopify_line_ids] Stored shopify_line_id={matched_line["id"]} '
                    f'on Odoo line "{odoo_line.name}"'
                )
            else:
                _logger.warning(
                    f'[_fetch_and_store_shopify_line_ids] Could NOT match Odoo line "{odoo_line.name}" '
                    f'to any Shopify line item'
                )

    def _lookup_shopify_variant_id(self, product, instance):
        """Look up the Shopify variant GID for an Odoo product.

        Priority:
          1. shopify_variant_id already stored on product.product
          2. Fetch variants from Shopify using product_tmpl_id.shopify_product_id,
             match by SKU, fall back to first variant; store the result for future use.

        Returns a full GID string like 'gid://shopify/ProductVariant/12345'
        or None if no match can be found.
        """
        # 1. Already stored
        if product.shopify_variant_id:
            return f"gid://shopify/ProductVariant/{product.shopify_variant_id}"

        # 2. Try to resolve from the parent template's Shopify product
        shopify_product_id = product.product_tmpl_id.shopify_product_id
        if not shopify_product_id:
            _logger.warning(
                f'[_lookup_shopify_variant_id] Product "{product.name}" has no '
                f'shopify_variant_id and no shopify_product_id on template — cannot resolve'
            )
            return None

        url = f"{instance._get_base_url()}/products/{shopify_product_id}.json"
        try:
            response = requests.get(
                url,
                headers=instance._get_headers(),
                params={'fields': 'id,variants'},
                timeout=30,
                verify=certifi.where(),
            )
            if response.status_code != 200:
                _logger.warning(
                    f'[_lookup_shopify_variant_id] GET {url} returned {response.status_code}'
                )
                return None

            variants = response.json().get('product', {}).get('variants', [])
            if not variants:
                return None

            # Match by SKU first
            sku = product.default_code or product.product_tmpl_id.default_code
            matched = None
            if sku:
                matched = next((v for v in variants if v.get('sku') == sku), None)
                if matched:
                    _logger.info(
                        f'[_lookup_shopify_variant_id] Matched variant for "{product.name}" by SKU={sku}'
                    )

            # Fallback: use first (default) variant for simple products
            if not matched:
                matched = variants[0]
                _logger.info(
                    f'[_lookup_shopify_variant_id] Using first variant for "{product.name}" '
                    f'(id={matched["id"]})'
                )

            variant_id = str(matched['id'])
            # Store it so the next call is immediate
            product.with_context(shopify_sync_skip=True).write({'shopify_variant_id': variant_id})
            return f"gid://shopify/ProductVariant/{variant_id}"

        except Exception as e:
            _logger.warning(f'[_lookup_shopify_variant_id] Exception: {e}')
            return None

    def sync_order_lines_to_shopify(self):
        """Sync order line qty and price changes to Shopify using the Order Editing API.

        Flow:
          1. orderEditBegin  → get calculatedOrder ID + current Shopify line data
          2. orderEditSetQuantity  → for each line where qty changed
          3. orderEditAddLineItemDiscount  → for price decreases
          4. orderEditAddCustomItem → for price increases (adds an adjustment line)
          5. orderEditCommit
        """
        self.ensure_one()
        _logger.info(
            f'[sync_order_lines_to_shopify] START for order {self.name} '
            f'(shopify_order_id={self.shopify_order_id}, '
            f'instance={self.shopify_instance_id.name if self.shopify_instance_id else None})'
        )

        if not self.shopify_order_id or not self.shopify_instance_id:
            raise UserError(_('This order is not linked to a Shopify instance'))

        # Log all current Odoo order lines
        for line in self.order_line:
            _logger.info(
                f'[sync_order_lines_to_shopify] Odoo line: "{line.name}" '
                f'qty={line.product_uom_qty} price={line.price_unit} '
                f'shopify_line_id={line.shopify_line_id!r}'
            )

        # Auto-fetch and store Shopify line IDs if they are not already stored
        if not self.order_line.filtered(lambda l: l.shopify_line_id):
            _logger.info('[sync_order_lines_to_shopify] No shopify_line_id found — calling _fetch_and_store_shopify_line_ids')
            self._fetch_and_store_shopify_line_ids()
        else:
            _logger.info('[sync_order_lines_to_shopify] Some lines already have shopify_line_id — skipping fetch')

        lines_with_id = self.order_line.filtered(lambda l: l.shopify_line_id)
        _logger.info(f'[sync_order_lines_to_shopify] Lines with shopify_line_id: {len(lines_with_id)} / {len(self.order_line)}')

        # New lines: added in Odoo but not yet synced to Shopify
        new_lines = self.order_line.filtered(lambda l: not l.shopify_line_id)
        _logger.info(f'[sync_order_lines_to_shopify] New lines to add: {len(new_lines)}')

        if not lines_with_id and not new_lines:
            _logger.error(
                f'[sync_order_lines_to_shopify] Could not link any order lines to Shopify for order {self.name}'
            )
            raise UserError(_(
                'Could not link order lines to Shopify line item IDs and no new products '
                'with Shopify variants found. Please check that this order exists in Shopify (Order ID: %s).'
            ) % self.shopify_order_id)

        instance = self.shopify_instance_id
        order_gid = f"gid://shopify/Order/{self.shopify_order_id}"
        currency = self.shopify_currency or (instance.currency_id.name if instance.currency_id else 'USD')

        # ── Step 1: Begin order edit ──────────────────────────────────────────
        _logger.info(f'[sync_order_lines_to_shopify] Step 1: orderEditBegin for GID={order_gid}')

        begin_mutation = """
        mutation orderEditBegin($id: ID!) {
          orderEditBegin(id: $id) {
            calculatedOrder {
              id
              lineItems(first: 100) {
                nodes {
                  id
                  quantity
                  title
                  originalUnitPriceSet { shopMoney { amount } }
                  discountedUnitPriceSet { shopMoney { amount } }
                }
              }
            }
            userErrors { field message }
          }
        }
        """
        try:
            begin_data = instance._execute_graphql(begin_mutation, {"id": order_gid})
            _logger.info(f'[sync_order_lines_to_shopify] orderEditBegin raw response: {begin_data}')
        except Exception as e:
            _logger.error(f'[sync_order_lines_to_shopify] orderEditBegin exception: {str(e)}')
            raise UserError(_('Error beginning order edit in Shopify: %s') % str(e))

        begin_result = begin_data.get('orderEditBegin', {})
        begin_errors = begin_result.get('userErrors', [])
        if begin_errors:
            msgs = [f"{e.get('field', '')}: {e.get('message', '')}" for e in begin_errors]
            _logger.error(f'[sync_order_lines_to_shopify] orderEditBegin userErrors: {msgs}')
            raise UserError(_('Cannot begin order edit: %s') % ', '.join(msgs))

        calc_order = begin_result.get('calculatedOrder', {})
        calc_order_id = calc_order.get('id')
        shopify_line_nodes = calc_order.get('lineItems', {}).get('nodes', [])
        shopify_line_map = {node['id']: node for node in shopify_line_nodes}

        _logger.info(
            f'[sync_order_lines_to_shopify] calculatedOrder id={calc_order_id}, '
            f'Shopify lines in edit session: '
            f'{[(n["id"], n.get("title"), n.get("quantity")) for n in shopify_line_nodes]}'
        )

        # ── Steps 1b, 2 & 3: Track whether any changes were staged ───────────────
        changes_made = False

        # ── Step 1a: Remove Shopify lines that no longer exist in Odoo ───────────
        # Build set of active numeric shopify_line_ids from current Odoo order lines
        odoo_shopify_line_id_set = {
            l.shopify_line_id for l in self.order_line if l.shopify_line_id
        }
        _logger.info(
            f'[sync_order_lines_to_shopify] Odoo active shopify_line_ids: {odoo_shopify_line_id_set}'
        )

        remove_qty_mutation = """
        mutation orderEditSetQuantity($id: ID!, $lineItemId: ID!, $quantity: Int!, $restock: Boolean) {
          orderEditSetQuantity(id: $id, lineItemId: $lineItemId, quantity: $quantity, restock: $restock) {
            calculatedOrder { id }
            userErrors { field message }
          }
        }
        """
        for shopify_node in shopify_line_nodes:
            node_gid = shopify_node['id']
            # GID format: "gid://shopify/CalculatedLineItem/12345"
            node_numeric_id = node_gid.split('/')[-1]
            if node_numeric_id not in odoo_shopify_line_id_set:
                _logger.info(
                    f'[sync_order_lines_to_shopify] Shopify line {node_gid} '
                    f'(title={shopify_node.get("title")!r}) is no longer in Odoo — removing (qty→0)'
                )
                try:
                    rm_data = instance._execute_graphql(remove_qty_mutation, {
                        "id": calc_order_id,
                        "lineItemId": node_gid,
                        "quantity": 0,
                        "restock": False,
                    })
                    rm_errs = rm_data.get('orderEditSetQuantity', {}).get('userErrors', [])
                    if rm_errs:
                        _logger.warning(
                            f'[sync_order_lines_to_shopify] Remove line errors for {node_gid}: {rm_errs}'
                        )
                    else:
                        changes_made = True
                        _logger.info(
                            f'[sync_order_lines_to_shopify] Removed Shopify line {node_gid} '
                            f'(title={shopify_node.get("title")!r})'
                        )
                except Exception as e:
                    _logger.error(
                        f'[sync_order_lines_to_shopify] Error removing Shopify line {node_gid}: {str(e)}'
                    )

        # ── Step 1b: Add new lines via orderEditAddVariant (or custom item fallback) ──
        add_variant_mutation = """
        mutation orderEditAddVariant($id: ID!, $variantId: ID!, $quantity: Int!, $allowDuplicates: Boolean) {
          orderEditAddVariant(id: $id, variantId: $variantId, quantity: $quantity, allowDuplicates: $allowDuplicates) {
            calculatedLineItem { id }
            calculatedOrder { id }
            userErrors { field message }
          }
        }
        """
        add_custom_mutation = """
        mutation orderEditAddCustomItem($id: ID!, $title: String!, $quantity: Int!, $price: MoneyInput!, $taxable: Boolean) {
          orderEditAddCustomItem(id: $id, title: $title, quantity: $quantity, price: $price, taxable: $taxable) {
            calculatedLineItem { id }
            calculatedOrder { id }
            userErrors { field message }
          }
        }
        """
        for new_line in new_lines:
            qty = max(int(new_line.product_uom_qty), 1)

            # Resolve Shopify variant GID — may trigger a Shopify API lookup
            variant_gid = self._lookup_shopify_variant_id(new_line.product_id, instance)

            if variant_gid:
                # ── Path A: use orderEditAddVariant (preserves Shopify product link) ──
                _logger.info(
                    f'[sync_order_lines_to_shopify] Adding new line "{new_line.name}" '
                    f'via orderEditAddVariant variant={variant_gid} qty={qty}'
                )
                try:
                    add_data = instance._execute_graphql(add_variant_mutation, {
                        "id": calc_order_id,
                        "variantId": variant_gid,
                        "quantity": qty,
                        "allowDuplicates": True,
                    })
                    add_result = add_data.get('orderEditAddVariant', {})
                    add_errs = add_result.get('userErrors', [])
                    if add_errs:
                        _logger.warning(
                            f'[sync_order_lines_to_shopify] orderEditAddVariant errors for '
                            f'"{new_line.name}": {add_errs} — falling back to custom item'
                        )
                        # Fall through to custom item below
                        variant_gid = None
                    else:
                        new_line_gid = add_result.get('calculatedLineItem', {}).get('id', '')
                        if new_line_gid:
                            new_shopify_line_id = new_line_gid.split('/')[-1]
                            new_line.with_context(shopify_sync_skip=True).write(
                                {'shopify_line_id': new_shopify_line_id}
                            )
                            _logger.info(
                                f'[sync_order_lines_to_shopify] Added "{new_line.name}" '
                                f'via variant → shopify_line_id={new_shopify_line_id}'
                            )
                        changes_made = True
                except Exception as e:
                    _logger.error(
                        f'[sync_order_lines_to_shopify] Error in orderEditAddVariant '
                        f'for "{new_line.name}": {str(e)} — falling back to custom item'
                    )
                    variant_gid = None  # trigger fallback

            if not variant_gid:
                # ── Path B: fallback to orderEditAddCustomItem ──
                _logger.info(
                    f'[sync_order_lines_to_shopify] Adding new line "{new_line.name}" '
                    f'via orderEditAddCustomItem qty={qty} price={new_line.price_unit}'
                )
                try:
                    custom_data = instance._execute_graphql(add_custom_mutation, {
                        "id": calc_order_id,
                        "title": new_line.product_id.name or new_line.name,
                        "quantity": qty,
                        "price": {
                            "amount": str(round(new_line.price_unit, 2)),
                            "currencyCode": currency,
                        },
                        "taxable": False,
                    })
                    custom_result = custom_data.get('orderEditAddCustomItem', {})
                    custom_errs = custom_result.get('userErrors', [])
                    if custom_errs:
                        _logger.warning(
                            f'[sync_order_lines_to_shopify] orderEditAddCustomItem errors for '
                            f'"{new_line.name}": {custom_errs}'
                        )
                    else:
                        new_line_gid = custom_result.get('calculatedLineItem', {}).get('id', '')
                        if new_line_gid:
                            new_shopify_line_id = new_line_gid.split('/')[-1]
                            new_line.with_context(shopify_sync_skip=True).write(
                                {'shopify_line_id': new_shopify_line_id}
                            )
                            _logger.info(
                                f'[sync_order_lines_to_shopify] Added "{new_line.name}" '
                                f'via custom item → shopify_line_id={new_shopify_line_id}'
                            )
                        changes_made = True
                except Exception as e:
                    _logger.error(
                        f'[sync_order_lines_to_shopify] Error in orderEditAddCustomItem '
                        f'for "{new_line.name}": {str(e)}'
                    )

        # ── Steps 2 & 3: Quantity and price changes ───────────────────────────

        for odoo_line in lines_with_id:
            line_gid = f"gid://shopify/CalculatedLineItem/{odoo_line.shopify_line_id}"
            _logger.info(
                f'[sync_order_lines_to_shopify] Checking Odoo line "{odoo_line.name}" '
                f'→ looking for Shopify GID={line_gid}'
            )
            shopify_line = shopify_line_map.get(line_gid)
            if not shopify_line:
                _logger.warning(
                    f'[sync_order_lines_to_shopify] Shopify line GID={line_gid} NOT found in '
                    f'calculatedOrder. Available GIDs: {list(shopify_line_map.keys())}'
                )
                continue

            # Quantity sync
            current_qty = int(shopify_line.get('quantity', 0))
            new_qty = max(int(odoo_line.product_uom_qty), 0)
            _logger.info(
                f'[sync_order_lines_to_shopify] "{odoo_line.name}": '
                f'Shopify qty={current_qty}, Odoo qty={new_qty}'
            )
            if current_qty != new_qty:
                qty_mutation = """
                mutation orderEditSetQuantity($id: ID!, $lineItemId: ID!, $quantity: Int!, $restock: Boolean) {
                  orderEditSetQuantity(id: $id, lineItemId: $lineItemId, quantity: $quantity, restock: $restock) {
                    calculatedOrder { id }
                    userErrors { field message }
                  }
                }
                """
                try:
                    qty_data = instance._execute_graphql(qty_mutation, {
                        "id": calc_order_id,
                        "lineItemId": line_gid,
                        "quantity": new_qty,
                        "restock": False,
                    })
                    qty_errs = qty_data.get('orderEditSetQuantity', {}).get('userErrors', [])
                    if qty_errs:
                        _logger.warning(f'Qty update errors for "{odoo_line.name}": {qty_errs}')
                    else:
                        changes_made = True
                        _logger.info(f'Qty updated for "{odoo_line.name}": {current_qty} → {new_qty}')
                except Exception as e:
                    _logger.error(f'Error updating qty for "{odoo_line.name}": {str(e)}')

            # Price sync — newer Shopify API exposes per-unit prices directly via discountedUnitPriceSet
            current_price = round(float(
                shopify_line.get('discountedUnitPriceSet', {}).get('shopMoney', {}).get('amount', 0) or 0
            ), 4)
            new_price = round(odoo_line.price_unit, 4)
            diff = round(current_price - new_price, 2)
            _logger.info(
                f'[sync_order_lines_to_shopify] "{odoo_line.name}": '
                f'Shopify current_price={current_price}, Odoo price={new_price}, diff={diff}'
            )

            if abs(diff) >= 0.01:
                if diff > 0:
                    # Price decreased → apply per-unit discount
                    discount_mutation = """
                    mutation orderEditAddLineItemDiscount($id: ID!, $lineItemId: ID!, $discount: OrderEditAppliedDiscountInput!) {
                      orderEditAddLineItemDiscount(id: $id, lineItemId: $lineItemId, discount: $discount) {
                        addedDiscountStagedChange { id }
                        calculatedOrder { id }
                        userErrors { field message }
                      }
                    }
                    """
                    try:
                        disc_data = instance._execute_graphql(discount_mutation, {
                            "id": calc_order_id,
                            "lineItemId": line_gid,
                            "discount": {
                                "description": "Price adjustment from Odoo",
                                "fixedValue": {
                                    "amount": str(round(diff * new_qty, 2)),
                                    "currencyCode": currency,
                                },
                            },
                        })
                        disc_errs = disc_data.get('orderEditAddLineItemDiscount', {}).get('userErrors', [])
                        if disc_errs:
                            _logger.warning(f'Price discount errors for "{odoo_line.name}": {disc_errs}')
                        else:
                            changes_made = True
                            _logger.info(f'Price discount applied for "{odoo_line.name}": -{diff}/unit')
                    except Exception as e:
                        _logger.error(f'Error applying price discount for "{odoo_line.name}": {str(e)}')
                else:
                    # Price increased → add a custom adjustment line item
                    price_increase = round(abs(diff), 2)
                    adj_qty = max(int(odoo_line.product_uom_qty), 1)
                    add_mutation = """
                    mutation orderEditAddCustomItem($id: ID!, $title: String!, $quantity: Int!, $price: MoneyInput!, $taxable: Boolean) {
                      orderEditAddCustomItem(id: $id, title: $title, quantity: $quantity, price: $price, taxable: $taxable) {
                        calculatedLineItem { id }
                        calculatedOrder { id }
                        userErrors { field message }
                      }
                    }
                    """
                    try:
                        add_data = instance._execute_graphql(add_mutation, {
                            "id": calc_order_id,
                            "title": f"Price adjustment – {odoo_line.name}",
                            "quantity": adj_qty,
                            "price": {"amount": str(price_increase), "currencyCode": currency},
                            "taxable": False,
                        })
                        add_errs = add_data.get('orderEditAddCustomItem', {}).get('userErrors', [])
                        if add_errs:
                            _logger.warning(f'Price increase errors for "{odoo_line.name}": {add_errs}')
                        else:
                            changes_made = True
                            _logger.info(f'Price increase adjustment added for "{odoo_line.name}": +{price_increase}/unit')
                    except Exception as e:
                        _logger.error(f'Error adding price adjustment for "{odoo_line.name}": {str(e)}')

        # ── Step 4: Commit ────────────────────────────────────────────────────
        _logger.info(f'[sync_order_lines_to_shopify] changes_made={changes_made}')
        if not changes_made:
            _logger.info('[sync_order_lines_to_shopify] No changes detected — skipping commit')
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('No Line Changes'),
                    'message': _('No quantity or price changes detected in order lines'),
                    'type': 'warning',
                }
            }

        commit_mutation = """
        mutation orderEditCommit($id: ID!, $notifyCustomer: Boolean, $staffNote: String) {
          orderEditCommit(id: $id, notifyCustomer: $notifyCustomer, staffNote: $staffNote) {
            order { id updatedAt }
            userErrors { field message }
          }
        }
        """
        _logger.info(f'[sync_order_lines_to_shopify] Step 5: orderEditCommit for calc_order_id={calc_order_id}')
        try:
            commit_data = instance._execute_graphql(commit_mutation, {
                "id": calc_order_id,
                "notifyCustomer": False,
                "staffNote": "Order lines updated from Odoo",
            })
            _logger.info(f'[sync_order_lines_to_shopify] orderEditCommit response: {commit_data}')
            commit_result = commit_data.get('orderEditCommit', {})
            commit_errors = commit_result.get('userErrors', [])
            if commit_errors:
                msgs = [f"{e.get('field', '')}: {e.get('message', '')}" for e in commit_errors]
                _logger.error(f'[sync_order_lines_to_shopify] orderEditCommit userErrors: {msgs}')
                raise UserError(_('Error committing order edit: %s') % ', '.join(msgs))

            order_data = commit_result.get('order', {})
            if order_data.get('updatedAt'):
                updated_at = date_parser.parse(order_data['updatedAt']).replace(tzinfo=None)
                self.with_context(shopify_sync_skip=True).write({'shopify_updated_at': updated_at})

        except UserError:
            raise
        except Exception as e:
            raise UserError(_('Error committing order edit to Shopify: %s') % str(e))

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Lines Synced'),
                'message': _('Order line qty/price changes committed to Shopify successfully'),
                'type': 'success',
            }
        }

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


class SaleOrderLine(models.Model):
    _inherit = 'sale.order.line'

    shopify_line_id = fields.Char('Shopify Line Item ID', readonly=True, copy=False)

    def create(self, vals_list):
        records = super().create(vals_list)
        if self.env.context.get('shopify_sync_skip'):
            return records
        orders = records.mapped('order_id').filtered(
            lambda o: o.is_shopify_order and o.shopify_order_id and o.shopify_instance_id
        )
        for order in orders:
            _logger.info(f'[SaleOrderLine.create] New line on Shopify order {order.name} — triggering sync')
            try:
                order.with_context(shopify_sync_skip=True).sync_order_lines_to_shopify()
                _logger.info(f'[SaleOrderLine.create] Sync completed for order {order.name}')
            except Exception as e:
                _logger.warning(
                    f'[SaleOrderLine.create] Auto-sync to Shopify FAILED for {order.name}: {str(e)}'
                )
        return records

    def write(self, vals):
        _logger.info(
            f'[SaleOrderLine.write] Called with vals keys={list(vals.keys())}, '
            f'shopify_sync_skip={self.env.context.get("shopify_sync_skip")}'
        )
        result = super().write(vals)
        if self.env.context.get('shopify_sync_skip'):
            _logger.info('[SaleOrderLine.write] shopify_sync_skip=True — skipping auto-sync')
            return result
        # Auto-sync quantity/price changes back to Shopify
        sync_fields = {'product_uom_qty', 'price_unit'}
        triggered = [f for f in sync_fields if f in vals]
        if triggered:
            _logger.info(f'[SaleOrderLine.write] Sync-triggering fields changed: {triggered}')
            # Include ALL Shopify orders — sync_order_lines_to_shopify will auto-fetch
            # line IDs from Shopify if they are not stored yet
            orders = self.mapped('order_id').filtered(
                lambda o: o.is_shopify_order and o.shopify_order_id and o.shopify_instance_id
            )
            _logger.info(
                f'[SaleOrderLine.write] Found {len(orders)} Shopify order(s) to sync: '
                f'{[(o.name, o.shopify_order_id) for o in orders]}'
            )
            for order in orders:
                _logger.info(f'[SaleOrderLine.write] Triggering sync for order {order.name}')
                try:
                    order.with_context(shopify_sync_skip=True).sync_order_lines_to_shopify()
                    _logger.info(f'[SaleOrderLine.write] Sync completed for order {order.name}')
                except Exception as e:
                    _logger.warning(
                        f'[SaleOrderLine.write] Auto-sync order lines to Shopify FAILED for '
                        f'{order.name}: {str(e)}'
                    )
        else:
            _logger.info(f'[SaleOrderLine.write] No sync-triggering fields in vals — skipping')
        return result

    def unlink(self):
        if self.env.context.get('shopify_sync_skip'):
            return super().unlink()
        # Capture affected Shopify orders BEFORE deletion so we can sync after
        shopify_orders = self.mapped('order_id').filtered(
            lambda o: o.is_shopify_order and o.shopify_order_id and o.shopify_instance_id
        )
        result = super().unlink()
        for order in shopify_orders:
            _logger.info(
                f'[SaleOrderLine.unlink] Line deleted on Shopify order {order.name} — triggering line sync'
            )
            try:
                order.with_context(shopify_sync_skip=True).sync_order_lines_to_shopify()
            except Exception as e:
                _logger.warning(
                    f'[SaleOrderLine.unlink] Auto-sync to Shopify FAILED for {order.name}: {str(e)}'
                )
        return result


class StockPicking(models.Model):
    _inherit = 'stock.picking'

    def _action_done(self):
        """Auto-fulfill the linked Shopify order when a delivery is validated in Odoo."""
        result = super()._action_done()
        if self.env.context.get('shopify_sync_skip'):
            return result
        for picking in self:
            # Only trigger on outgoing deliveries to customers
            if picking.picking_type_code != 'outgoing' or picking.state != 'done':
                continue
            sale_order = getattr(picking, 'sale_id', False)
            if not sale_order or not sale_order.is_shopify_order:
                continue
            if not sale_order.shopify_order_id or not sale_order.shopify_instance_id:
                continue
            if sale_order.shopify_fulfillment_status == 'fulfilled':
                _logger.info(
                    f'[StockPicking._action_done] Order {sale_order.name} already fulfilled '
                    f'in Shopify — skipping'
                )
                continue
            _logger.info(
                f'[StockPicking._action_done] Auto-fulfilling Shopify order for '
                f'{sale_order.name} (Shopify #{sale_order.shopify_order_id})'
            )
            try:
                sale_order.with_context(shopify_sync_skip=True).create_fulfillment_in_shopify()
            except Exception as e:
                _logger.warning(
                    f'[StockPicking._action_done] Shopify fulfillment failed for '
                    f'{sale_order.name}: {str(e)}'
                )
        return result


class AccountMove(models.Model):
    _inherit = 'account.move'

    def action_post(self):
        """Auto-refund the linked Shopify order when a credit note is posted in Odoo."""
        result = super().action_post()
        if self.env.context.get('shopify_sync_skip'):
            return result
        for move in self:
            if move.move_type != 'out_refund':
                continue
            sale_order = self._get_shopify_sale_order_from_refund(move)
            if not sale_order:
                continue
            refund_amount = move.amount_total
            _logger.info(
                f'[AccountMove.action_post] Auto-refunding Shopify order for '
                f'{sale_order.name} (Shopify #{sale_order.shopify_order_id}), '
                f'amount={refund_amount}'
            )
            try:
                sale_order.with_context(shopify_sync_skip=True).create_refund_in_shopify(
                    amount=refund_amount,
                    note=f'Refund from Odoo credit note {move.name or ""}',
                )
            except Exception as e:
                _logger.warning(
                    f'[AccountMove.action_post] Shopify refund failed for '
                    f'{sale_order.name}: {str(e)}'
                )
        return result

    def _get_shopify_sale_order_from_refund(self, move):
        """Resolve the Shopify-linked sale order from a credit note.

        Priority:
          1. reversed_entry_id → original invoice → sale order lines
          2. invoice_origin field → sale order name lookup
        """
        # 1. Via the reversed invoice's sale lines
        if move.reversed_entry_id:
            for line in move.reversed_entry_id.line_ids:
                sale_lines = getattr(line, 'sale_line_ids', None)
                if sale_lines:
                    for sale_line in sale_lines:
                        order = sale_line.order_id
                        if order.is_shopify_order and order.shopify_order_id and order.shopify_instance_id:
                            _logger.info(
                                f'[_get_shopify_sale_order_from_refund] Found order '
                                f'{order.name} via reversed_entry_id'
                            )
                            return order

        # 2. Via invoice_origin (sale order name stored on the credit note)
        if move.invoice_origin:
            order = self.env['sale.order'].search([
                ('name', '=', move.invoice_origin),
                ('is_shopify_order', '=', True),
            ], limit=1)
            if order and order.shopify_order_id and order.shopify_instance_id:
                _logger.info(
                    f'[_get_shopify_sale_order_from_refund] Found order '
                    f'{order.name} via invoice_origin'
                )
                return order

        _logger.info(
            f'[_get_shopify_sale_order_from_refund] No Shopify sale order found '
            f'for credit note {move.name or move.id}'
        )
        return None
