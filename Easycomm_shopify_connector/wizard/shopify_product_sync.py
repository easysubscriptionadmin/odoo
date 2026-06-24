# -*- coding: utf-8 -*-

from odoo import api, fields, models, _
from odoo.exceptions import UserError
import logging

_logger = logging.getLogger(__name__)


class ShopifyProductSyncWizard(models.TransientModel):
    _name = 'shopify.product.sync.wizard'
    _description = 'Sync Products to Shopify'

    shopify_instance_id = fields.Many2one(
        'shopify.instance', string='Shopify Instance', required=True,
        help='The Shopify store this product will be created/updated in.')
    product_ids = fields.Many2many(
        'product.template', string='Products', required=True)
    product_count = fields.Integer('Product Count', compute='_compute_product_count')
    export_images = fields.Boolean(
        'Also Export Images', default=True,
        help='Upload the product images to Shopify (only for newly created products, to avoid duplicates).')

    @api.depends('product_ids')
    def _compute_product_count(self):
        for wiz in self:
            wiz.product_count = len(wiz.product_ids)

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        ctx = self.env.context
        if ctx.get('active_model') == 'product.template' and ctx.get('active_ids'):
            res['product_ids'] = [(6, 0, ctx['active_ids'])]
        elif ctx.get('active_model') == 'product.product' and ctx.get('active_ids'):
            tmpl_ids = self.env['product.product'].browse(ctx['active_ids']).product_tmpl_id.ids
            res['product_ids'] = [(6, 0, tmpl_ids)]
        # Pre-select the instance if there is only one, or reuse one already set.
        instances = self.env['shopify.instance'].search([('active', '=', True)])
        if len(instances) == 1:
            res.setdefault('shopify_instance_id', instances.id)
        elif res.get('product_ids'):
            existing = self.env['product.template'].browse(
                res['product_ids'][0][2]).mapped('shopify_instance_id')
            if len(existing) == 1:
                res.setdefault('shopify_instance_id', existing.id)
        return res

    def action_sync(self):
        self.ensure_one()
        if not self.product_ids:
            raise UserError(_('Please select at least one product to sync.'))

        success, errors = 0, []
        for product in self.product_ids:
            try:
                product.shopify_instance_id = self.shopify_instance_id.id
                product.export_product_to_shopify(export_images=self.export_images)
                success += 1
            except Exception as e:
                errors.append(f"{product.name}: {e}")
                _logger.error('Sync to Shopify failed for %s: %s', product.name, e)

        message = _('Synced %(ok)s product(s) to %(store)s.') % {
            'ok': success, 'store': self.shopify_instance_id.name}
        if errors:
            message += _('\nFailed: %s') % len(errors)
            message += '\n\n' + '\n'.join(errors[:5])

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Shopify Sync'),
                'message': message,
                'type': 'warning' if errors else 'success',
                'sticky': bool(errors),
                'next': {'type': 'ir.actions.act_window_close'},
            },
        }
