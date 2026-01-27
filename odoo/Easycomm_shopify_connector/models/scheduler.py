# -*- coding: utf-8 -*-

from odoo import api, fields, models, _
import logging
import threading

_logger = logging.getLogger(__name__)

# Global lock to prevent concurrent cron executions
_sync_lock = threading.Lock()
_running_syncs = {}


class ShopifyScheduler(models.Model):
    _name = 'shopify.scheduler'
    _description = 'Shopify Automated Sync Scheduler'

    name = fields.Char('Schedule Name', required=True)
    shopify_instance_id = fields.Many2one('shopify.instance', string='Shopify Instance', required=True)
    active = fields.Boolean('Active', default=True)

    # Lock status
    is_running = fields.Boolean('Currently Running', default=False, readonly=True)

    @api.model
    def _reset_running_flags(self):
        """Reset all is_running flags on server startup (in case of crash)"""
        try:
            running_schedulers = self.search([('is_running', '=', True)])
            if running_schedulers:
                _logger.info(f'Resetting {len(running_schedulers)} stale running flags from previous session')
                running_schedulers.write({'is_running': False})
        except Exception as e:
            _logger.warning(f'Could not reset running flags: {str(e)}')

    # Sync Configuration
    sync_products = fields.Boolean('Sync Products', default=True)
    sync_customers = fields.Boolean('Sync Customers', default=True)
    sync_orders = fields.Boolean('Sync Orders', default=True)
    sync_inventory = fields.Boolean('Sync Inventory', default=False)
    sync_collections = fields.Boolean('Sync Collections', default=False)
    sync_gift_cards = fields.Boolean('Sync Gift Cards', default=False)
    sync_locations = fields.Boolean('Sync Locations', default=False)
    sync_discounts = fields.Boolean('Sync Discounts', default=False)

    # Schedule Configuration
    interval_number = fields.Integer('Interval Number', default=1)
    interval_type = fields.Selection([
        ('minutes', 'Minutes'),
        ('hours', 'Hours'),
        ('days', 'Days'),
        ('weeks', 'Weeks'),
    ], string='Interval Unit', default='hours')

    # Last Run Info
    last_run = fields.Datetime('Last Run', readonly=True)
    next_run = fields.Datetime('Next Run', compute='_compute_next_run', store=True)
    last_run_status = fields.Selection([
        ('success', 'Success'),
        ('failed', 'Failed'),
        ('partial', 'Partial Success'),
    ], string='Last Run Status', readonly=True)
    last_run_message = fields.Text('Last Run Message', readonly=True)

    # Linked Cron Job
    cron_id = fields.Many2one('ir.cron', string='Scheduled Action', readonly=True, ondelete='cascade')

    @api.depends('last_run', 'interval_number', 'interval_type')
    def _compute_next_run(self):
        from datetime import timedelta
        for record in self:
            if not record.last_run:
                record.next_run = fields.Datetime.now()
            else:
                if record.interval_type == 'minutes':
                    delta = timedelta(minutes=record.interval_number)
                elif record.interval_type == 'hours':
                    delta = timedelta(hours=record.interval_number)
                elif record.interval_type == 'days':
                    delta = timedelta(days=record.interval_number)
                elif record.interval_type == 'weeks':
                    delta = timedelta(weeks=record.interval_number)
                else:
                    delta = timedelta(hours=1)

                record.next_run = record.last_run + delta

    @api.model
    def create(self, vals):
        record = super(ShopifyScheduler, self).create(vals)
        record._create_cron_job()
        return record

    def write(self, vals):
        res = super(ShopifyScheduler, self).write(vals)
        if 'interval_number' in vals or 'interval_type' in vals or 'active' in vals:
            self._update_cron_job()
        return res

    def unlink(self):
        self.mapped('cron_id').unlink()
        return super(ShopifyScheduler, self).unlink()

    def _create_cron_job(self):
        """Create automated cron job for this scheduler"""
        self.ensure_one()

        if self.cron_id:
            return

        cron_vals = {
            'name': f'Shopify Sync: {self.name}',
            'model_id': self.env['ir.model']._get('shopify.scheduler').id,
            'state': 'code',
            'code': f'model.browse({self.id}).run_scheduled_sync()',
            'interval_number': self.interval_number,
            'interval_type': self.interval_type,
            'active': self.active,
        }

        cron = self.env['ir.cron'].sudo().create(cron_vals)
        self.write({'cron_id': cron.id})

    def _update_cron_job(self):
        """Update the cron job when settings change"""
        for record in self:
            if record.cron_id:
                record.cron_id.write({
                    'interval_number': record.interval_number,
                    'interval_type': record.interval_type,
                    'active': record.active,
                })

    def run_scheduled_sync(self):
        """Execute the scheduled sync with lock to prevent concurrent runs"""
        self.ensure_one()

        global _running_syncs

        # Check if this scheduler is already running
        scheduler_key = f'scheduler_{self.id}'

        with _sync_lock:
            if _running_syncs.get(scheduler_key):
                _logger.warning(f'Sync for {self.name} is already running. Skipping this execution.')
                return
            _running_syncs[scheduler_key] = True

        # Also check database flag for distributed environments
        if self.is_running:
            _logger.warning(f'Sync for {self.name} is marked as running in database. Skipping.')
            with _sync_lock:
                _running_syncs[scheduler_key] = False
            return

        # Mark as running
        self.write({'is_running': True})
        self.env.cr.commit()  # Commit immediately so other processes see it

        _logger.info(f'Running scheduled sync for {self.name}')

        errors = []
        success_messages = []

        try:
            # Sync Products
            if self.sync_products:
                try:
                    self.env['product.template'].import_shopify_products(self.shopify_instance_id.id)
                    success_messages.append('Products synced successfully')
                except Exception as e:
                    errors.append(f'Product sync failed: {str(e)}')

            # Sync Customers
            if self.sync_customers:
                try:
                    self.env['res.partner'].import_shopify_customers(self.shopify_instance_id.id)
                    success_messages.append('Customers synced successfully')
                except Exception as e:
                    errors.append(f'Customer sync failed: {str(e)}')

            # Sync Orders
            if self.sync_orders:
                try:
                    self.env['sale.order'].import_shopify_orders(self.shopify_instance_id.id)
                    success_messages.append('Orders synced successfully')
                except Exception as e:
                    errors.append(f'Order sync failed: {str(e)}')

            # Sync Inventory
            if self.sync_inventory:
                try:
                    sync_record = self.env['shopify.inventory.sync'].create({
                        'shopify_instance_id': self.shopify_instance_id.id,
                        'sync_type': 'automatic',
                    })
                    sync_record.sync_inventory_to_shopify()
                    success_messages.append('Inventory synced successfully')
                except Exception as e:
                    errors.append(f'Inventory sync failed: {str(e)}')

            # Sync Collections
            if self.sync_collections:
                try:
                    collection = self.env['shopify.collection'].create({
                        'name': 'Sync Trigger',
                        'shopify_instance_id': self.shopify_instance_id.id,
                    })
                    collection.sync_from_shopify()
                    collection.unlink()
                    success_messages.append('Collections synced successfully')
                except Exception as e:
                    errors.append(f'Collection sync failed: {str(e)}')

            # Sync Gift Cards
            if self.sync_gift_cards:
                try:
                    self.env['shopify.gift.card'].sync_from_shopify(self.shopify_instance_id.id)
                    success_messages.append('Gift cards synced successfully')
                except Exception as e:
                    errors.append(f'Gift card sync failed: {str(e)}')

            # Sync Locations
            if self.sync_locations:
                try:
                    self.env['shopify.inventory.location'].sync_locations_from_shopify(self.shopify_instance_id.id)
                    success_messages.append('Locations synced successfully')
                except Exception as e:
                    errors.append(f'Location sync failed: {str(e)}')

            # Sync Discounts
            if self.sync_discounts:
                try:
                    self.env['shopify.discount'].sync_from_shopify(self.shopify_instance_id.id)
                    success_messages.append('Discounts synced successfully')
                except Exception as e:
                    errors.append(f'Discount sync failed: {str(e)}')

            # Update last run info
            if errors:
                status = 'partial' if success_messages else 'failed'
                message = '\n'.join(success_messages + errors)
            else:
                status = 'success'
                message = '\n'.join(success_messages)

            self.write({
                'last_run': fields.Datetime.now(),
                'last_run_status': status,
                'last_run_message': message,
                'is_running': False,
            })

            _logger.info(f'Scheduled sync completed with status: {status}')

        except Exception as e:
            _logger.error(f'Scheduled sync failed: {str(e)}')
            self.write({
                'last_run': fields.Datetime.now(),
                'last_run_status': 'failed',
                'last_run_message': str(e),
                'is_running': False,
            })

        finally:
            # Always release the lock
            with _sync_lock:
                _running_syncs[scheduler_key] = False

    def action_run_now(self):
        """Manually trigger the sync"""
        self.ensure_one()
        self.run_scheduled_sync()

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Sync Completed'),
                'message': self.last_run_message,
                'type': 'success' if self.last_run_status == 'success' else 'warning',
                'sticky': True,
            }
        }
