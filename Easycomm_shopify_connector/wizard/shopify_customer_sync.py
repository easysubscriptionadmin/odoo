# -*- coding: utf-8 -*-

from odoo import api, fields, models, _
from odoo.exceptions import UserError
import logging

_logger = logging.getLogger(__name__)


class ShopifyCustomerSyncWizard(models.TransientModel):
    _name = 'shopify.customer.sync.wizard'
    _description = 'Sync Customers to Shopify'

    shopify_instance_id = fields.Many2one(
        'shopify.instance', string='Shopify Instance', required=True,
        help='The Shopify store this customer will be created/updated in.')
    partner_ids = fields.Many2many('res.partner', string='Customers', required=True)
    customer_count = fields.Integer('Customer Count', compute='_compute_customer_count')

    @api.depends('partner_ids')
    def _compute_customer_count(self):
        for wiz in self:
            wiz.customer_count = len(wiz.partner_ids)

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        ctx = self.env.context
        if ctx.get('active_model') == 'res.partner' and ctx.get('active_ids'):
            res['partner_ids'] = [(6, 0, ctx['active_ids'])]
        instances = self.env['shopify.instance'].search([('active', '=', True)])
        if len(instances) == 1:
            res.setdefault('shopify_instance_id', instances.id)
        elif res.get('partner_ids'):
            existing = self.env['res.partner'].browse(
                res['partner_ids'][0][2]).mapped('shopify_instance_id')
            if len(existing) == 1:
                res.setdefault('shopify_instance_id', existing.id)
        return res

    def action_sync(self):
        self.ensure_one()
        if not self.partner_ids:
            raise UserError(_('Please select at least one customer to sync.'))

        success, errors = 0, []
        for partner in self.partner_ids:
            try:
                partner.shopify_instance_id = self.shopify_instance_id.id
                partner.export_customer_to_shopify()
                success += 1
            except Exception as e:
                errors.append(f"{partner.name}: {e}")
                _logger.error('Customer sync to Shopify failed for %s: %s', partner.name, e)

        message = _('Synced %(ok)s customer(s) to %(store)s.') % {
            'ok': success, 'store': self.shopify_instance_id.name}
        if errors:
            message += _('\nFailed: %s') % len(errors)
            message += '\n\n' + '\n'.join(errors[:5])

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Shopify Customer Sync'),
                'message': message,
                'type': 'warning' if errors else 'success',
                'sticky': bool(errors),
                'next': {'type': 'ir.actions.act_window_close'},
            },
        }
