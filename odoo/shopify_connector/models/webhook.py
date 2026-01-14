# -*- coding: utf-8 -*-

from odoo import api, fields, models, _
from odoo.exceptions import UserError
import logging
import requests
import certifi
import json

_logger = logging.getLogger(__name__)


class ShopifyWebhook(models.Model):
    _name = 'shopify.webhook'
    _description = 'Shopify Webhook Configuration'
    _order = 'create_date desc'

    name = fields.Char('Webhook Name', required=True)
    shopify_instance_id = fields.Many2one('shopify.instance', string='Shopify Instance', required=True, ondelete='cascade')
    shopify_webhook_id = fields.Char('Shopify Webhook ID', readonly=True)

    topic = fields.Selection([
        ('products/create', 'Product Created'),
        ('products/update', 'Product Updated'),
        ('products/delete', 'Product Deleted'),
        ('orders/create', 'Order Created'),
        ('orders/updated', 'Order Updated'),
        ('orders/cancelled', 'Order Cancelled'),
        ('orders/fulfilled', 'Order Fulfilled'),
        ('customers/create', 'Customer Created'),
        ('customers/update', 'Customer Updated'),
        ('inventory_levels/update', 'Inventory Updated'),
        ('refunds/create', 'Refund Created'),
    ], string='Webhook Topic', required=True)

    webhook_url = fields.Char('Webhook URL', required=True, help='Your Odoo server URL that will receive webhook calls')
    active = fields.Boolean('Active', default=True)

    format = fields.Selection([
        ('json', 'JSON'),
    ], string='Format', default='json', readonly=True)

    created_at = fields.Datetime('Created At', readonly=True)
    updated_at = fields.Datetime('Updated At', readonly=True)

    def create_webhook_in_shopify(self):
        """Create webhook in Shopify"""
        self.ensure_one()

        instance = self.shopify_instance_id

        try:
            webhook_data = {
                'webhook': {
                    'topic': self.topic,
                    'address': self.webhook_url,
                    'format': self.format,
                }
            }

            url = f"{instance._get_base_url()}/webhooks.json"
            response = requests.post(
                url,
                headers=instance._get_headers(),
                json=webhook_data,
                timeout=30,
                verify=certifi.where()
            )

            if response.status_code == 201:
                webhook = response.json().get('webhook', {})
                self.write({
                    'shopify_webhook_id': str(webhook.get('id')),
                    'created_at': fields.Datetime.now(),
                })

                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': _('Success'),
                        'message': _('Webhook created successfully in Shopify'),
                        'type': 'success',
                    }
                }
            else:
                raise UserError(_('Failed to create webhook: %s - %s') % (response.status_code, response.text))

        except Exception as e:
            _logger.error(f'Error creating webhook: {str(e)}')
            raise UserError(_('Error creating webhook: %s') % str(e))

    def delete_webhook_from_shopify(self):
        """Delete webhook from Shopify"""
        self.ensure_one()

        if not self.shopify_webhook_id:
            raise UserError(_('No Shopify webhook ID found'))

        instance = self.shopify_instance_id

        try:
            url = f"{instance._get_base_url()}/webhooks/{self.shopify_webhook_id}.json"
            response = requests.delete(
                url,
                headers=instance._get_headers(),
                timeout=30,
                verify=certifi.where()
            )

            if response.status_code == 200:
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': _('Success'),
                        'message': _('Webhook deleted successfully from Shopify'),
                        'type': 'success',
                    }
                }
            else:
                raise UserError(_('Failed to delete webhook: %s - %s') % (response.status_code, response.text))

        except Exception as e:
            _logger.error(f'Error deleting webhook: {str(e)}')
            raise UserError(_('Error deleting webhook: %s') % str(e))

    @api.model
    def process_webhook(self, topic, data, shopify_domain):
        """Process incoming webhook from Shopify"""
        try:
            # Find the instance based on shop domain
            instance = self.env['shopify.instance'].search([
                ('shop_url', 'ilike', shopify_domain)
            ], limit=1)

            if not instance:
                _logger.warning(f'No instance found for domain: {shopify_domain}')
                return False

            # Log webhook receipt
            self.env['shopify.sync.log'].log_sync(
                instance_id=instance.id,
                sync_type='webhook',
                direction='import',
                status='success',
                message=f'Received webhook: {topic}',
            )

            # Process based on topic
            if topic.startswith('products/'):
                self._process_product_webhook(topic, data, instance)
            elif topic.startswith('orders/'):
                self._process_order_webhook(topic, data, instance)
            elif topic.startswith('customers/'):
                self._process_customer_webhook(topic, data, instance)
            elif topic.startswith('inventory_levels/'):
                self._process_inventory_webhook(topic, data, instance)
            elif topic.startswith('refunds/'):
                self._process_refund_webhook(topic, data, instance)

            return True

        except Exception as e:
            _logger.error(f'Error processing webhook: {str(e)}')
            return False

    def _process_product_webhook(self, topic, data, instance):
        """Process product webhook"""
        product_model = self.env['product.template']

        if topic in ['products/create', 'products/update']:
            product_vals = product_model._prepare_product_vals(data, instance)
            existing_product = product_model.search([
                ('shopify_product_id', '=', str(data['id'])),
                ('shopify_instance_id', '=', instance.id)
            ], limit=1)

            if existing_product:
                existing_product.write(product_vals)
            else:
                product_model.create(product_vals)

        elif topic == 'products/delete':
            product = product_model.search([
                ('shopify_product_id', '=', str(data['id'])),
                ('shopify_instance_id', '=', instance.id)
            ], limit=1)
            if product:
                product.write({'active': False})

    def _process_order_webhook(self, topic, data, instance):
        """Process order webhook"""
        order_model = self.env['sale.order']

        if topic in ['orders/create', 'orders/updated']:
            order_vals = order_model._prepare_order_vals(data, instance)
            existing_order = order_model.search([
                ('shopify_order_id', '=', str(data['id'])),
                ('shopify_instance_id', '=', instance.id)
            ], limit=1)

            if existing_order:
                existing_order.write(order_vals)
            else:
                order_model.create(order_vals)

    def _process_customer_webhook(self, topic, data, instance):
        """Process customer webhook"""
        partner_model = self.env['res.partner']

        if topic in ['customers/create', 'customers/update']:
            partner_vals = partner_model._prepare_customer_vals(data, instance)
            existing_partner = partner_model.search([
                ('shopify_customer_id', '=', str(data['id'])),
                ('shopify_instance_id', '=', instance.id)
            ], limit=1)

            if existing_partner:
                existing_partner.write(partner_vals)
            else:
                partner_model.create(partner_vals)

    def _process_inventory_webhook(self, topic, data, instance):
        """Process inventory webhook"""
        # Log the inventory update
        _logger.info(f'Inventory webhook received: {data}')

    def _process_refund_webhook(self, topic, data, instance):
        """Process refund webhook"""
        # Log the refund
        _logger.info(f'Refund webhook received: {data}')
