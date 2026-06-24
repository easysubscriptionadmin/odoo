# -*- coding: utf-8 -*-

from odoo import api, fields, models, _
from odoo.exceptions import UserError
import logging
import requests
import certifi
import json

_logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Master list of all supported Shopify webhook topics
# ---------------------------------------------------------------------------
WEBHOOK_TOPICS = [
    # ── Orders ──────────────────────────────────────────────────────────
    ('orders/create',              'Order: Created'),
    ('orders/updated',             'Order: Updated'),
    ('orders/cancelled',           'Order: Cancelled'),
    ('orders/fulfilled',           'Order: Fulfilled'),
    ('orders/paid',                'Order: Paid'),
    ('orders/partially_fulfilled', 'Order: Partially Fulfilled'),
    ('orders/delete',              'Order: Deleted'),
    ('orders/edited',              'Order: Edited'),
    # ── Draft Orders ────────────────────────────────────────────────────
    ('draft_orders/create',        'Draft Order: Created'),
    ('draft_orders/update',        'Draft Order: Updated'),
    ('draft_orders/delete',        'Draft Order: Deleted'),
    # ── Customers ───────────────────────────────────────────────────────
    ('customers/create',           'Customer: Created'),
    ('customers/update',           'Customer: Updated'),
    ('customers/delete',           'Customer: Deleted'),
    ('customers/enable',           'Customer: Enabled'),
    ('customers/disable',          'Customer: Disabled'),
    ('customers/merge',            'Customer: Merged'),
    # ── Products ────────────────────────────────────────────────────────
    ('products/create',            'Product: Created'),
    ('products/update',            'Product: Updated'),
    ('products/delete',            'Product: Deleted'),
    # ── Collections ─────────────────────────────────────────────────────
    ('collections/create',         'Collection: Created'),
    ('collections/update',         'Collection: Updated'),
    ('collections/delete',         'Collection: Deleted'),
    # ── Inventory ───────────────────────────────────────────────────────
    ('inventory_levels/connect',      'Inventory Level: Connected'),
    ('inventory_levels/update',       'Inventory Level: Updated'),
    ('inventory_levels/disconnect',   'Inventory Level: Disconnected'),
    ('inventory_items/create',        'Inventory Item: Created'),
    ('inventory_items/update',        'Inventory Item: Updated'),
    ('inventory_items/delete',        'Inventory Item: Deleted'),
    # ── Fulfillments ────────────────────────────────────────────────────
    ('fulfillments/create',           'Fulfillment: Created'),
    ('fulfillments/update',           'Fulfillment: Updated'),
    # ── Fulfillment Orders ──────────────────────────────────────────────
    ('fulfillment_orders/cancelled',                      'Fulfillment Order: Cancelled'),
    ('fulfillment_orders/fulfillment_requested',          'Fulfillment Order: Requested'),
    ('fulfillment_orders/fulfillment_service_failed',     'Fulfillment Order: Service Failed'),
    ('fulfillment_orders/hold_released',                  'Fulfillment Order: Hold Released'),
    ('fulfillment_orders/moved',                          'Fulfillment Order: Moved'),
    ('fulfillment_orders/placed_on_hold',                 'Fulfillment Order: Placed on Hold'),
    ('fulfillment_orders/rescheduled',                    'Fulfillment Order: Rescheduled'),
    # ── Refunds ─────────────────────────────────────────────────────────
    ('refunds/create',             'Refund: Created'),
    # ── Checkouts ───────────────────────────────────────────────────────
    ('checkouts/create',           'Checkout: Created'),
    ('checkouts/update',           'Checkout: Updated'),
    ('checkouts/delete',           'Checkout: Deleted'),
    # ── Carts ───────────────────────────────────────────────────────────
    ('carts/create',               'Cart: Created'),
    ('carts/update',               'Cart: Updated'),
    # ── Locations ───────────────────────────────────────────────────────
    ('locations/create',           'Location: Created'),
    ('locations/update',           'Location: Updated'),
    ('locations/activate',         'Location: Activated'),
    ('locations/deactivate',       'Location: Deactivated'),
    ('locations/delete',           'Location: Deleted'),
    # ── Order Transactions ──────────────────────────────────────────────
    ('order_transactions/create',  'Order Transaction: Created'),
    # ── Themes ──────────────────────────────────────────────────────────
    ('themes/create',              'Theme: Created'),
    ('themes/update',              'Theme: Updated'),
    ('themes/delete',              'Theme: Deleted'),
    ('themes/publish',             'Theme: Published'),
    # ── Shop / App ──────────────────────────────────────────────────────
    ('shop/update',                'Shop: Updated'),
    ('app/uninstalled',            'App: Uninstalled'),
    # ── Mandatory GDPR ──────────────────────────────────────────────────
    ('customers/data_request',     'GDPR: Customer Data Request'),
    ('customers/redact',           'GDPR: Customer Redact'),
    ('shop/redact',                'GDPR: Shop Redact'),
]

# Topics registered automatically when "Register All" is clicked
DEFAULT_TOPICS = [
    'orders/create',
    'orders/updated',
    'orders/cancelled',
    'orders/fulfilled',
    'orders/paid',
    'orders/delete',
    'draft_orders/create',
    'draft_orders/update',
    'customers/create',
    'customers/update',
    'customers/delete',
    'products/create',
    'products/update',
    'products/delete',
    'collections/create',
    'collections/update',
    'collections/delete',
    'inventory_levels/update',
    'inventory_items/update',
    'fulfillments/create',
    'fulfillments/update',
    'refunds/create',
    'order_transactions/create',
    'locations/create',
    'locations/update',
    'locations/delete',
    # Mandatory GDPR webhooks
    'customers/data_request',
    'customers/redact',
    'shop/redact',
]

_TOPIC_LABEL = dict(WEBHOOK_TOPICS)


