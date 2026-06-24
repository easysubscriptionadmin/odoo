# -*- coding: utf-8 -*-

from odoo import api, fields, models, _
from odoo.exceptions import UserError

import logging

_logger = logging.getLogger(__name__)


class ShopifyRefundWizard(models.TransientModel):
    _name = 'shopify.refund.wizard'
    _description = 'Create Refund in Shopify'

    order_id = fields.Many2one('sale.order', string='Order', required=True, readonly=True)
    currency_id = fields.Many2one(related='order_id.currency_id', readonly=True)
    order_amount = fields.Monetary('Order Total', related='order_id.amount_total', readonly=True, currency_field='currency_id')
    shopify_order_id = fields.Char('Shopify Order #', related='order_id.shopify_order_number', readonly=True)
    refund_amount = fields.Float('Refund Amount', required=True, digits=(16, 2))
    refund_note = fields.Text('Note', default='Refund from Odoo')
    notify_customer = fields.Boolean('Notify Customer', default=True)
    restock = fields.Boolean('Restock Items', default=False)

    @api.constrains('refund_amount')
    def _check_refund_amount(self):
        for rec in self:
            if rec.refund_amount <= 0:
                raise UserError(_('Refund amount must be greater than zero'))
            if rec.refund_amount > rec.order_amount:
                raise UserError(_('Refund amount (%s) cannot exceed the order total (%s)') % (
                    rec.refund_amount, rec.order_amount))

    def action_create_refund(self):
        self.ensure_one()
        try:
            return self.order_id.create_refund_in_shopify(
                amount=self.refund_amount,
                note=self.refund_note or '',
                notify=self.notify_customer,
                restock=self.restock,
            )
        except UserError:
            # Already a clean, user-facing message — let it through.
            raise
        except Exception as e:
            _logger.error('Shopify refund failed for order %s: %s',
                          self.order_id.name, e, exc_info=True)
            raise UserError(_(
                'Could not create the refund in Shopify.\n\n%s\n\n'
                'Please check the order is still valid in Shopify and try again, '
                'or review the server log for details.'
            ) % e)
