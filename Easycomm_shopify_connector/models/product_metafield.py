# -*- coding: utf-8 -*-

from odoo import api, fields, models, _


class ShopifyProductMetafield(models.Model):
    _name = 'shopify.product.metafield'
    _description = 'Shopify Product/Variant Metafield'
    _order = 'namespace, key'

    name = fields.Char('Technical Key', compute='_compute_name', store=True)
    label = fields.Char('Field', help="Human-readable metafield label from its Shopify definition.")
    shopify_metafield_id = fields.Char('Shopify Metafield ID', index=True, copy=False)
    namespace = fields.Char('Namespace')
    key = fields.Char('Key')
    type = fields.Char('Type')
    value = fields.Text('Value')
    display_value = fields.Char('Display Value', compute='_compute_display_value',
        help='The value, or "Not configured" when empty in Shopify.')
    is_set = fields.Boolean('Has Value', compute='_compute_display_value')
    description = fields.Char('Description')

    owner_type = fields.Selection([
        ('product', 'Product'),
        ('variant', 'Variant'),
    ], string='Owner Type', default='product')

    product_tmpl_id = fields.Many2one('product.template', string='Product', ondelete='cascade', index=True)
    product_variant_id = fields.Many2one('product.product', string='Variant', ondelete='cascade', index=True)
    shopify_instance_id = fields.Many2one('shopify.instance', string='Shopify Instance', ondelete='cascade')

    @api.depends('namespace', 'key')
    def _compute_name(self):
        for rec in self:
            rec.name = f"{rec.namespace or ''}.{rec.key or ''}".strip('.')

    @api.depends('value')
    def _compute_display_value(self):
        for rec in self:
            has_value = bool(rec.value and str(rec.value).strip())
            rec.is_set = has_value
            rec.display_value = rec.value if has_value else _('Not configured')
