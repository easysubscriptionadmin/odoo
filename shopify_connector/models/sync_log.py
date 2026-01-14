# -*- coding: utf-8 -*-

from odoo import api, fields, models, _
import logging

_logger = logging.getLogger(__name__)


class ShopifySyncLog(models.Model):
    _name = 'shopify.sync.log'
    _description = 'Shopify Sync Log'
    _order = 'create_date desc'

    name = fields.Char('Log Reference', required=True, default='New')
    shopify_instance_id = fields.Many2one('shopify.instance', string='Shopify Instance', required=True, ondelete='cascade')

    sync_type = fields.Selection([
        ('product', 'Product Sync'),
        ('customer', 'Customer Sync'),
        ('order', 'Order Sync'),
        ('inventory', 'Inventory Sync'),
        ('webhook', 'Webhook'),
        ('payment', 'Payment Sync'),
        ('refund', 'Refund Sync'),
    ], string='Sync Type', required=True)

    direction = fields.Selection([
        ('import', 'Shopify to Odoo'),
        ('export', 'Odoo to Shopify'),
    ], string='Direction', required=True)

    status = fields.Selection([
        ('success', 'Success'),
        ('failed', 'Failed'),
        ('partial', 'Partial Success'),
    ], string='Status', required=True, default='success')

    record_id = fields.Integer('Record ID')
    record_model = fields.Char('Record Model')
    shopify_id = fields.Char('Shopify ID')

    message = fields.Text('Message')
    error_details = fields.Text('Error Details')

    created_count = fields.Integer('Created Records', default=0)
    updated_count = fields.Integer('Updated Records', default=0)
    failed_count = fields.Integer('Failed Records', default=0)

    duration = fields.Float('Duration (seconds)', digits=(16, 2))

    @api.model
    def create(self, vals):
        if vals.get('name', 'New') == 'New':
            sync_type = vals.get('sync_type', 'sync')
            vals['name'] = f"{sync_type.upper()}/{self.env['ir.sequence'].next_by_code('shopify.sync.log') or 'NEW'}"
        return super(ShopifySyncLog, self).create(vals)

    @api.model
    def log_sync(self, instance_id, sync_type, direction, status, **kwargs):
        """Helper method to create sync log"""
        vals = {
            'shopify_instance_id': instance_id,
            'sync_type': sync_type,
            'direction': direction,
            'status': status,
            'record_id': kwargs.get('record_id'),
            'record_model': kwargs.get('record_model'),
            'shopify_id': kwargs.get('shopify_id'),
            'message': kwargs.get('message', ''),
            'error_details': kwargs.get('error_details', ''),
            'created_count': kwargs.get('created_count', 0),
            'updated_count': kwargs.get('updated_count', 0),
            'failed_count': kwargs.get('failed_count', 0),
            'duration': kwargs.get('duration', 0.0),
        }
        return self.create(vals)

    def action_retry(self):
        """Retry failed sync operation"""
        self.ensure_one()

        if self.status != 'failed':
            return

        # Based on sync type, trigger appropriate sync
        if self.sync_type == 'product':
            if self.direction == 'import':
                self.env['product.template'].import_shopify_products(self.shopify_instance_id.id)
            else:
                product = self.env['product.template'].browse(self.record_id)
                if product.exists():
                    product.export_product_to_shopify()

        elif self.sync_type == 'customer':
            if self.direction == 'import':
                self.env['res.partner'].import_shopify_customers(self.shopify_instance_id.id)

        elif self.sync_type == 'order':
            if self.direction == 'import':
                self.env['sale.order'].import_shopify_orders(self.shopify_instance_id.id)

        elif self.sync_type == 'inventory':
            if self.direction == 'export':
                sync_record = self.env['shopify.inventory.sync'].create({
                    'shopify_instance_id': self.shopify_instance_id.id,
                    'sync_type': 'manual',
                })
                sync_record.sync_inventory_to_shopify()
