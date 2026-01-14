# -*- coding: utf-8 -*-

from odoo import api, fields, models, _
from odoo.exceptions import UserError
import logging
import requests
import certifi
from dateutil import parser as date_parser

_logger = logging.getLogger(__name__)


class ShopifyGiftCard(models.Model):
    _name = 'shopify.gift.card'
    _description = 'Shopify Gift Card'
    _order = 'create_date desc'

    name = fields.Char('Gift Card Code', required=True, readonly=True)
    shopify_instance_id = fields.Many2one('shopify.instance', string='Shopify Instance', required=True, ondelete='cascade')
    shopify_gift_card_id = fields.Char('Shopify Gift Card ID', readonly=True)

    partner_id = fields.Many2one('res.partner', string='Customer')

    initial_value = fields.Monetary('Initial Value', currency_field='currency_id', readonly=True)
    balance = fields.Monetary('Balance', currency_field='currency_id', readonly=True)
    currency_id = fields.Many2one('res.currency', string='Currency', readonly=True)

    status = fields.Selection([
        ('enabled', 'Enabled'),
        ('disabled', 'Disabled'),
        ('expired', 'Expired'),
    ], string='Status', default='enabled', readonly=True)

    expires_on = fields.Date('Expires On', readonly=True)
    note = fields.Text('Note')

    last_characters = fields.Char('Last 4 Characters', readonly=True)

    shopify_created_at = fields.Datetime('Created At (Shopify)', readonly=True)
    shopify_updated_at = fields.Datetime('Updated At (Shopify)', readonly=True)

    @api.model
    def sync_from_shopify(self, instance_id):
        """Import gift cards from Shopify"""
        instance = self.env['shopify.instance'].browse(instance_id)
        if not instance:
            raise UserError(_('Shopify instance not found'))

        try:
            url = f"{instance._get_base_url()}/gift_cards.json"
            params = {'limit': 250}
            headers = instance._get_headers()

            response = requests.get(url, headers=headers, params=params, timeout=30, verify=certifi.where())

            if response.status_code == 200:
                gift_cards = response.json().get('gift_cards', [])

                for card_data in gift_cards:
                    self._create_or_update_gift_card(card_data, instance)

                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': _('Success'),
                        'message': _('Synced %s gift cards') % len(gift_cards),
                        'type': 'success',
                    }
                }
            else:
                raise UserError(_('Failed to fetch gift cards: %s') % response.text)

        except Exception as e:
            _logger.error(f'Error syncing gift cards: {str(e)}')
            raise UserError(_('Failed to sync gift cards: %s') % str(e))

    def _create_or_update_gift_card(self, card_data, instance):
        """Create or update gift card"""
        shopify_id = str(card_data.get('id'))

        existing = self.search([
            ('shopify_gift_card_id', '=', shopify_id),
            ('shopify_instance_id', '=', instance.id)
        ], limit=1)

        # Get currency
        currency_code = card_data.get('currency', 'USD')
        currency = self.env['res.currency'].search([('name', '=', currency_code)], limit=1)
        if not currency:
            currency = self.env.company.currency_id

        # Find customer by email
        partner = False
        customer_id = card_data.get('customer_id')
        if customer_id:
            partner = self.env['res.partner'].search([
                ('shopify_customer_id', '=', str(customer_id)),
                ('shopify_instance_id', '=', instance.id)
            ], limit=1)

        vals = {
            'name': card_data.get('code', card_data.get('last_characters', 'N/A')),
            'shopify_instance_id': instance.id,
            'shopify_gift_card_id': shopify_id,
            'partner_id': partner.id if partner else False,
            'initial_value': float(card_data.get('initial_value', 0.0)),
            'balance': float(card_data.get('balance', 0.0)),
            'currency_id': currency.id,
            'status': card_data.get('disabled_at') and 'disabled' or 'enabled',
            'last_characters': card_data.get('last_characters', ''),
            'note': card_data.get('note', ''),
        }

        # Parse expiry date
        expires_on = card_data.get('expires_on')
        if expires_on:
            try:
                vals['expires_on'] = date_parser.parse(expires_on).date()
            except:
                pass

        if existing:
            existing.write(vals)
        else:
            self.create(vals)
