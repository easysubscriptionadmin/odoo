# -*- coding: utf-8 -*-

from odoo import api, fields, models, _
from odoo.exceptions import UserError
import logging
import threading
import requests
import certifi

_logger = logging.getLogger(__name__)


class ShopifyOperation(models.TransientModel):
    _name = 'shopify.operation'
    _description = 'Shopify Operation Wizard'

    shopify_instance_id = fields.Many2one('shopify.instance', string='Shopify Instance', required=True)
    shopify_operation = fields.Selection([
        ('import_products', 'Import Products from Shopify'),
        ('import_customers', 'Import Customers from Shopify'),
        ('import_orders', 'Import Orders from Shopify'),
        ('import_collections', 'Import Collections from Shopify'),
        ('import_gift_cards', 'Import Gift Cards from Shopify'),
        ('import_locations', 'Import Locations from Shopify'),
        ('import_discounts', 'Import Discounts from Shopify'),
        ('export_products', 'Export Products to Shopify'),
        ('export_customers', 'Export Customers to Shopify'),
    ], string='Operation', required=True, default='import_products')

    # For order import date range filter
    import_orders_from_date = fields.Datetime(
        'Orders From',
        help='Import orders created on or after this date. Leave empty for no lower limit.',
    )
    import_orders_to_date = fields.Datetime(
        'Orders To',
        help='Import orders created on or before this date. Leave empty for no upper limit.',
    )

    # For export operations
    product_ids = fields.Many2many('product.template', string='Products to Export',
                                     help='Select specific products to export. Leave empty to export all Shopify products.')
    customer_ids = fields.Many2many('res.partner', string='Customers to Export',
                                      help='Select specific customers to export. Leave empty to export all Shopify customers.')

    def perform_operation(self):
        """Perform the selected Shopify operation"""
        self.ensure_one()

        if not self.shopify_instance_id:
            raise UserError(_('Please select a Shopify instance'))

        # Test connection first
        try:
            url = f"{self.shopify_instance_id._get_base_url()}/shop.json"
            response = requests.get(url, headers=self.shopify_instance_id._get_headers(), timeout=10, verify=certifi.where())
            if response.status_code != 200:
                raise UserError(_('Cannot connect to Shopify. Please check your credentials.'))
        except Exception as e:
            raise UserError(_('Connection test failed: %s') % str(e))

        # Perform the operation
        if self.shopify_operation == 'import_products':
            return self._import_products()
        elif self.shopify_operation == 'import_customers':
            return self._import_customers()
        elif self.shopify_operation == 'import_orders':
            return self._import_orders()
        elif self.shopify_operation == 'import_collections':
            return self._import_collections()
        elif self.shopify_operation == 'import_gift_cards':
            return self._import_gift_cards()
        elif self.shopify_operation == 'import_locations':
            return self._import_locations()
        elif self.shopify_operation == 'import_discounts':
            return self._import_discounts()
        elif self.shopify_operation == 'export_products':
            return self._export_products()
        elif self.shopify_operation == 'export_customers':
            return self._export_customers()

    def _import_products(self):
        """Import products from Shopify"""
        _logger.info(f'Starting product import from Shopify instance: {self.shopify_instance_id.name}')

        try:
            result = self.env['product.template'].import_shopify_products(self.shopify_instance_id.id)
            return result
        except Exception as e:
            _logger.error(f'Error in product import: {str(e)}')
            raise UserError(_('Product import failed: %s') % str(e))

    def _import_customers(self):
        """Import customers from Shopify"""
        _logger.info(f'Starting customer import from Shopify instance: {self.shopify_instance_id.name}')

        try:
            result = self.env['res.partner'].import_shopify_customers(self.shopify_instance_id.id)
            return result
        except Exception as e:
            _logger.error(f'Error in customer import: {str(e)}')
            raise UserError(_('Customer import failed: %s') % str(e))

    def _import_orders(self):
        """Import orders from Shopify in a background thread.

        Returns immediately with an 'Import started' toast.
        Progress toasts (every 20 orders) and a final completion toast are
        sent via the Odoo bus so the user sees live updates without the UI
        being blocked.
        """
        instance_id = self.shopify_instance_id.id
        instance_name = self.shopify_instance_id.name
        date_from = self.import_orders_from_date
        date_to = self.import_orders_to_date
        uid = self.env.uid
        registry = self.env.registry

        _logger.info(
            f'Scheduling background order import for instance {instance_name} '
            f'(date_from={date_from}, date_to={date_to})'
        )

        def run_import():
            import odoo
            with registry.cursor() as cr:
                env = odoo.api.Environment(cr, uid, {})
                try:
                    env['sale.order'].import_shopify_orders(
                        instance_id,
                        date_from=date_from,
                        date_to=date_to,
                        batch_size=20,
                        notify_uid=uid,
                    )
                    cr.commit()
                except Exception as exc:
                    _logger.error(f'Background order import failed: {exc}')
                    # Send failure notification via bus
                    try:
                        partner = env['res.users'].browse(uid).partner_id
                        env['bus.bus']._sendone(partner, 'simple_notification', {
                            'title': _('Order Import Failed'),
                            'message': str(exc),
                            'type': 'danger',
                        })
                        cr.commit()
                    except Exception:
                        pass

        thread = threading.Thread(target=run_import, daemon=True)
        thread.start()

        # Build a helpful summary of what date range is being imported
        if date_from and date_to:
            date_info = _('from %s to %s') % (
                date_from.strftime('%d %b %Y'), date_to.strftime('%d %b %Y')
            )
        elif date_from:
            date_info = _('from %s onwards') % date_from.strftime('%d %b %Y')
        elif date_to:
            date_info = _('up to %s') % date_to.strftime('%d %b %Y')
        else:
            date_info = _('all orders')

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Order Import Started'),
                'message': _(
                    'Importing %s from Shopify in the background (batches of 20). '
                    'You will receive a notification after each batch.'
                ) % date_info,
                'type': 'info',
                'sticky': False,
            }
        }

    def _export_products(self):
        """Export products to Shopify"""
        _logger.info(f'Starting product export to Shopify instance: {self.shopify_instance_id.name}')

        try:
            # Get products to export
            if self.product_ids:
                products = self.product_ids
            else:
                # Export only products linked to this instance or products without instance
                products = self.env['product.template'].search([
                    '|',
                    ('shopify_instance_id', '=', self.shopify_instance_id.id),
                    ('shopify_instance_id', '=', False)
                ])

            if not products:
                raise UserError(_('No products found to export'))

            success_count = 0
            error_count = 0
            errors = []

            for product in products:
                try:
                    # Set instance if not set
                    if not product.shopify_instance_id:
                        product.shopify_instance_id = self.shopify_instance_id.id

                    product.export_product_to_shopify()
                    success_count += 1
                except Exception as e:
                    error_count += 1
                    errors.append(f"{product.name}: {str(e)}")
                    _logger.error(f'Error exporting product {product.name}: {str(e)}')

            message = _('Successfully exported: %s products') % success_count
            if error_count > 0:
                message += _('\nFailed: %s products') % error_count
                if errors:
                    message += '\n\nErrors:\n' + '\n'.join(errors[:5])  # Show first 5 errors

            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Product Export Complete'),
                    'message': message,
                    'type': 'warning' if error_count > 0 else 'success',
                    'sticky': error_count > 0,
                }
            }

        except Exception as e:
            _logger.error(f'Error in product export: {str(e)}')
            raise UserError(_('Product export failed: %s') % str(e))

    def _export_customers(self):
        """Export customers to Shopify"""
        _logger.info(f'Starting customer export to Shopify instance: {self.shopify_instance_id.name}')

        try:
            # Get customers to export
            if self.customer_ids:
                customers = self.customer_ids
            else:
                # Export only customers linked to this instance or customers without instance
                customers = self.env['res.partner'].search([
                    ('customer_rank', '>', 0),
                    '|',
                    ('shopify_instance_id', '=', self.shopify_instance_id.id),
                    ('shopify_instance_id', '=', False)
                ])

            if not customers:
                raise UserError(_('No customers found to export'))

            success_count = 0
            error_count = 0
            errors = []

            for customer in customers:
                try:
                    # Set instance if not set
                    if not customer.shopify_instance_id:
                        customer.shopify_instance_id = self.shopify_instance_id.id

                    customer.export_customer_to_shopify()
                    success_count += 1
                except Exception as e:
                    error_count += 1
                    errors.append(f"{customer.name}: {str(e)}")
                    _logger.error(f'Error exporting customer {customer.name}: {str(e)}')

            message = _('Successfully exported: %s customers') % success_count
            if error_count > 0:
                message += _('\nFailed: %s customers') % error_count
                if errors:
                    message += '\n\nErrors:\n' + '\n'.join(errors[:5])  # Show first 5 errors

            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Customer Export Complete'),
                    'message': message,
                    'type': 'warning' if error_count > 0 else 'success',
                    'sticky': error_count > 0,
                }
            }

        except Exception as e:
            _logger.error(f'Error in customer export: {str(e)}')
            raise UserError(_('Customer export failed: %s') % str(e))

    def _import_collections(self):
        """Import collections from Shopify"""
        _logger.info(f'Starting collection import from Shopify instance: {self.shopify_instance_id.name}')

        try:
            # Create a dummy collection record to trigger sync
            collection = self.env['shopify.collection'].create({
                'name': 'Sync Trigger',
                'shopify_instance_id': self.shopify_instance_id.id,
            })
            result = collection.sync_from_shopify()
            collection.unlink()  # Remove the dummy record
            return result
        except Exception as e:
            _logger.error(f'Error in collection import: {str(e)}')
            raise UserError(_('Collection import failed: %s') % str(e))

    def _import_gift_cards(self):
        """Import gift cards from Shopify"""
        _logger.info(f'Starting gift card import from Shopify instance: {self.shopify_instance_id.name}')

        try:
            result = self.env['shopify.gift.card'].sync_from_shopify(self.shopify_instance_id.id)
            return result
        except Exception as e:
            _logger.error(f'Error in gift card import: {str(e)}')
            raise UserError(_('Gift card import failed: %s') % str(e))

    def _import_locations(self):
        """Import locations from Shopify"""
        _logger.info(f'Starting location import from Shopify instance: {self.shopify_instance_id.name}')

        try:
            result = self.env['shopify.inventory.location'].sync_locations_from_shopify(self.shopify_instance_id.id)
            return result
        except Exception as e:
            _logger.error(f'Error in location import: {str(e)}')
            raise UserError(_('Location import failed: %s') % str(e))

    def _import_discounts(self):
        """Import discounts from Shopify"""
        _logger.info(f'Starting discount import from Shopify instance: {self.shopify_instance_id.name}')

        try:
            result = self.env['shopify.discount'].sync_from_shopify(self.shopify_instance_id.id)
            return result
        except Exception as e:
            _logger.error(f'Error in discount import: {str(e)}')
            raise UserError(_('Discount import failed: %s') % str(e))
