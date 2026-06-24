# -*- coding: utf-8 -*-

from odoo import fields, models


class ShopifyProductMedia(models.Model):
    _name = 'shopify.product.media'
    _description = 'Shopify Product Image/Media'
    _inherit = ['image.mixin']
    _order = 'sequence, id'

    name = fields.Char('Name')
    sequence = fields.Integer('Position', default=10)
    src = fields.Char('Source URL')
    alt = fields.Char('Alt Text')
    shopify_image_id = fields.Char('Shopify Image ID', copy=False)
    product_tmpl_id = fields.Many2one('product.template', string='Product',
                                      ondelete='cascade', index=True)
    shopify_instance_id = fields.Many2one('shopify.instance', string='Shopify Instance',
                                          ondelete='cascade')
