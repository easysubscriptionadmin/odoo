# -*- coding: utf-8 -*-

from odoo import api, fields, models, _
from odoo.exceptions import UserError
import logging

_logger = logging.getLogger(__name__)


class ShopifyPaymentTransaction(models.Model):
    _name = 'shopify.payment.transaction'
    _description = 'Shopify Payment Transaction'
    _order = 'transaction_date desc'

    name = fields.Char('Transaction Reference', required=True, readonly=True)
    shopify_instance_id = fields.Many2one('shopify.instance', string='Shopify Instance', required=True, ondelete='cascade')
    shopify_transaction_id = fields.Char('Shopify Transaction ID', required=True, readonly=True)

    order_id = fields.Many2one('sale.order', string='Sale Order', readonly=True)
    shopify_order_id = fields.Char('Shopify Order ID', readonly=True)

    partner_id = fields.Many2one('res.partner', string='Customer', readonly=True)

    amount = fields.Monetary('Amount', currency_field='currency_id', readonly=True)
    currency_id = fields.Many2one('res.currency', string='Currency', readonly=True)

    payment_method = fields.Char('Payment Method', readonly=True)
    gateway = fields.Char('Payment Gateway', readonly=True)

    status = fields.Selection([
        ('pending', 'Pending'),
        ('authorized', 'Authorized'),
        ('success', 'Success'),
        ('paid', 'Paid'),
        ('partially_paid', 'Partially Paid'),
        ('refunded', 'Refunded'),
        ('partially_refunded', 'Partially Refunded'),
        ('voided', 'Voided'),
        ('failed', 'Failed'),
        ('error', 'Error'),
    ], string='Status', required=True, readonly=True)

    kind = fields.Selection([
        ('authorization', 'Authorization'),
        ('capture', 'Capture'),
        ('sale', 'Sale'),
        ('void', 'Void'),
        ('refund', 'Refund'),
    ], string='Transaction Kind', readonly=True)

    transaction_date = fields.Datetime('Transaction Date', readonly=True)

    authorization_code = fields.Char('Authorization Code', readonly=True)
    receipt = fields.Text('Receipt Details', readonly=True)

    error_message = fields.Text('Error Message', readonly=True)

    test_transaction = fields.Boolean('Test Transaction', default=False, readonly=True)

    @api.model
    def sync_transactions_for_order(self, shopify_order_id, instance):
        """Sync payment transactions for a specific Shopify order"""
        try:
            import requests
            import certifi

            url = f"{instance._get_base_url()}/orders/{shopify_order_id}/transactions.json"
            response = requests.get(
                url,
                headers=instance._get_headers(),
                timeout=30,
                verify=certifi.where()
            )

            if response.status_code != 200:
                _logger.error(f'Failed to fetch transactions: {response.status_code} - {response.text}')
                return

            transactions = response.json().get('transactions', [])

            for trans_data in transactions:
                self._create_or_update_transaction(trans_data, instance, shopify_order_id)

        except Exception as e:
            _logger.error(f'Error syncing transactions: {str(e)}')

    @api.model
    def _create_or_update_transaction(self, trans_data, instance, shopify_order_id):
        """Create or update payment transaction"""
        try:
            shopify_trans_id = str(trans_data.get('id'))

            existing_trans = self.search([
                ('shopify_transaction_id', '=', shopify_trans_id),
                ('shopify_instance_id', '=', instance.id)
            ], limit=1)

            # Find related order
            order = self.env['sale.order'].search([
                ('shopify_order_id', '=', str(shopify_order_id)),
                ('shopify_instance_id', '=', instance.id)
            ], limit=1)

            # Get currency
            currency_code = trans_data.get('currency', 'USD')
            currency = self.env['res.currency'].search([('name', '=', currency_code)], limit=1)
            if not currency:
                currency = self.env.company.currency_id

            vals = {
                'name': f"TRANS/{shopify_trans_id}",
                'shopify_instance_id': instance.id,
                'shopify_transaction_id': shopify_trans_id,
                'shopify_order_id': str(shopify_order_id),
                'order_id': order.id if order else False,
                'partner_id': order.partner_id.id if order else False,
                'amount': float(trans_data.get('amount', 0.0)),
                'currency_id': currency.id,
                'payment_method': trans_data.get('payment_details', {}).get('credit_card_company', 'Unknown'),
                'gateway': trans_data.get('gateway', 'Unknown'),
                'status': trans_data.get('status', 'pending'),
                'kind': trans_data.get('kind', 'sale'),
                'transaction_date': fields.Datetime.now(),
                'authorization_code': trans_data.get('authorization', ''),
                'receipt': str(trans_data.get('receipt', {})),
                'error_message': trans_data.get('message', ''),
                'test_transaction': trans_data.get('test', False),
            }

            if existing_trans:
                existing_trans.write(vals)
            else:
                self.create(vals)

        except Exception as e:
            _logger.error(f'Error creating/updating transaction: {str(e)}')


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    shopify_transaction_ids = fields.One2many(
        'shopify.payment.transaction',
        'order_id',
        string='Shopify Transactions',
        readonly=True
    )

    shopify_payment_status = fields.Selection([
        ('pending', 'Pending'),
        ('authorized', 'Authorized'),
        ('success', 'Success'),
        ('paid', 'Paid'),
        ('partially_paid', 'Partially Paid'),
        ('refunded', 'Refunded'),
        ('partially_refunded', 'Partially Refunded'),
        ('voided', 'Voided'),
    ], string='Payment Status', compute='_compute_payment_status')

    @api.depends('shopify_transaction_ids.status')
    def _compute_payment_status(self):
        for order in self:
            if not order.shopify_transaction_ids:
                order.shopify_payment_status = 'pending'
                continue

            statuses = order.shopify_transaction_ids.mapped('status')
            if 'paid' in statuses or 'success' in statuses:
                order.shopify_payment_status = 'paid'
            elif 'refunded' in statuses:
                order.shopify_payment_status = 'refunded'
            elif 'authorized' in statuses:
                order.shopify_payment_status = 'authorized'
            else:
                order.shopify_payment_status = 'pending'
