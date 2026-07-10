# -*- coding: utf-8 -*-

from odoo import api, fields, models, _
from odoo.exceptions import UserError
import logging
import threading
import requests
import certifi

_logger = logging.getLogger(__name__)

# Guard against two collection imports running at once for the same instance —
# concurrent upserts of the same collections cause PostgreSQL serialization
# failures ("could not serialize access due to concurrent update").
_import_lock = threading.Lock()
_imports_running = {}


class ShopifyCollection(models.Model):
    _name = 'shopify.collection'
    _description = 'Shopify Product Collection'
    _order = 'name'

    name = fields.Char('Collection Name', required=True)
    shopify_instance_id = fields.Many2one('shopify.instance', string='Shopify Instance', required=True, ondelete='cascade')
    shopify_collection_id = fields.Char('Shopify Collection ID', readonly=True)
    collection_type = fields.Selection([
        ('smart', 'Smart Collection'),
        ('custom', 'Custom Collection'),
    ], string='Type', default='custom')

    description = fields.Html('Description')
    published = fields.Boolean('Published', default=True)
    published_scope = fields.Selection([
        ('web', 'Online Store'),
        ('global', 'Online Store and Point of Sale'),
    ], string='Published Scope', default='web')

    sort_order = fields.Selection([
        ('alpha-asc', 'Alphabetically, A-Z'),
        ('alpha-desc', 'Alphabetically, Z-A'),
        ('best-selling', 'Best Selling'),
        ('created', 'Created (oldest first)'),
        ('created-desc', 'Created (newest first)'),
        ('manual', 'Manual'),
        ('most-relevant', 'Most Relevant'),
        ('price-asc', 'Price, low to high'),
        ('price-desc', 'Price, high to low'),
    ], string='Sort Order', default='manual')

    product_ids = fields.Many2many(
        'product.template',
        'product_template_shopify_collection_rel', 'shopify_collection_id', 'product_template_id',
        string='Products')
    product_count = fields.Integer('Product Count', compute='_compute_product_count')

    image_url = fields.Char('Image URL')

    shopify_created_at = fields.Datetime('Created At (Shopify)', readonly=True)
    shopify_updated_at = fields.Datetime('Updated At (Shopify)', readonly=True)

    @api.depends('product_ids')
    def _compute_product_count(self):
        for record in self:
            record.product_count = len(record.product_ids)

    def sync_from_shopify(self):
        """Import collections from Shopify (kept for backward compatibility)."""
        self.ensure_one()
        total = self.sync_collections_from_shopify(self.shopify_instance_id.id)
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Success'),
                'message': _('Synced %s collections with products') % total,
                'type': 'success',
            }
        }

    @api.model
    def sync_collections_from_shopify(self, instance_id, notify_uid=None):
        """Import ALL collections (custom + smart, paginated) with their product
        links. Commits after every collection so progress is never lost, and
        sends bus progress toasts to `notify_uid`. Returns the total count."""
        instance = self.env['shopify.instance'].browse(instance_id)
        if not instance:
            raise UserError(_('Shopify instance not found'))

        # Only one import at a time per database+instance.
        run_key = (self.env.cr.dbname, instance_id)
        with _import_lock:
            if _imports_running.get(run_key):
                _logger.warning('Collection import already running for instance %s — skipping duplicate start',
                                instance_id)
                return 0
            _imports_running[run_key] = True

        try:
            return self._run_collection_sync(instance, notify_uid)
        finally:
            with _import_lock:
                _imports_running.pop(run_key, None)

    def _run_collection_sync(self, instance, notify_uid=None):
        def _notify(title, message, msg_type='info'):
            if not notify_uid:
                return
            try:
                partner = self.env['res.users'].browse(notify_uid).partner_id
                self.env['bus.bus']._sendone(partner, 'simple_notification', {
                    'title': title, 'message': message, 'type': msg_type,
                })
                self.env.cr.commit()
            except Exception as bus_err:
                _logger.warning(f'Bus notification failed: {bus_err}')

        base = instance._get_base_url()
        headers = instance._get_headers()
        total = 0

        for ctype, endpoint, key in (
            ('custom', 'custom_collections.json', 'custom_collections'),
            ('smart', 'smart_collections.json', 'smart_collections'),
        ):
            params = {'limit': 250}
            page_info = None
            while True:
                request_params = {'page_info': page_info, 'limit': 250} if page_info else params
                try:
                    response = requests.get(f"{base}/{endpoint}", headers=headers,
                                            params=request_params, timeout=60, verify=certifi.where())
                except Exception as e:
                    _logger.warning('%s fetch error: %s', endpoint, e)
                    break
                if response.status_code != 200:
                    _logger.warning('%s fetch failed: %s - %s', endpoint,
                                    response.status_code, response.text[:200])
                    break
                rows = response.json().get(key, [])
                if not rows:
                    break

                for collection_data in rows:
                    # STEP 1 — save the collection itself and COMMIT it first, so
                    # a slow/failing product fetch (large collections) can never
                    # roll it back. Up to 2 attempts for serialization conflicts.
                    collection = False
                    for attempt in (1, 2):
                        try:
                            collection = self._create_or_update_collection(collection_data, instance, ctype)
                            self.env.cr.commit()
                            total += 1
                            if total % 10 == 0:
                                _notify(_('Collection Import Progress'),
                                        _('%s collections imported so far...') % total)
                            break
                        except Exception as col_err:
                            self.env.cr.rollback()
                            if attempt == 1 and 'serialize' in str(col_err).lower():
                                _logger.info('Retrying collection %s after concurrent update...',
                                             collection_data.get('title'))
                                continue
                            _logger.error('Collection %s failed: %s',
                                          collection_data.get('title'), col_err)
                            collection = False
                            break

                    # STEP 2 — link its products in a SEPARATE transaction; if
                    # this fails, the collection above is already saved.
                    if collection and collection.shopify_collection_id:
                        try:
                            self._fetch_collection_products(collection, instance)
                            self.env.cr.commit()
                        except Exception as prod_err:
                            self.env.cr.rollback()
                            _logger.warning('Product linking for collection %s failed (collection kept): %s',
                                            collection_data.get('title'), prod_err)
                link = response.headers.get('Link', '')
                if 'rel="next"' in link:
                    page_info = None
                    for part in link.split(','):
                        if 'rel="next"' in part:
                            page_info = part.split('page_info=')[1].split('>')[0]
                            break
                    if not page_info:
                        break
                else:
                    break

        _logger.info('Collection import complete: %s collections', total)
        _notify(_('Collection Import Complete'),
                _('All done! %s collections imported with their products.') % total, 'success')
        return total

    def _fetch_collection_products(self, collection, instance):
        """Link the collection to its Odoo products.

        Fast path: fetch only the product IDs from Shopify (fields=id) and
        resolve them with a single Odoo search, instead of full payloads and
        one search per product.
        """
        shopify_ids = self.env['product.template']._fetch_collection_product_ids(
            instance, collection.shopify_collection_id)
        if not shopify_ids:
            return
        products = self.env['product.template'].search([
            ('shopify_product_id', 'in', shopify_ids),
            ('shopify_instance_id', '=', instance.id),
        ])
        if products:
            collection.write({'product_ids': [(6, 0, products.ids)]})
            _logger.info(f'Collection "{collection.name}" linked to {len(products)} products')

    def action_fetch_products(self):
        """Manual action to fetch products for this collection"""
        self.ensure_one()
        if not self.shopify_collection_id:
            raise UserError(_('This collection has not been synced from Shopify yet.'))

        self._fetch_collection_products(self, self.shopify_instance_id)

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Success'),
                'message': _('Fetched %s products for this collection') % self.product_count,
                'type': 'success',
            }
        }

    def _create_or_update_collection(self, collection_data, instance, collection_type):
        """Create or update collection and return the record"""
        shopify_id = str(collection_data.get('id'))

        existing = self.search([
            ('shopify_collection_id', '=', shopify_id),
            ('shopify_instance_id', '=', instance.id)
        ], limit=1)

        # Guard against sort orders Odoo doesn't know (Shopify adds new ones).
        valid_sort_orders = {v for v, _l in self._fields['sort_order'].selection}
        sort_order = collection_data.get('sort_order', 'manual')
        if sort_order not in valid_sort_orders:
            _logger.info('Unknown Shopify sort_order %r for collection %s — storing as manual',
                         sort_order, collection_data.get('title'))
            sort_order = 'manual'

        vals = {
            'name': collection_data.get('title', 'Untitled'),
            'shopify_instance_id': instance.id,
            'shopify_collection_id': shopify_id,
            'collection_type': collection_type,
            'description': collection_data.get('body_html', ''),
            'published': collection_data.get('published', True),
            'sort_order': sort_order,
        }

        if collection_data.get('image'):
            vals['image_url'] = collection_data['image'].get('src', '')

        if existing:
            existing.write(vals)
            return existing
        else:
            return self.create(vals)