class ShopifyWebhook(models.Model):
    _name = 'shopify.webhook'
    _description = 'Shopify Webhook Configuration'
    _order = 'topic, create_date desc'

    name = fields.Char('Webhook Name', required=True)
    shopify_instance_id = fields.Many2one(
        'shopify.instance', string='Shopify Instance', required=True, ondelete='cascade'
    )
    shopify_webhook_id = fields.Char('Shopify Webhook ID', readonly=True)
    topic = fields.Selection(WEBHOOK_TOPICS, string='Webhook Topic', required=True)
    webhook_url = fields.Char(
        'Webhook URL', required=True,
        help='Public Odoo URL that Shopify will POST to (e.g. https://yourodoo.com/shopify/webhook)',
    )
    active = fields.Boolean('Active', default=True)
    format = fields.Selection([('json', 'JSON')], string='Format', default='json', readonly=True)
    created_at = fields.Datetime('Created At', readonly=True)
    updated_at = fields.Datetime('Updated At', readonly=True)

    # ──────────────────────────────────────────────────────────────────────
    # CRUD helpers
    # ──────────────────────────────────────────────────────────────────────

    def create_webhook_in_shopify(self):
        """Register this single webhook record in Shopify."""
        self.ensure_one()
        instance = self.shopify_instance_id
        try:
            payload = {
                'webhook': {
                    'topic': self.topic,
                    'address': self.webhook_url,
                    'format': self.format,
                }
            }
            url = f"{instance._get_base_url()}/webhooks.json"
            response = requests.post(
                url, headers=instance._get_headers(),
                json=payload, timeout=30, verify=certifi.where(),
            )
            if response.status_code == 201:
                wh = response.json().get('webhook', {})
                self.write({'shopify_webhook_id': str(wh.get('id')), 'created_at': fields.Datetime.now()})
                return {
                    'type': 'ir.actions.client', 'tag': 'display_notification',
                    'params': {'title': _('Success'), 'message': _('Webhook created in Shopify'), 'type': 'success'},
                }
            else:
                raise UserError(_('Failed to create webhook: %s - %s') % (response.status_code, response.text))
        except UserError:
            raise
        except Exception as e:
            _logger.error(f'Error creating webhook: {e}')
            raise UserError(_('Error creating webhook: %s') % str(e))

    def delete_webhook_from_shopify(self):
        """Remove this webhook from Shopify."""
        self.ensure_one()
        if not self.shopify_webhook_id:
            raise UserError(_('No Shopify webhook ID found'))
        instance = self.shopify_instance_id
        try:
            url = f"{instance._get_base_url()}/webhooks/{self.shopify_webhook_id}.json"
            response = requests.delete(
                url, headers=instance._get_headers(), timeout=30, verify=certifi.where(),
            )
            if response.status_code == 200:
                self.write({'shopify_webhook_id': False})
                return {
                    'type': 'ir.actions.client', 'tag': 'display_notification',
                    'params': {'title': _('Success'), 'message': _('Webhook deleted from Shopify'), 'type': 'success'},
                }
            else:
                raise UserError(_('Failed to delete webhook: %s - %s') % (response.status_code, response.text))
        except UserError:
            raise
        except Exception as e:
            _logger.error(f'Error deleting webhook: {e}')
            raise UserError(_('Error deleting webhook: %s') % str(e))

    # ──────────────────────────────────────────────────────────────────────
    # Bulk registration
    # ──────────────────────────────────────────────────────────────────────

    @api.model
    def register_all_webhooks(self, instance_id, base_url=None):
        """Register all default webhook topics for the given Shopify instance.

        Skips topics that already have a Shopify webhook ID.
        Uses `web.base.url` system parameter when base_url is not provided.
        """
        instance = self.env['shopify.instance'].browse(instance_id)
        if not instance:
            raise UserError(_('Shopify instance not found'))

        if not base_url:
            base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url', '')

        webhook_url = f"{base_url.rstrip('/')}/shopify/webhook"
        results = {'created': 0, 'skipped': 0, 'errors': []}

        for topic in DEFAULT_TOPICS:
            existing = self.search([
                ('shopify_instance_id', '=', instance.id),
                ('topic', '=', topic),
            ], limit=1)

            if existing and existing.shopify_webhook_id:
                results['skipped'] += 1
                _logger.info(f'[register_all_webhooks] Skipping {topic} — already registered')
                continue

            try:
                payload = {
                    'webhook': {'topic': topic, 'address': webhook_url, 'format': 'json'}
                }
                url = f"{instance._get_base_url()}/webhooks.json"
                response = requests.post(
                    url, headers=instance._get_headers(),
                    json=payload, timeout=30, verify=certifi.where(),
                )

                if response.status_code == 201:
                    wh = response.json().get('webhook', {})
                    vals = {
                        'name': _TOPIC_LABEL.get(topic, topic),
                        'shopify_instance_id': instance.id,
                        'topic': topic,
                        'webhook_url': webhook_url,
                        'shopify_webhook_id': str(wh['id']),
                        'created_at': fields.Datetime.now(),
                        'active': True,
                    }
                    if existing:
                        existing.write(vals)
                    else:
                        self.create(vals)
                    results['created'] += 1
                    _logger.info(f'[register_all_webhooks] Registered {topic} → id={wh["id"]}')
                else:
                    err = f"{topic}: HTTP {response.status_code}"
                    results['errors'].append(err)
                    _logger.warning(f'[register_all_webhooks] {err} — {response.text[:200]}')

            except Exception as e:
                err = f"{topic}: {str(e)}"
                results['errors'].append(err)
                _logger.error(f'[register_all_webhooks] Exception for {topic}: {e}')

        msg = _('Registered: %s | Already existed: %s') % (results['created'], results['skipped'])
        if results['errors']:
            msg += _(' | Errors (%s): %s') % (len(results['errors']), ', '.join(results['errors'][:3]))

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Webhook Registration Complete'),
                'message': msg,
                'type': 'success' if not results['errors'] else 'warning',
            },
        }

    @api.model
    def delete_all_webhooks(self, instance_id):
        """Remove all registered webhooks for a Shopify instance."""
        instance = self.env['shopify.instance'].browse(instance_id)
        webhooks = self.search([
            ('shopify_instance_id', '=', instance.id),
            ('shopify_webhook_id', '!=', False),
        ])
        deleted = 0
        for wh in webhooks:
            try:
                url = f"{instance._get_base_url()}/webhooks/{wh.shopify_webhook_id}.json"
                requests.delete(url, headers=instance._get_headers(), timeout=30, verify=certifi.where())
                deleted += 1
            except Exception as e:
                _logger.warning(f'[delete_all_webhooks] Could not delete {wh.topic}: {e}')
        webhooks.write({'shopify_webhook_id': False})
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Webhooks Removed'),
                'message': _('%s webhooks removed from Shopify') % deleted,
                'type': 'success',
            },
        }

    @api.model
    def action_register_all(self):
        """Register all default webhooks for ALL active Shopify instances.

        Invoked from the list view Action menu.
        """
        instances = self.env['shopify.instance'].search([('active', '=', True)])
        if not instances:
            raise UserError(_('No active Shopify instances found.'))
        for instance in instances:
            self.register_all_webhooks(instance.id)
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Webhook Registration Complete'),
                'message': _('All webhooks registered for %s instance(s). Refresh the list.') % len(instances),
                'type': 'success',
            },
        }

    @api.model
    def action_delete_all(self):
        """Remove all registered webhooks for ALL active Shopify instances."""
        instances = self.env['shopify.instance'].search([('active', '=', True)])
        if not instances:
            raise UserError(_('No active Shopify instances found.'))
        for instance in instances:
            self.delete_all_webhooks(instance.id)
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Webhooks Deleted'),
                'message': _('All webhooks removed for %s instance(s).') % len(instances),
                'type': 'success',
            },
        }

    def button_register_all_for_instance(self):
        """Register all webhooks for this record's Shopify instance.

        Called from the form view header button.
        """
        self.ensure_one()
        return self.register_all_webhooks(self.shopify_instance_id.id)

    # ──────────────────────────────────────────────────────────────────────
    # Main dispatcher
    # ──────────────────────────────────────────────────────────────────────

    @api.model
    def process_webhook(self, topic, data, shopify_domain):
        """Route an incoming webhook payload to the appropriate handler."""
        instance = None
        try:
            _logger.info(
                f'[process_webhook] ▶ Looking up Shopify instance for '
                f'domain={shopify_domain!r}'
            )
            instance = self.env['shopify.instance'].search([
                '|',
                ('shop_url', 'ilike', shopify_domain.replace('.myshopify.com', '')),
                ('shop_url', '=', shopify_domain),
            ], limit=1)

            if not instance:
                _logger.warning(
                    f'[process_webhook] ✘ No Shopify instance found for '
                    f'domain={shopify_domain!r} — cannot process topic={topic!r}'
                )
                return False

            _logger.info(
                f'[process_webhook] ✔ Instance found: "{instance.name}" '
                f'(id={instance.id}) for domain={shopify_domain!r}'
            )

            # Log to sync log
            try:
                self.env['shopify.sync.log'].log_sync(
                    instance_id=instance.id,
                    sync_type='webhook',
                    direction='import',
                    status='success',
                    message=f'Received webhook: {topic}',
                )
            except Exception:
                pass  # Non-critical

            # Route by topic prefix
            if topic.startswith('orders/'):
                _logger.info(f'[process_webhook] ▶ Routing topic={topic!r} → _handle_order')
                self._handle_order(topic, data, instance)
            elif topic.startswith('draft_orders/'):
                _logger.info(f'[process_webhook] ▶ Routing topic={topic!r} → _handle_draft_order')
                self._handle_draft_order(topic, data, instance)
            elif topic.startswith('customers/'):
                _logger.info(f'[process_webhook] ▶ Routing topic={topic!r} → _handle_customer')
                self._handle_customer(topic, data, instance)
            elif topic.startswith('products/'):
                _logger.info(f'[process_webhook] ▶ Routing topic={topic!r} → _handle_product')
                self._handle_product(topic, data, instance)
            elif topic.startswith('collections/'):
                _logger.info(f'[process_webhook] ▶ Routing topic={topic!r} → _handle_collection')
                self._handle_collection(topic, data, instance)
            elif topic.startswith('inventory_levels/'):
                _logger.info(f'[process_webhook] ▶ Routing topic={topic!r} → _handle_inventory_level')
                self._handle_inventory_level(topic, data, instance)
            elif topic.startswith('inventory_items/'):
                _logger.info(f'[process_webhook] ▶ Routing topic={topic!r} → _handle_inventory_item')
                self._handle_inventory_item(topic, data, instance)
            elif topic.startswith('fulfillments/'):
                _logger.info(f'[process_webhook] ▶ Routing topic={topic!r} → _handle_fulfillment')
                self._handle_fulfillment(topic, data, instance)
            elif topic.startswith('fulfillment_orders/'):
                _logger.info(f'[process_webhook] ▶ Routing topic={topic!r} → _handle_fulfillment_order')
                self._handle_fulfillment_order(topic, data, instance)
            elif topic.startswith('refunds/'):
                _logger.info(f'[process_webhook] ▶ Routing topic={topic!r} → _handle_refund')
                self._handle_refund(topic, data, instance)
            elif topic.startswith('checkouts/'):
                _logger.info(f'[process_webhook] ▶ Routing topic={topic!r} → _handle_checkout')
                self._handle_checkout(topic, data, instance)
            elif topic.startswith('carts/'):
                _logger.info(f'[process_webhook] ▶ Routing topic={topic!r} → _handle_cart')
                self._handle_cart(topic, data, instance)
            elif topic.startswith('locations/'):
                _logger.info(f'[process_webhook] ▶ Routing topic={topic!r} → _handle_location')
                self._handle_location(topic, data, instance)
            elif topic.startswith('order_transactions/'):
                _logger.info(f'[process_webhook] ▶ Routing topic={topic!r} → _handle_order_transaction')
                self._handle_order_transaction(topic, data, instance)
            elif topic.startswith('themes/'):
                _logger.info(f'[process_webhook] ▶ Routing topic={topic!r} → _handle_theme')
                self._handle_theme(topic, data, instance)
            elif topic == 'shop/update':
                _logger.info(f'[process_webhook] ▶ Routing topic={topic!r} → _handle_shop_update')
                self._handle_shop_update(data, instance)
            elif topic == 'app/uninstalled':
                _logger.warning(f'[process_webhook] ▶ Routing topic={topic!r} → _handle_app_uninstalled')
                self._handle_app_uninstalled(data, instance)
            elif topic in ('customers/data_request', 'customers/redact', 'shop/redact'):
                _logger.info(f'[process_webhook] ▶ Routing topic={topic!r} → _handle_gdpr')
                self._handle_gdpr(topic, data, instance)
            else:
                _logger.warning(
                    f'[process_webhook] ✘ Unhandled topic={topic!r} — '
                    f'no handler registered, logged only'
                )

            _logger.info(
                f'[process_webhook] ✔ Handler completed for topic={topic!r} '
                f'instance="{instance.name}"'
            )
            return True

        except Exception as e:
            _logger.error(
                f'[process_webhook] ✘ Handler raised exception for topic={topic!r}: {e}',
                exc_info=True
            )
            try:
                self.env['shopify.sync.log'].log_sync(
                    instance_id=instance.id if instance else 0,
                    sync_type='webhook',
                    direction='import',
                    status='error',
                    message=f'Webhook {topic} error: {str(e)}',
                )
            except Exception:
                pass
            return False

    # ──────────────────────────────────────────────────────────────────────
    # Handlers
    # ──────────────────────────────────────────────────────────────────────

    def _handle_order(self, topic, data, instance):
        """Handle all orders/* webhooks."""
        order_model = self.env['sale.order']
        shopify_order_id = str(data.get('id', ''))
        if not shopify_order_id:
            _logger.warning(f'[_handle_order] ✘ {topic} — no order id in payload, skipping')
            return

        _logger.info(
            f'[_handle_order] ▶ {topic} — searching for order '
            f'shopify_id={shopify_order_id} in instance="{instance.name}"'
        )
        existing = order_model.search([
            ('shopify_order_id', '=', shopify_order_id),
            ('shopify_instance_id', '=', instance.id),
        ], limit=1)

        if existing:
            _logger.info(
                f'[_handle_order] ✔ Found existing order {existing.name} '
                f'(shopify_id={shopify_order_id}  state={existing.state})'
            )
        else:
            _logger.info(
                f'[_handle_order] Order shopify_id={shopify_order_id} '
                f'not found in Odoo (will create if applicable)'
            )

        if topic == 'orders/delete':
            if existing:
                _logger.info(
                    f'[_handle_order] ▶ orders/delete — archiving order '
                    f'{existing.name} (shopify_id={shopify_order_id})'
                )
                existing.with_context(shopify_sync_skip=True).write({'active': False})
                _logger.info(
                    f'[_handle_order] ✔ orders/delete — order {existing.name} archived successfully'
                )
            else:
                _logger.warning(
                    f'[_handle_order] orders/delete — order shopify_id={shopify_order_id} '
                    f'not found in Odoo, nothing to archive'
                )
            return

        if topic in ('orders/create', 'orders/updated', 'orders/edited'):
            order_vals = order_model._prepare_order_vals(data, instance)
            ctx = {'shopify_sync_skip': True}
            if existing:
                if existing.state in ('draft', 'sent'):
                    _logger.info(
                        f'[_handle_order] ▶ {topic} — updating order {existing.name} '
                        f'(state={existing.state})'
                    )
                    existing.with_context(**ctx).write(order_vals)
                    _logger.info(
                        f'[_handle_order] ✔ {topic} — order {existing.name} updated successfully'
                    )
                else:
                    _logger.warning(
                        f'[_handle_order] {topic} — order {existing.name} is in state '
                        f'"{existing.state}", skipping write (only draft/sent are editable)'
                    )
            else:
                _logger.info(
                    f'[_handle_order] ▶ {topic} — creating new order for '
                    f'shopify_id={shopify_order_id}'
                )
                new_order = order_model.with_context(**ctx).create(order_vals)
                line_count = len(data.get('line_items', []))
                _logger.info(
                    f'[_handle_order] ▶ {topic} — creating {line_count} order line(s) '
                    f'for new order {new_order.name}'
                )
                order_model._create_order_lines(new_order, data.get('line_items', []))
                _logger.info(
                    f'[_handle_order] ✔ {topic} — order {new_order.name} created '
                    f'with {line_count} line(s) (shopify_id={shopify_order_id})'
                )

        elif topic == 'orders/cancelled':
            if existing:
                cancel_reason = data.get('cancel_reason', 'other')
                _logger.info(
                    f'[_handle_order] ▶ orders/cancelled — updating order {existing.name} '
                    f'cancel_reason={cancel_reason!r}'
                )
                existing.with_context(shopify_sync_skip=True).write({
                    'shopify_financial_status': 'voided',
                    'shopify_cancelled_at': fields.Datetime.now(),
                    'shopify_cancel_reason': cancel_reason,
                })
                _logger.info(
                    f'[_handle_order] ✔ orders/cancelled — order {existing.name} '
                    f'marked as cancelled (reason={cancel_reason!r})'
                )
            else:
                _logger.warning(
                    f'[_handle_order] orders/cancelled — order shopify_id={shopify_order_id} '
                    f'not found in Odoo, cannot mark as cancelled'
                )

        elif topic == 'orders/fulfilled':
            if existing:
                _logger.info(
                    f'[_handle_order] ▶ orders/fulfilled — updating order {existing.name} '
                    f'fulfillment_status → fulfilled'
                )
                existing.with_context(shopify_sync_skip=True).write({
                    'shopify_fulfillment_status': 'fulfilled',
                })
                _logger.info(
                    f'[_handle_order] ✔ orders/fulfilled — order {existing.name} '
                    f'fulfillment_status set to "fulfilled"'
                )
            else:
                _logger.warning(
                    f'[_handle_order] orders/fulfilled — order shopify_id={shopify_order_id} '
                    f'not found in Odoo'
                )

        elif topic == 'orders/partially_fulfilled':
            if existing:
                _logger.info(
                    f'[_handle_order] ▶ orders/partially_fulfilled — updating order '
                    f'{existing.name} fulfillment_status → partial'
                )
                existing.with_context(shopify_sync_skip=True).write({
                    'shopify_fulfillment_status': 'partial',
                })
                _logger.info(
                    f'[_handle_order] ✔ orders/partially_fulfilled — order {existing.name} '
                    f'fulfillment_status set to "partial"'
                )
            else:
                _logger.warning(
                    f'[_handle_order] orders/partially_fulfilled — order '
                    f'shopify_id={shopify_order_id} not found in Odoo'
                )

        elif topic == 'orders/paid':
            if existing:
                _logger.info(
                    f'[_handle_order] ▶ orders/paid — updating order {existing.name} '
                    f'financial_status → paid'
                )
                existing.with_context(shopify_sync_skip=True).write({
                    'shopify_financial_status': 'paid',
                })
                _logger.info(
                    f'[_handle_order] ✔ orders/paid — order {existing.name} '
                    f'financial_status set to "paid"'
                )
            else:
                _logger.warning(
                    f'[_handle_order] orders/paid — order shopify_id={shopify_order_id} '
                    f'not found in Odoo'
                )

    def _handle_draft_order(self, topic, data, instance):
        """Handle all draft_orders/* webhooks."""
        order_model = self.env['sale.order']
        shopify_order_id = str(data.get('id', ''))
        if not shopify_order_id:
            _logger.warning(f'[_handle_draft_order] ✘ {topic} — no order id in payload, skipping')
            return

        _logger.info(
            f'[_handle_draft_order] ▶ {topic} — searching for draft order '
            f'shopify_id={shopify_order_id} in instance="{instance.name}"'
        )
        existing = order_model.search([
            ('shopify_order_id', '=', shopify_order_id),
            ('shopify_instance_id', '=', instance.id),
        ], limit=1)

        if existing:
            _logger.info(
                f'[_handle_draft_order] ✔ Found existing order {existing.name} '
                f'(shopify_id={shopify_order_id}  state={existing.state})'
            )
        else:
            _logger.info(
                f'[_handle_draft_order] Draft order shopify_id={shopify_order_id} '
                f'not found in Odoo'
            )

        if topic == 'draft_orders/delete':
            if existing:
                _logger.info(
                    f'[_handle_draft_order] ▶ draft_orders/delete — archiving '
                    f'order {existing.name}'
                )
                existing.with_context(shopify_sync_skip=True).write({'active': False})
                _logger.info(
                    f'[_handle_draft_order] ✔ draft_orders/delete — order '
                    f'{existing.name} archived successfully'
                )
            else:
                _logger.warning(
                    f'[_handle_draft_order] draft_orders/delete — shopify_id='
                    f'{shopify_order_id} not found in Odoo, nothing to archive'
                )
            return

        if topic in ('draft_orders/create', 'draft_orders/update'):
            order_vals = order_model._prepare_order_vals(data, instance)
            ctx = {'shopify_sync_skip': True}
            if existing:
                if existing.state in ('draft', 'sent'):
                    _logger.info(
                        f'[_handle_draft_order] ▶ {topic} — updating order '
                        f'{existing.name} (state={existing.state})'
                    )
                    existing.with_context(**ctx).write(order_vals)
                    _logger.info(
                        f'[_handle_draft_order] ✔ {topic} — order {existing.name} '
                        f'updated successfully'
                    )
                else:
                    _logger.warning(
                        f'[_handle_draft_order] {topic} — order {existing.name} '
                        f'is in state "{existing.state}", skipping write'
                    )
            else:
                _logger.info(
                    f'[_handle_draft_order] ▶ {topic} — creating new draft order '
                    f'for shopify_id={shopify_order_id}'
                )
                new_order = order_model.with_context(**ctx).create(order_vals)
                line_count = len(data.get('line_items', []))
                order_model._create_order_lines(new_order, data.get('line_items', []))
                _logger.info(
                    f'[_handle_draft_order] ✔ {topic} — draft order {new_order.name} '
                    f'created with {line_count} line(s) (shopify_id={shopify_order_id})'
                )

    def _handle_customer(self, topic, data, instance):
        """Handle all customers/* webhooks."""
        partner_model = self.env['res.partner']
        shopify_customer_id = str(data.get('id', ''))

        # GDPR mandatory webhooks — just log
        if topic in ('customers/data_request', 'customers/redact'):
            _logger.info(
                f'[_handle_customer] GDPR request topic={topic!r} '
                f'for shopify_customer_id={shopify_customer_id}'
            )
            return

        _logger.info(
            f'[_handle_customer] ▶ {topic} — searching for customer '
            f'shopify_id={shopify_customer_id} in instance="{instance.name}"'
        )
        existing = partner_model.search([
            ('shopify_customer_id', '=', shopify_customer_id),
            ('shopify_instance_id', '=', instance.id),
        ], limit=1)

        if existing:
            _logger.info(
                f'[_handle_customer] ✔ Found existing customer: "{existing.name}" '
                f'(shopify_id={shopify_customer_id})'
            )
        else:
            _logger.info(
                f'[_handle_customer] Customer shopify_id={shopify_customer_id} '
                f'not found in Odoo'
            )

        if topic == 'customers/delete':
            if existing:
                _logger.info(
                    f'[_handle_customer] ▶ customers/delete — archiving '
                    f'customer "{existing.name}"'
                )
                existing.with_context(shopify_sync_skip=True).write({'active': False})
                _logger.info(
                    f'[_handle_customer] ✔ customers/delete — customer '
                    f'"{existing.name}" (shopify_id={shopify_customer_id}) archived'
                )
            else:
                _logger.warning(
                    f'[_handle_customer] customers/delete — shopify_id='
                    f'{shopify_customer_id} not found in Odoo, nothing to archive'
                )
            return

        if topic == 'customers/merge':
            source_id = str(data.get('source_customer_id', ''))
            _logger.info(
                f'[_handle_customer] ▶ customers/merge — source_customer_id={source_id} '
                f'→ resulting_customer_id={shopify_customer_id}'
            )
            if source_id:
                source = partner_model.search([
                    ('shopify_customer_id', '=', source_id),
                    ('shopify_instance_id', '=', instance.id),
                ], limit=1)
                if source:
                    source.with_context(shopify_sync_skip=True).write({'active': False})
                    _logger.info(
                        f'[_handle_customer] ✔ customers/merge — source customer '
                        f'"{source.name}" (shopify_id={source_id}) archived'
                    )
                else:
                    _logger.warning(
                        f'[_handle_customer] customers/merge — source shopify_id='
                        f'{source_id} not found in Odoo'
                    )
            return

        if topic in ('customers/create', 'customers/update', 'customers/enable', 'customers/disable'):
            if not shopify_customer_id:
                _logger.warning(
                    f'[_handle_customer] ✘ {topic} — no customer id in payload, skipping'
                )
                return
            partner_vals = partner_model._prepare_customer_vals(data, instance)
            ctx = {'shopify_sync_skip': True}
            if existing:
                _logger.info(
                    f'[_handle_customer] ▶ {topic} — updating customer "{existing.name}"'
                )
                existing.with_context(**ctx).write(partner_vals)
                _logger.info(
                    f'[_handle_customer] ✔ {topic} — customer "{existing.name}" '
                    f'(shopify_id={shopify_customer_id}) updated successfully'
                )
            elif topic == 'customers/create':
                _logger.info(
                    f'[_handle_customer] ▶ customers/create — creating new customer '
                    f'shopify_id={shopify_customer_id}'
                )
                new_partner = partner_model.with_context(**ctx).create(partner_vals)
                _logger.info(
                    f'[_handle_customer] ✔ customers/create — customer "{new_partner.name}" '
                    f'created (shopify_id={shopify_customer_id})'
                )
            else:
                _logger.warning(
                    f'[_handle_customer] {topic} — customer shopify_id={shopify_customer_id} '
                    f'not found in Odoo, cannot update'
                )

    def _handle_product(self, topic, data, instance):
        """Handle all products/* webhooks."""
        product_model = self.env['product.template']
        shopify_product_id = str(data.get('id', ''))
        if not shopify_product_id:
            _logger.warning(f'[_handle_product] ✘ {topic} — no product id in payload, skipping')
            return

        _logger.info(
            f'[_handle_product] ▶ {topic} — searching for product '
            f'shopify_id={shopify_product_id} in instance="{instance.name}"'
        )
        existing = product_model.search([
            ('shopify_product_id', '=', shopify_product_id),
            ('shopify_instance_id', '=', instance.id),
        ], limit=1)

        if existing:
            _logger.info(
                f'[_handle_product] ✔ Found existing product: "{existing.name}" '
                f'(shopify_id={shopify_product_id})'
            )
        else:
            _logger.info(
                f'[_handle_product] Product shopify_id={shopify_product_id} '
                f'not found in Odoo'
            )

        if topic == 'products/delete':
            if existing:
                _logger.info(
                    f'[_handle_product] ▶ products/delete — archiving '
                    f'product "{existing.name}"'
                )
                existing.with_context(shopify_sync_skip=True).write({'active': False})
                _logger.info(
                    f'[_handle_product] ✔ products/delete — product "{existing.name}" '
                    f'(shopify_id={shopify_product_id}) archived successfully'
                )
            else:
                _logger.warning(
                    f'[_handle_product] products/delete — shopify_id={shopify_product_id} '
                    f'not found in Odoo, nothing to archive'
                )
            return

        if topic in ('products/create', 'products/update'):
            try:
                product_vals = product_model._prepare_product_vals(data, instance)
                ctx = {'shopify_sync_skip': True}
                if existing:
                    _logger.info(
                        f'[_handle_product] ▶ {topic} — updating product "{existing.name}"'
                    )
                    existing.with_context(**ctx).write(product_vals)
                    _logger.info(
                        f'[_handle_product] ✔ {topic} — product "{existing.name}" '
                        f'(shopify_id={shopify_product_id}) updated successfully'
                    )
                else:
                    _logger.info(
                        f'[_handle_product] ▶ {topic} — creating new product '
                        f'shopify_id={shopify_product_id}'
                    )
                    new_product = product_model.with_context(**ctx).create(product_vals)
                    _logger.info(
                        f'[_handle_product] ✔ {topic} — product "{new_product.name}" '
                        f'created (shopify_id={shopify_product_id})'
                    )
            except Exception as e:
                _logger.error(
                    f'[_handle_product] ✘ {topic} — FAILED for '
                    f'shopify_id={shopify_product_id}: {e}',
                    exc_info=True
                )

    def _handle_collection(self, topic, data, instance):
        """Handle all collections/* webhooks."""
        collection_model = self.env.get('shopify.collection')
        if not collection_model:
            _logger.warning(
                f'[_handle_collection] ✘ {topic} — shopify.collection model not available, '
                f'skipping (module may not be installed)'
            )
            return

        shopify_id = str(data.get('id', ''))
        collection_title = data.get('title', '(no title)')
        if not shopify_id:
            _logger.warning(
                f'[_handle_collection] ✘ {topic} — no collection id in payload, skipping'
            )
            return

        _logger.info(
            f'[_handle_collection] ▶ {topic} — searching for collection '
            f'shopify_id={shopify_id} title="{collection_title}" '
            f'in instance="{instance.name}"'
        )
        existing = collection_model.search([
            ('shopify_collection_id', '=', shopify_id),
            ('shopify_instance_id', '=', instance.id),
        ], limit=1)

        if existing:
            _logger.info(
                f'[_handle_collection] ✔ Found existing collection: "{existing.name}" '
                f'(shopify_id={shopify_id})'
            )
        else:
            _logger.info(
                f'[_handle_collection] Collection shopify_id={shopify_id} '
                f'not found in Odoo'
            )

        if topic == 'collections/delete':
            if existing:
                _logger.info(
                    f'[_handle_collection] ▶ collections/delete — archiving '
                    f'collection "{existing.name}"'
                )
                existing.with_context(shopify_sync_skip=True).write({'active': False})
                _logger.info(
                    f'[_handle_collection] ✔ collections/delete — collection '
                    f'"{existing.name}" (shopify_id={shopify_id}) archived'
                )
            else:
                _logger.warning(
                    f'[_handle_collection] collections/delete — shopify_id={shopify_id} '
                    f'not found in Odoo, nothing to archive'
                )
            return

        vals = {
            'name': collection_title,
            'shopify_collection_id': shopify_id,
            'shopify_instance_id': instance.id,
        }
        if existing:
            _logger.info(
                f'[_handle_collection] ▶ {topic} — updating collection "{existing.name}"'
            )
            existing.with_context(shopify_sync_skip=True).write(vals)
            _logger.info(
                f'[_handle_collection] ✔ {topic} — collection "{existing.name}" '
                f'(shopify_id={shopify_id}) updated successfully'
            )
        elif topic == 'collections/create':
            _logger.info(
                f'[_handle_collection] ▶ collections/create — creating new collection '
                f'"{collection_title}" (shopify_id={shopify_id})'
            )
            new_col = collection_model.with_context(shopify_sync_skip=True).create(vals)
            _logger.info(
                f'[_handle_collection] ✔ collections/create — collection "{new_col.name}" '
                f'created (shopify_id={shopify_id})'
            )

    def _handle_inventory_level(self, topic, data, instance):
        """Handle all inventory_levels/* webhooks."""
        inventory_item_id = data.get('inventory_item_id')
        location_id = data.get('location_id')
        available = data.get('available')

        _logger.info(
            f'[_handle_inventory_level] ▶ {topic} — '
            f'inventory_item_id={inventory_item_id}  '
            f'location_id={location_id}  available={available}'
        )

        if topic == 'inventory_levels/update' and inventory_item_id and available is not None:
            _logger.info(
                f'[_handle_inventory_level] ▶ Searching for product variant '
                f'with shopify_inventory_item_id={inventory_item_id}'
            )
            variant = self.env['product.product'].search([
                ('shopify_inventory_item_id', '=', str(inventory_item_id)),
            ], limit=1)
            if variant:
                _logger.info(
                    f'[_handle_inventory_level] ✔ Found variant: "{variant.display_name}" — '
                    f'Shopify reports available={available} at location_id={location_id} '
                    f'(stock adjustment is log-only; uncomment quant update to enable)'
                )
                # Optionally update qty_available via stock quant — only if you manage stock in Odoo
                # This is left as a log-only update to avoid unintended stock adjustments.
                # Uncomment below to enable automatic stock adjustment:
                # warehouse = self.env['stock.warehouse'].search([], limit=1)
                # if warehouse:
                #     quant = self.env['stock.quant'].search([
                #         ('product_id', '=', variant.id),
                #         ('location_id', '=', warehouse.lot_stock_id.id),
                #     ], limit=1)
                #     if quant:
                #         quant.with_context(shopify_sync_skip=True).write({'quantity': available})
            else:
                _logger.warning(
                    f'[_handle_inventory_level] inventory_levels/update — no variant found '
                    f'with shopify_inventory_item_id={inventory_item_id} '
                    f'(variant may not be synced yet)'
                )
        else:
            _logger.info(
                f'[_handle_inventory_level] ✔ {topic} — logged only '
                f'(no write action for this sub-topic)'
            )

    def _handle_inventory_item(self, topic, data, instance):
        """Handle all inventory_items/* webhooks."""
        shopify_item_id = str(data.get('id', ''))
        sku = data.get('sku', '')
        _logger.info(
            f'[_handle_inventory_item] ▶ {topic} — '
            f'inventory_item_id={shopify_item_id}  sku={sku!r}'
        )
        # Store inventory_item_id on product variant for future lookups
        if topic in ('inventory_items/create', 'inventory_items/update') and sku:
            _logger.info(
                f'[_handle_inventory_item] ▶ Searching for product variant '
                f'with SKU={sku!r}'
            )
            variant = self.env['product.product'].search([
                ('default_code', '=', sku),
            ], limit=1)
            if variant and hasattr(variant, 'shopify_inventory_item_id'):
                _logger.info(
                    f'[_handle_inventory_item] ▶ Found variant "{variant.display_name}" — '
                    f'writing shopify_inventory_item_id={shopify_item_id}'
                )
                variant.with_context(shopify_sync_skip=True).write({
                    'shopify_inventory_item_id': shopify_item_id,
                })
                _logger.info(
                    f'[_handle_inventory_item] ✔ {topic} — variant "{variant.display_name}" '
                    f'shopify_inventory_item_id updated to {shopify_item_id}'
                )
            elif variant and not hasattr(variant, 'shopify_inventory_item_id'):
                _logger.warning(
                    f'[_handle_inventory_item] variant with SKU={sku!r} found but '
                    f'shopify_inventory_item_id field not available on the model'
                )
            else:
                _logger.warning(
                    f'[_handle_inventory_item] ✘ {topic} — no product variant found '
                    f'with SKU={sku!r} (variant may not be synced yet)'
                )
        else:
            _logger.info(
                f'[_handle_inventory_item] ✔ {topic} — logged only '
                f'(sku={sku!r}, no write action)'
            )

    def _handle_fulfillment(self, topic, data, instance):
        """Handle all fulfillments/* webhooks."""
        order_id = str(data.get('order_id', ''))
        fulfillment_id = str(data.get('id', ''))
        status = data.get('status', '')
        tracking_numbers = data.get('tracking_numbers', [])

        _logger.info(
            f'[_handle_fulfillment] ▶ {topic} — '
            f'fulfillment_id={fulfillment_id}  order_id={order_id}  '
            f'status={status!r}  tracking={tracking_numbers}'
        )

        if not order_id:
            _logger.warning(
                f'[_handle_fulfillment] ✘ {topic} — no order_id in payload, skipping'
            )
            return

        _logger.info(
            f'[_handle_fulfillment] ▶ Searching for order shopify_id={order_id} '
            f'in instance="{instance.name}"'
        )
        order = self.env['sale.order'].search([
            ('shopify_order_id', '=', order_id),
            ('shopify_instance_id', '=', instance.id),
        ], limit=1)

        if not order:
            _logger.warning(
                f'[_handle_fulfillment] ✘ {topic} — order shopify_id={order_id} '
                f'not found in Odoo, cannot update fulfillment status'
            )
            return

        _logger.info(
            f'[_handle_fulfillment] ✔ Found order {order.name} — '
            f'updating fulfillment_id={fulfillment_id} status={status!r}'
        )
        fulfillment_status = 'fulfilled' if status == 'success' else 'partial'
        order.with_context(shopify_sync_skip=True).write({
            'shopify_fulfillment_id': fulfillment_id,
            'shopify_fulfillment_status': fulfillment_status,
        })
        _logger.info(
            f'[_handle_fulfillment] ✔ {topic} — order {order.name} '
            f'fulfillment_status set to "{fulfillment_status}" '
            f'(fulfillment_id={fulfillment_id})'
        )

    def _handle_fulfillment_order(self, topic, data, instance):
        """Handle all fulfillment_orders/* webhooks — log-only."""
        fo_id = data.get('id', '')
        order_id = data.get('order_id', '')
        assigned_location = data.get('assigned_location', {}).get('name', '')
        _logger.info(
            f'[_handle_fulfillment_order] ▶ {topic} — '
            f'fulfillment_order_id={fo_id}  order_id={order_id}  '
            f'assigned_location={assigned_location!r}'
        )
        # For cancelled fulfillment orders, update parent sale order status
        if topic == 'fulfillment_orders/cancelled' and order_id:
            _logger.info(
                f'[_handle_fulfillment_order] ▶ fulfillment_orders/cancelled — '
                f'searching for order shopify_id={order_id}'
            )
            order = self.env['sale.order'].search([
                ('shopify_order_id', '=', str(order_id)),
                ('shopify_instance_id', '=', instance.id),
            ], limit=1)
            if order:
                _logger.info(
                    f'[_handle_fulfillment_order] ▶ fulfillment_orders/cancelled — '
                    f'updating order {order.name} fulfillment_status → unfulfilled'
                )
                order.with_context(shopify_sync_skip=True).write({
                    'shopify_fulfillment_status': 'unfulfilled',
                })
                _logger.info(
                    f'[_handle_fulfillment_order] ✔ fulfillment_orders/cancelled — '
                    f'order {order.name} fulfillment_status set to "unfulfilled"'
                )
            else:
                _logger.warning(
                    f'[_handle_fulfillment_order] fulfillment_orders/cancelled — '
                    f'order shopify_id={order_id} not found in Odoo'
                )
        else:
            _logger.info(
                f'[_handle_fulfillment_order] ✔ {topic} — logged only '
                f'(no write action for this sub-topic)'
            )

    def _handle_refund(self, topic, data, instance):
        """Handle refunds/create webhook."""
        order_id = str(data.get('order_id', ''))
        refund_id = str(data.get('id', ''))
        txn_count = len(data.get('transactions', []))

        _logger.info(
            f'[_handle_refund] ▶ {topic} — '
            f'refund_id={refund_id}  order_id={order_id}  '
            f'transactions_in_payload={txn_count}'
        )

        if not order_id:
            _logger.warning(
                f'[_handle_refund] ✘ {topic} — no order_id in payload, skipping'
            )
            return

        _logger.info(
            f'[_handle_refund] ▶ Searching for order shopify_id={order_id} '
            f'in instance="{instance.name}"'
        )
        order = self.env['sale.order'].search([
            ('shopify_order_id', '=', order_id),
            ('shopify_instance_id', '=', instance.id),
        ], limit=1)

        if not order:
            _logger.warning(
                f'[_handle_refund] ✘ {topic} — order shopify_id={order_id} '
                f'not found in Odoo, cannot record refund'
            )
            return

        _logger.info(
            f'[_handle_refund] ✔ Found order {order.name} '
            f'(amount_total={order.amount_total}) — computing refund amount'
        )

        # Determine new financial status from the refund data
        transactions = data.get('transactions', [])
        refunded_amount = sum(
            float(t.get('amount', 0)) for t in transactions
            if t.get('kind') == 'refund' and t.get('status') == 'success'
        )
        _logger.info(
            f'[_handle_refund] ▶ Computed refunded_amount={refunded_amount} '
            f'from {len(transactions)} transaction(s)'
        )

        if refunded_amount >= order.amount_total:
            new_status = 'refunded'
        elif refunded_amount > 0:
            new_status = 'partially_refunded'
        else:
            new_status = order.shopify_financial_status
            _logger.info(
                f'[_handle_refund] refunded_amount=0 — keeping existing '
                f'financial_status={new_status!r}'
            )

        _logger.info(
            f'[_handle_refund] ▶ Updating order {order.name} — '
            f'refund_id={refund_id}  financial_status → {new_status!r}'
        )
        order.with_context(shopify_sync_skip=True).write({
            'shopify_refund_id': refund_id,
            'shopify_financial_status': new_status,
        })
        _logger.info(
            f'[_handle_refund] ✔ {topic} — order {order.name} updated: '
            f'financial_status="{new_status}"  refunded_amount={refunded_amount}'
        )

        # Requirement #13: create a credit note in Odoo for the refund.
        if refunded_amount > 0:
            order.with_context(shopify_sync_skip=True).create_credit_note_from_shopify_refund(
                refunded_amount, refund_id)

    def _handle_checkout(self, topic, data, instance):
        """Handle all checkouts/* webhooks — logged only (extend as needed)."""
        token = data.get('token', '')
        email = data.get('email', '')
        total_price = data.get('total_price', '')
        _logger.info(
            f'[_handle_checkout] ✔ {topic} — token={token!r}  '
            f'email={email!r}  total_price={total_price} (logged only)'
        )

    def _handle_cart(self, topic, data, instance):
        """Handle all carts/* webhooks — logged only (extend as needed)."""
        cart_token = data.get('token', '')
        item_count = len(data.get('line_items', []))
        _logger.info(
            f'[_handle_cart] ✔ {topic} — token={cart_token!r}  '
            f'line_items={item_count} (logged only)'
        )

    def _handle_location(self, topic, data, instance):
        """Handle all locations/* webhooks."""
        shopify_location_id = str(data.get('id', ''))
        name = data.get('name', '')
        active_flag = data.get('active', True)

        _logger.info(
            f'[_handle_location] ▶ {topic} — '
            f'location_id={shopify_location_id}  name={name!r}  active={active_flag}'
        )

        location_model = self.env.get('shopify.inventory.location')
        if not location_model:
            _logger.warning(
                f'[_handle_location] ✘ {topic} — shopify.inventory.location model '
                f'not available, skipping (module may not be installed)'
            )
            return

        _logger.info(
            f'[_handle_location] ▶ Searching for location '
            f'shopify_id={shopify_location_id} in instance="{instance.name}"'
        )
        existing = location_model.search([
            ('shopify_location_id', '=', shopify_location_id),
            ('shopify_instance_id', '=', instance.id),
        ], limit=1)

        if existing:
            _logger.info(
                f'[_handle_location] ✔ Found existing location: "{existing.name}" '
                f'(shopify_id={shopify_location_id})'
            )
        else:
            _logger.info(
                f'[_handle_location] Location shopify_id={shopify_location_id} '
                f'not found in Odoo'
            )

        if topic == 'locations/delete':
            if existing:
                _logger.info(
                    f'[_handle_location] ▶ locations/delete — archiving '
                    f'location "{existing.name}"'
                )
                existing.with_context(shopify_sync_skip=True).write({'active': False})
                _logger.info(
                    f'[_handle_location] ✔ locations/delete — location "{existing.name}" '
                    f'(shopify_id={shopify_location_id}) archived'
                )
            else:
                _logger.warning(
                    f'[_handle_location] locations/delete — shopify_id={shopify_location_id} '
                    f'not found in Odoo, nothing to archive'
                )
            return

        vals = {
            'name': name,
            'shopify_location_id': shopify_location_id,
            'shopify_instance_id': instance.id,
            'active': active_flag,
        }
        if existing:
            _logger.info(
                f'[_handle_location] ▶ {topic} — updating location "{existing.name}"'
            )
            existing.with_context(shopify_sync_skip=True).write(vals)
            _logger.info(
                f'[_handle_location] ✔ {topic} — location "{existing.name}" '
                f'(shopify_id={shopify_location_id}) updated successfully'
            )
        elif topic in ('locations/create', 'locations/activate'):
            _logger.info(
                f'[_handle_location] ▶ {topic} — creating new location '
                f'"{name}" (shopify_id={shopify_location_id})'
            )
            new_loc = location_model.with_context(shopify_sync_skip=True).create(vals)
            _logger.info(
                f'[_handle_location] ✔ {topic} — location "{new_loc.name}" '
                f'created (shopify_id={shopify_location_id})'
            )

        if topic == 'locations/deactivate' and existing:
            _logger.info(
                f'[_handle_location] ▶ locations/deactivate — deactivating '
                f'location "{existing.name}"'
            )
            existing.with_context(shopify_sync_skip=True).write({'active': False})
            _logger.info(
                f'[_handle_location] ✔ locations/deactivate — location "{existing.name}" '
                f'deactivated (shopify_id={shopify_location_id})'
            )

    def _handle_order_transaction(self, topic, data, instance):
        """Handle order_transactions/create webhook."""
        txn_id = str(data.get('id', ''))
        order_id = str(data.get('order_id', ''))
        kind = data.get('kind', '')
        status = data.get('status', '')
        amount = data.get('amount', '0')
        gateway = data.get('gateway', '')
        currency = data.get('currency', '')

        _logger.info(
            f'[_handle_order_transaction] ▶ {topic} — '
            f'txn_id={txn_id}  order_id={order_id}  '
            f'kind={kind!r}  status={status!r}  '
            f'amount={amount}  gateway={gateway!r}  currency={currency!r}'
        )

        # Upsert the payment transaction record
        txn_model = self.env.get('shopify.payment.transaction')
        if not txn_model:
            _logger.warning(
                f'[_handle_order_transaction] ✘ {topic} — shopify.payment.transaction '
                f'model not available, skipping'
            )
            return

        _logger.info(
            f'[_handle_order_transaction] ▶ Searching for existing transaction '
            f'shopify_txn_id={txn_id} in instance="{instance.name}"'
        )
        existing = txn_model.search([
            ('shopify_transaction_id', '=', txn_id),
            ('shopify_instance_id', '=', instance.id),
        ], limit=1)

        currency_rec = self.env['res.currency'].search([('name', '=', currency)], limit=1)
        if currency and not currency_rec:
            _logger.warning(
                f'[_handle_order_transaction] Currency "{currency}" not found in Odoo'
            )

        vals = {
            'shopify_transaction_id': txn_id,
            'shopify_order_id': order_id,
            'shopify_instance_id': instance.id,
            'kind': kind,
            'status': status,
            'amount': float(amount) if amount else 0.0,
            'gateway': gateway,
            'currency_id': currency_rec.id if currency_rec else False,
        }

        if existing:
            _logger.info(
                f'[_handle_order_transaction] ▶ Updating existing transaction '
                f'shopify_txn_id={txn_id}'
            )
            existing.with_context(shopify_sync_skip=True).write(vals)
            _logger.info(
                f'[_handle_order_transaction] ✔ {topic} — transaction '
                f'txn_id={txn_id} updated (kind={kind!r}  status={status!r})'
            )
        else:
            _logger.info(
                f'[_handle_order_transaction] ▶ Creating new transaction '
                f'shopify_txn_id={txn_id}'
            )
            txn_model.with_context(shopify_sync_skip=True).create(vals)
            _logger.info(
                f'[_handle_order_transaction] ✔ {topic} — transaction '
                f'txn_id={txn_id} created (kind={kind!r}  status={status!r}  '
                f'amount={amount}  gateway={gateway!r})'
            )

        # Also update parent order financial status when it's a successful sale/capture
        if status == 'success' and kind in ('sale', 'capture') and order_id:
            _logger.info(
                f'[_handle_order_transaction] ▶ Successful {kind} transaction — '
                f'searching for order shopify_id={order_id} to mark as paid'
            )
            order = self.env['sale.order'].search([
                ('shopify_order_id', '=', order_id),
                ('shopify_instance_id', '=', instance.id),
            ], limit=1)
            if order:
                order.with_context(shopify_sync_skip=True).write(
                    {'shopify_financial_status': 'paid'}
                )
                _logger.info(
                    f'[_handle_order_transaction] ✔ order {order.name} '
                    f'financial_status set to "paid" via {kind} transaction txn_id={txn_id}'
                )
            else:
                _logger.warning(
                    f'[_handle_order_transaction] order shopify_id={order_id} '
                    f'not found in Odoo — could not update financial_status to paid'
                )

    def _handle_theme(self, topic, data, instance):
        """Handle all themes/* webhooks — logged only."""
        theme_id = data.get('id', '')
        name = data.get('name', '')
        role = data.get('role', '')
        _logger.info(
            f'[_handle_theme] ✔ {topic} — '
            f'theme_id={theme_id}  name={name!r}  role={role!r} (logged only)'
        )

    def _handle_shop_update(self, data, instance):
        """Handle shop/update webhook."""
        shop_name = data.get('name', '')
        shop_email = data.get('email', '')
        _logger.info(
            f'[_handle_shop_update] ▶ shop/update — '
            f'name={shop_name!r}  email={shop_email!r}  '
            f'current_instance_name="{instance.name}"'
        )
        if shop_name and instance.name != shop_name:
            _logger.info(
                f'[_handle_shop_update] ▶ Updating instance name: '
                f'"{instance.name}" → "{shop_name}"'
            )
            instance.with_context(shopify_sync_skip=True).write({'name': shop_name})
            _logger.info(
                f'[_handle_shop_update] ✔ shop/update — instance name updated to "{shop_name}"'
            )
        else:
            _logger.info(
                f'[_handle_shop_update] ✔ shop/update — instance name unchanged "{instance.name}"'
            )

    def _handle_app_uninstalled(self, data, instance):
        """Handle app/uninstalled webhook — deactivate instance."""
        _logger.warning(
            f'[_handle_app_uninstalled] ▶ app/uninstalled — Shopify app removed from store '
            f'"{instance.name}" — deactivating instance'
        )
        instance.with_context(shopify_sync_skip=True).write({'active': False})
        _logger.warning(
            f'[_handle_app_uninstalled] ✔ app/uninstalled — instance '
            f'"{instance.name}" deactivated'
        )

    def _handle_gdpr(self, topic, data, instance):
        """Handle mandatory GDPR webhooks."""
        _logger.info(
            f'[_handle_gdpr] GDPR webhook {topic} received for instance {instance.name}. '
            f'Customer/shop ID: {data.get("customer", {}).get("id") or data.get("shop_id", "")}'
        )
        # These must return HTTP 200 — actual data deletion/export should be
        # handled according to your data-processing agreements.
        # Log the request for compliance audit trail.
        try:
            self.env['shopify.sync.log'].log_sync(
                instance_id=instance.id,
                sync_type='webhook',
                direction='import',
                status='success',
                message=f'GDPR webhook received: {topic} — data: {json.dumps(data)[:500]}',
            )
        except Exception:
            pass
