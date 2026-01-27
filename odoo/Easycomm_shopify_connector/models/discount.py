# -*- coding: utf-8 -*-

from odoo import api, fields, models, _
from odoo.exceptions import UserError
import logging
import requests
import certifi
from dateutil import parser as date_parser

_logger = logging.getLogger(__name__)


class ShopifyDiscount(models.Model):
    _name = 'shopify.discount'
    _description = 'Shopify Discount / Price Rule'
    _order = 'create_date desc'

    name = fields.Char('Discount Title', required=True)
    shopify_instance_id = fields.Many2one('shopify.instance', string='Shopify Instance', required=True, ondelete='cascade')
    shopify_price_rule_id = fields.Char('Shopify Price Rule ID', readonly=True)

    # Discount Code
    discount_code = fields.Char('Discount Code', readonly=True)
    discount_type = fields.Selection([
        ('percentage', 'Percentage'),
        ('fixed_amount', 'Fixed Amount'),
        ('shipping', 'Free Shipping'),
    ], string='Discount Type', readonly=True)

    value = fields.Float('Discount Value', readonly=True, help='Percentage or Fixed Amount')
    value_type = fields.Selection([
        ('percentage', 'Percentage'),
        ('fixed_amount', 'Fixed Amount'),
    ], string='Value Type', readonly=True)

    # Conditions
    target_type = fields.Selection([
        ('line_item', 'Line Item (Products)'),
        ('shipping_line', 'Shipping'),
    ], string='Target Type', readonly=True)

    target_selection = fields.Selection([
        ('all', 'All Products'),
        ('entitled', 'Specific Products/Collections'),
    ], string='Applies To', readonly=True)

    allocation_method = fields.Selection([
        ('across', 'Across All Items'),
        ('each', 'To Each Item'),
    ], string='Allocation Method', readonly=True)

    # Usage Limits
    usage_limit = fields.Integer('Usage Limit', readonly=True, help='Maximum number of times this discount can be used')
    once_per_customer = fields.Boolean('Once Per Customer', readonly=True)

    # Prerequisites
    prerequisite_subtotal_range = fields.Monetary('Minimum Purchase Amount', currency_field='currency_id', readonly=True)
    prerequisite_quantity_range = fields.Integer('Minimum Quantity', readonly=True)

    currency_id = fields.Many2one('res.currency', string='Currency', readonly=True)

    # Validity
    starts_at = fields.Datetime('Starts At', readonly=True)
    ends_at = fields.Datetime('Ends At', readonly=True)

    # Status
    active_discount = fields.Boolean('Active', readonly=True, default=True)

    # Products Associated
    entitled_product_ids = fields.Text('Entitled Product IDs', readonly=True, help='Comma-separated product IDs')
    entitled_collection_ids = fields.Text('Entitled Collection IDs', readonly=True, help='Comma-separated collection IDs')

    # Statistics
    usage_count = fields.Integer('Times Used', readonly=True)

    shopify_created_at = fields.Datetime('Created At (Shopify)', readonly=True)
    shopify_updated_at = fields.Datetime('Updated At (Shopify)', readonly=True)

    @api.model
    def sync_from_shopify(self, instance_id):
        """Import discounts/price rules from Shopify"""
        instance = self.env['shopify.instance'].browse(instance_id)
        if not instance:
            raise UserError(_('Shopify instance not found'))

        try:
            # Fetch price rules
            url = f"{instance._get_base_url()}/price_rules.json"
            headers = instance._get_headers()

            all_price_rules = []
            page_info = None

            while True:
                params = {'limit': 250}
                if page_info:
                    params['page_info'] = page_info

                response = requests.get(url, headers=headers, params=params, timeout=30, verify=certifi.where())

                if response.status_code == 200:
                    price_rules = response.json().get('price_rules', [])
                    all_price_rules.extend(price_rules)

                    # Check for pagination
                    link_header = response.headers.get('Link', '')
                    if 'rel="next"' in link_header:
                        # Extract page_info from Link header
                        for link in link_header.split(','):
                            if 'rel="next"' in link:
                                page_info = link.split('page_info=')[1].split('>')[0]
                                break
                    else:
                        break
                else:
                    raise UserError(_('Failed to fetch discounts: %s') % response.text)

            # Process each price rule
            for price_rule_data in all_price_rules:
                self._create_or_update_discount(price_rule_data, instance)

            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Success'),
                    'message': _('Synced %s discount rules') % len(all_price_rules),
                    'type': 'success',
                }
            }

        except Exception as e:
            _logger.error(f'Error syncing discounts: {str(e)}')
            raise UserError(_('Failed to sync discounts: %s') % str(e))

    def _create_or_update_discount(self, price_rule_data, instance):
        """Create or update discount record"""
        shopify_id = str(price_rule_data.get('id'))

        existing = self.search([
            ('shopify_price_rule_id', '=', shopify_id),
            ('shopify_instance_id', '=', instance.id)
        ], limit=1)

        # Get currency - use instance currency if available, otherwise company currency
        currency = instance.currency_id if instance.currency_id else self.env.company.currency_id

        # Parse value type
        value_type = price_rule_data.get('value_type', 'percentage')
        value = float(price_rule_data.get('value', 0.0))

        # If percentage, convert from -X.X to X.X (Shopify uses negative percentages)
        if value_type == 'percentage':
            value = abs(value)

        # Get discount codes for this price rule
        discount_code = ''
        try:
            discount_codes_url = f"{instance._get_base_url()}/price_rules/{shopify_id}/discount_codes.json"
            dc_response = requests.get(discount_codes_url, headers=instance._get_headers(), timeout=30, verify=certifi.where())
            if dc_response.status_code == 200:
                discount_codes = dc_response.json().get('discount_codes', [])
                if discount_codes:
                    discount_code = discount_codes[0].get('code', '')
        except Exception as e:
            _logger.warning(f'Could not fetch discount codes for price rule {shopify_id}: {str(e)}')

        vals = {
            'name': price_rule_data.get('title', 'Unnamed Discount'),
            'shopify_instance_id': instance.id,
            'shopify_price_rule_id': shopify_id,
            'discount_code': discount_code,
            'value': value,
            'value_type': value_type,
            'target_type': price_rule_data.get('target_type', 'line_item'),
            'target_selection': price_rule_data.get('target_selection', 'all'),
            'allocation_method': price_rule_data.get('allocation_method', 'across'),
            'usage_limit': price_rule_data.get('usage_limit', 0),
            'once_per_customer': price_rule_data.get('once_per_customer', False),
            'currency_id': currency.id,
            'active_discount': True,
        }

        # Parse prerequisite subtotal
        prerequisite_subtotal = price_rule_data.get('prerequisite_subtotal_range', {})
        if prerequisite_subtotal:
            vals['prerequisite_subtotal_range'] = float(prerequisite_subtotal.get('greater_than_or_equal_to', 0.0))

        # Parse prerequisite quantity
        prerequisite_quantity = price_rule_data.get('prerequisite_quantity_range', {})
        if prerequisite_quantity:
            vals['prerequisite_quantity_range'] = int(prerequisite_quantity.get('greater_than_or_equal_to', 0))

        # Parse entitled products
        entitled_product_ids = price_rule_data.get('entitled_product_ids', [])
        if entitled_product_ids:
            vals['entitled_product_ids'] = ','.join(map(str, entitled_product_ids))

        # Parse entitled collections
        entitled_collection_ids = price_rule_data.get('entitled_collection_ids', [])
        if entitled_collection_ids:
            vals['entitled_collection_ids'] = ','.join(map(str, entitled_collection_ids))

        # Parse dates
        starts_at = price_rule_data.get('starts_at')
        if starts_at:
            try:
                parsed_dt = date_parser.parse(starts_at)
                vals['starts_at'] = parsed_dt.replace(tzinfo=None)
            except:
                pass

        ends_at = price_rule_data.get('ends_at')
        if ends_at:
            try:
                parsed_dt = date_parser.parse(ends_at)
                vals['ends_at'] = parsed_dt.replace(tzinfo=None)
            except:
                pass

        created_at = price_rule_data.get('created_at')
        if created_at:
            try:
                parsed_dt = date_parser.parse(created_at)
                vals['shopify_created_at'] = parsed_dt.replace(tzinfo=None)
            except:
                pass

        updated_at = price_rule_data.get('updated_at')
        if updated_at:
            try:
                parsed_dt = date_parser.parse(updated_at)
                vals['shopify_updated_at'] = parsed_dt.replace(tzinfo=None)
            except:
                pass

        if existing:
            existing.write(vals)
            return existing
        else:
            return self.create(vals)

    def action_view_products(self):
        """View products associated with this discount"""
        self.ensure_one()

        if not self.entitled_product_ids:
            raise UserError(_('No specific products are associated with this discount'))

        product_ids = [int(pid.strip()) for pid in self.entitled_product_ids.split(',') if pid.strip()]

        # Find products by Shopify product IDs
        products = self.env['product.template'].search([
            ('shopify_product_id', 'in', [str(pid) for pid in product_ids]),
            ('shopify_instance_id', '=', self.shopify_instance_id.id)
        ])

        return {
            'name': _('Products with Discount'),
            'type': 'ir.actions.act_window',
            'res_model': 'product.template',
            'view_mode': 'list,form',
            'domain': [('id', 'in', products.ids)],
            'context': {'default_shopify_instance_id': self.shopify_instance_id.id}
        }

    def action_sync_single(self):
        """Sync single discount from Shopify"""
        self.ensure_one()

        try:
            instance = self.shopify_instance_id
            url = f"{instance._get_base_url()}/price_rules/{self.shopify_price_rule_id}.json"
            response = requests.get(url, headers=instance._get_headers(), timeout=30, verify=certifi.where())

            if response.status_code == 200:
                price_rule_data = response.json().get('price_rule', {})
                self._create_or_update_discount(price_rule_data, instance)

                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': _('Success'),
                        'message': _('Discount updated successfully'),
                        'type': 'success',
                    }
                }
            else:
                raise UserError(_('Failed to sync discount: %s') % response.text)

        except Exception as e:
            _logger.error(f'Error syncing discount: {str(e)}')
            raise UserError(_('Failed to sync discount: %s') % str(e))
