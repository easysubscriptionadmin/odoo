# -*- coding: utf-8 -*-

from odoo import api, fields, models, _
from odoo.exceptions import UserError
import logging
import json
import requests
import certifi
import base64
from dateutil import parser as date_parser

_logger = logging.getLogger(__name__)

# Shopify product metafields surfaced as dedicated fields in the product
# General Information tab. Key = Odoo technical field, value = the Shopify
# metafield definition LABEL to match (case-insensitive) when importing.
SHOPIFY_METAFIELD_FIELD_MAP = {
    'shopify_brand': 'Brand',
    'shopify_brand_code': 'Brand Code',
    'shopify_dimensions': 'Dimensions',
    'shopify_arabic_name': 'Arabic Name',
    'shopify_mpn': 'MPN',
    'shopify_recommended_age': 'Recommended Age',
    'shopify_battery_information': 'Battery Information',
    'shopify_assembly_required': 'Assembly Required',
    'shopify_rating_value': 'Rating Value',
    'shopify_free_installation': 'Free Installation',
    'shopify_meta_label': 'Meta Label',
    'shopify_header_select': 'Header Select',
    'shopify_skills': 'Skills',
    'shopify_characters': 'Characters',
    'shopify_mf_discount': 'Discount',
    'shopify_category_new': 'Category New',
    'shopify_sub_category_new': 'Sub Category New',
    'shopify_age_new': 'Age New',
    'shopify_gender_new': 'Gender New',
    'shopify_diaper_size': 'Diaper Size',
    'shopify_mf_type': 'Type',
    'shopify_mf_color': 'Color',
}


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    shopify_instance_id = fields.Many2one('shopify.instance', string='Shopify Instance', ondelete='cascade')
    shopify_product_id = fields.Char('Shopify Product ID', readonly=True, copy=False)
    is_shopify_product = fields.Boolean('Is Shopify Product', default=False, copy=False)
    shopify_product_status = fields.Selection([
        ('active', 'Active'),
        ('draft', 'Draft'),
        ('archived', 'Archived')
    ], string='Shopify Status', default='active')
    shopify_product_type = fields.Char('Shopify Product Type')
    shopify_vendor = fields.Char('Shopify Vendor')
    shopify_tags = fields.Char('Shopify Tags')
    shopify_collections = fields.Char('Shopify Collections (names)',
        help="Comma-separated list of the Shopify collections this product belongs to.")
    shopify_collection_ids = fields.Many2many(
        'shopify.collection',
        'product_template_shopify_collection_rel', 'product_template_id', 'shopify_collection_id',
        string='Collections',
        help="Select the Shopify collections this product belongs to. "
             "Custom collections are pushed to Shopify on sync; smart collections "
             "are rule-based and assigned automatically by Shopify.")
    shopify_published_at = fields.Datetime('Published At')
    shopify_created_at = fields.Datetime('Shopify Created At')
    shopify_updated_at = fields.Datetime('Shopify Updated At')
    shopify_handle = fields.Char('Shopify Handle', copy=False)
    shopify_template_suffix = fields.Char('Template Suffix')
    shopify_options_json = fields.Text('Shopify Options (raw)', copy=False)
    shopify_raw_data = fields.Text('Shopify Raw Data', copy=False,
        help="Complete untouched JSON of this product as received from Shopify — every key.")
    shopify_image_ids = fields.One2many(
        'shopify.product.media', 'product_tmpl_id', string='Shopify Media')
    shopify_metafield_ids = fields.One2many(
        'shopify.product.metafield', 'product_tmpl_id', string='Product Metafields',
        domain=[('owner_type', '=', 'product')], context={'default_owner_type': 'product'})
    shopify_variant_metafield_ids = fields.One2many(
        'shopify.product.metafield', 'product_tmpl_id', string='Variant Metafields',
        domain=[('owner_type', '=', 'variant')], context={'default_owner_type': 'variant'})
    shopify_metafields_count = fields.Integer('Metafields', compute='_compute_metafields_count')
    shopify_company = fields.Char(
        'Company', compute='_compute_shopify_company',
        help="Shopify store (domain) this product was imported from / syncs to.")

    @api.depends('shopify_instance_id', 'shopify_instance_id.shop_url')
    def _compute_shopify_company(self):
        for rec in self:
            instance = rec.shopify_instance_id
            rec.shopify_company = instance._get_shop_domain() if instance else False

    # ── Shopify metafields surfaced as product fields (General Information) ──
    shopify_brand = fields.Char('Brand')
    shopify_brand_code = fields.Char('Brand Code')
    shopify_dimensions = fields.Char('Dimensions')
    shopify_arabic_name = fields.Char('Arabic Name')
    shopify_mpn = fields.Char('MPN')
    shopify_recommended_age = fields.Char('Recommended Age')
    shopify_battery_information = fields.Char('Battery Information')
    shopify_assembly_required = fields.Char('Assembly Required')
    shopify_rating_value = fields.Char('Rating Value')
    shopify_free_installation = fields.Char('Free Installation')
    shopify_meta_label = fields.Char('Meta Label')
    shopify_header_select = fields.Char('Header Select')
    shopify_skills = fields.Char('Skills')
    shopify_characters = fields.Char('Characters')
    shopify_mf_discount = fields.Char('Discount (Shopify)')
    shopify_category_new = fields.Char('Category New')
    shopify_sub_category_new = fields.Char('Sub Category New')
    shopify_age_new = fields.Char('Age New')
    shopify_gender_new = fields.Char('Gender New')
    shopify_diaper_size = fields.Char('Diaper Size')
    shopify_mf_type = fields.Char('Type (Shopify)')
    shopify_mf_color = fields.Char('Color')

    def _compute_metafields_count(self):
        for rec in self:
            rec.shopify_metafields_count = len(rec.shopify_metafield_ids)

    def _sync_attribute_fields_to_metafields(self):
        """Mirror the General Information 'Shopify Attributes' fields into the
        Shopify-tab metafield rows carrying the same label, creating missing
        rows (linked to the store definition when one exists)."""
        Metafield = self.env['shopify.product.metafield']
        Definition = self.env['shopify.metafield.definition']
        for rec in self:
            for fname, label in SHOPIFY_METAFIELD_FIELD_MAP.items():
                value = getattr(rec, fname)
                rows = rec.shopify_metafield_ids.filtered(
                    lambda m: (m.label or '').strip().lower() == label.lower())
                if rows:
                    row = rows[0]
                    # Reference metafields (metaobject/file) only accept Shopify
                    # GIDs — they are set via the "Select Values" picker, never
                    # from free text typed in General Information.
                    if '_reference' in (row.type or ''):
                        continue
                    if (row.value or '') != (value or ''):
                        row.with_context(shopify_sync_skip=True).write({'value': value or False})
                    continue
                if not value:
                    continue
                domain = [('owner_type', '=', 'product'), ('name', '=ilike', label)]
                definition = Definition.search(
                    domain + [('shopify_instance_id', '=', rec.shopify_instance_id.id)], limit=1) \
                    if rec.shopify_instance_id else Definition.browse()
                definition = definition or Definition.search(domain, limit=1)
                if definition and '_reference' in (definition.type or ''):
                    # Same guard for rows we would create from typed text.
                    continue
                vals = {
                    'product_tmpl_id': rec.id,
                    'owner_type': 'product',
                    'label': label,
                    'value': value,
                    'shopify_instance_id': rec.shopify_instance_id.id if rec.shopify_instance_id else False,
                }
                if definition:
                    vals['definition_id'] = definition.id
                Metafield.with_context(shopify_sync_skip=True).create(vals)

    def import_shopify_products(self, instance_id, batch_size=25, skip_images=False,
                                import_variants=True, import_metafields=True,
                                import_collections=True, notify_uid=None):
        """Import products from Shopify with batch processing — captures
        everything: all variants, options/attributes, all images, all
        metafields, collections, and the full raw JSON of each product.

        Args:
            instance_id: Shopify instance ID
            batch_size: Number of products to process per batch (default 25 for stability)
            skip_images: Skip image download during bulk import for speed (default False = import all images)
            import_variants: Create/update every variant with all its fields (default True)
            import_metafields: Fetch and store product + variant metafields (default True)
            import_collections: Fetch and store each product's collections (default True)
        """
        instance = self.env['shopify.instance'].browse(instance_id)
        if not instance:
            raise UserError(_('Shopify instance not found'))

        def _notify(title, message, msg_type='info'):
            """Push a live progress toast to the requesting user via the bus."""
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

        # Fetch metafield definitions ONCE (store-wide) so every product can
        # display all defined fields — empty ones shown as "Not configured".
        product_defs, variant_defs = [], []
        if import_metafields:
            product_defs = instance.fetch_metafield_definitions('PRODUCT')
            variant_defs = instance.fetch_metafield_definitions('PRODUCTVARIANT')
            _logger.info('Fetched %s product + %s variant metafield definitions',
                         len(product_defs), len(variant_defs))

        # NOTE: collections are handled AFTER the product loop (second phase) so
        # products stream in immediately and are never blocked by the (slow)
        # store-wide collection/collects fetch.
        collections_map = {}

        try:
            url = f"{instance._get_base_url()}/products.json"
            params = {'limit': 250}
            headers = instance._get_headers()

            created_count = 0
            updated_count = 0
            total_fetched = 0
            page_info = None
            page_number = 1

            while True:
                # When using page_info, only pass page_info parameter (Shopify API requirement)
                if page_info:
                    request_params = {'page_info': page_info}
                else:
                    request_params = params

                _logger.info(f'Fetching products page {page_number}...')
                response = requests.get(url, headers=headers, params=request_params, timeout=60, verify=certifi.where())

                if response.status_code != 200:
                    raise UserError(_('Failed to fetch products: %s - %s') % (response.status_code, response.text))

                data = response.json()
                products = data.get('products', [])

                if not products:
                    _logger.info('No more products to fetch')
                    break

                total_fetched += len(products)
                _logger.info(f'Page {page_number}: Fetched {len(products)} products (Total: {total_fetched})')

                # Process products in smaller batches
                for i in range(0, len(products), batch_size):
                    batch = products[i:i + batch_size]
                    batch_num = (i // batch_size) + 1
                    _logger.info(f'Processing batch {batch_num} of {(len(products) + batch_size - 1) // batch_size}...')

                    try:
                        batch_created, batch_updated = self._process_product_batch(
                            batch, instance, skip_images=skip_images,
                            import_variants=import_variants, import_metafields=import_metafields,
                            product_defs=product_defs, variant_defs=variant_defs,
                            collections_map=collections_map)
                        created_count += batch_created
                        updated_count += batch_updated

                        # Commit after each batch to save progress
                        self.env.cr.commit()
                        _logger.info(f'Batch {batch_num} complete: Created {batch_created}, Updated {batch_updated}')
                        _notify(
                            _('Product Import Progress'),
                            _('Fetched %s products so far — Created: %s | Updated: %s') % (
                                total_fetched, created_count, updated_count),
                            'info')
                    except Exception as batch_error:
                        _logger.error(f'Error processing batch {batch_num}: {str(batch_error)}')
                        # Rollback failed batch and continue
                        self.env.cr.rollback()
                        continue

                # Check for pagination
                link_header = response.headers.get('Link', '')
                if 'rel="next"' in link_header:
                    for link in link_header.split(','):
                        if 'rel="next"' in link:
                            page_info = link.split('page_info=')[1].split('>')[0]
                            break
                    page_number += 1
                else:
                    break

            _logger.info(f'Product import complete! Total: {total_fetched}, Created: {created_count}, Updated: {updated_count}')

            # Update sync timestamp
            instance.write({'last_product_sync': fields.Datetime.now()})

            # ── Phase 2: collections (after products exist, non-blocking) ──
            if import_collections:
                try:
                    _logger.info('Building collections map (phase 2)...')
                    collections_map = self._build_product_collections_map(instance)
                    Collection = self.env['shopify.collection']
                    updated_cols = 0
                    for pid, entries in collections_map.items():
                        prod = self.search([
                            ('shopify_product_id', '=', pid),
                            ('shopify_instance_id', '=', instance.id),
                        ], limit=1)
                        if not prod:
                            continue
                        cids = [cid for cid, _t in entries]
                        col_records = Collection.search([
                            ('shopify_instance_id', '=', instance.id),
                            ('shopify_collection_id', 'in', cids),
                        ])
                        vals = {
                            'shopify_collections': ', '.join(sorted({t for _c, t in entries if t})),
                        }
                        if col_records:
                            vals['shopify_collection_ids'] = [(6, 0, col_records.ids)]
                        prod.with_context(shopify_sync_skip=True).write(vals)
                        updated_cols += 1
                        if updated_cols % 100 == 0:
                            self.env.cr.commit()
                    self.env.cr.commit()
                    _logger.info('Collections set on %s products', updated_cols)
                    _notify(_('Collections Linked'),
                            _('Collections set on %s products.') % updated_cols, 'success')
                except Exception as col_err:
                    _logger.warning('Collections phase failed (products still imported): %s', col_err)

            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Products Imported'),
                    'message': _('Total: %s, Created: %s, Updated: %s') % (total_fetched, created_count, updated_count),
                    'type': 'success',
                }
            }

        except Exception as e:
            _logger.error(f'Error importing products: {str(e)}')
            raise UserError(_('Error importing products: %s') % str(e))

    def _process_product_batch(self, products_batch, instance, skip_images=False,
                               import_variants=True, import_metafields=True,
                               product_defs=None, variant_defs=None, collections_map=None):
        """Process a batch of products — full import including variants,
        attributes, images and metafields.

        Args:
            products_batch: List of product data from Shopify
            instance: Shopify instance record
            skip_images: Skip image download for faster processing
            import_variants: Create/update all variants with full data
            import_metafields: Fetch and store metafields
        """
        created_count = 0
        updated_count = 0

        for product_data in products_batch:
            try:
                ctx = {'shopify_sync_skip': True}
                existing_product = self.search([
                    ('shopify_product_id', '=', str(product_data['id'])),
                    ('shopify_instance_id', '=', instance.id)
                ], limit=1)

                product_vals = self._prepare_product_vals(
                    product_data, instance, skip_images=skip_images,
                    is_new=not existing_product, import_variants=import_variants,
                    collections_map=collections_map)

                if existing_product:
                    existing_product.with_context(**ctx).write(product_vals)
                    template = existing_product
                    is_new = False
                    # Products imported before options support have no attribute
                    # lines — build them now (safe: only when none exist yet) so
                    # variants show in the native Attributes & Variants tab.
                    if import_variants and not template.attribute_line_ids:
                        attr_lines = self._build_attribute_lines(product_data.get('options', []))
                        if attr_lines:
                            template.with_context(**ctx).write({'attribute_line_ids': attr_lines})
                    _logger.debug(f'Updated product: {product_data.get("title")}')
                else:
                    template = self.with_context(**ctx).create(product_vals)
                    is_new = True
                    _logger.debug(f'Created product: {product_data.get("title")}')

                # Post-processing that needs the (existing) variants to be present.
                if import_variants:
                    template.with_context(**ctx)._apply_variant_data(product_data, instance)
                if not skip_images:
                    template.with_context(**ctx)._import_all_images(product_data, instance)
                if import_metafields:
                    template.with_context(**ctx)._import_product_metafields(
                        product_data, instance, include_variants=import_variants,
                        product_defs=product_defs, variant_defs=variant_defs)

                # Commit each product individually so a later failure (e.g. an
                # ir_attachment lock timeout) never discards already-imported
                # products, and DB locks are released promptly.
                self.env.cr.commit()
                if is_new:
                    created_count += 1
                else:
                    updated_count += 1
            except Exception as e:
                # Roll back ONLY this product's work and keep going.
                self.env.cr.rollback()
                _logger.error(
                    f'Error processing product {product_data.get("id")} '
                    f'({product_data.get("title")}): {str(e)}',
                    exc_info=True,
                )
                continue

        return created_count, updated_count

    def _parse_shopify_datetime(self, datetime_str):
        """Parse Shopify datetime string to Odoo datetime"""
        if not datetime_str:
            return False
        try:
            # Parse ISO 8601 format and convert to naive datetime (remove timezone)
            dt = date_parser.parse(datetime_str)
            return dt.replace(tzinfo=None)
        except:
            return False

    def _download_image(self, image_url):
        """Download image from URL and return base64 encoded data"""
        try:
            if not image_url:
                return False

            response = requests.get(image_url, timeout=30, verify=certifi.where())
            if response.status_code == 200:
                return base64.b64encode(response.content)
            return False
        except Exception as e:
            _logger.warning(f'Failed to download image from {image_url}: {str(e)}')
            return False

    def _build_product_collections_map(self, instance):
        """Return {shopify_product_id(str): 'Collection A, Collection B'} built
        once for the whole store: custom-collection membership via /collects.json
        and smart-collection membership via each collection's products endpoint."""
        base = instance._get_base_url()
        headers = instance._get_headers()
        titles = {}       # collection_id -> title
        prod_cols = {}    # product_id(str) -> set(titles)

        def _paginate(url, key, handler):
            params = {'limit': 250}
            page_info = None
            while True:
                request_params = {'page_info': page_info} if page_info else params
                try:
                    r = requests.get(url, headers=headers, params=request_params,
                                     timeout=60, verify=certifi.where())
                except Exception as e:
                    _logger.warning('Collections fetch error %s: %s', url, e)
                    return
                if r.status_code != 200:
                    _logger.warning('Collections fetch %s failed: %s', url, r.status_code)
                    return
                rows = r.json().get(key, [])
                if not rows:
                    return
                handler(rows)
                link = r.headers.get('Link', '')
                if 'rel="next"' in link:
                    page_info = None
                    for part in link.split(','):
                        if 'rel="next"' in part:
                            page_info = part.split('page_info=')[1].split('>')[0]
                            break
                    if not page_info:
                        return
                else:
                    return

        def _add_collection(rows, is_smart):
            for c in rows:
                cid = c.get('id')
                title = c.get('title', '')
                titles[cid] = title
                if is_smart and cid:
                    # smart collections have no collects — fetch their products
                    for pid in self._fetch_collection_product_ids(instance, cid):
                        prod_cols.setdefault(pid, set()).add((str(cid), title))

        _paginate(f"{base}/custom_collections.json", 'custom_collections',
                  lambda rows: _add_collection(rows, False))
        _paginate(f"{base}/smart_collections.json", 'smart_collections',
                  lambda rows: _add_collection(rows, True))

        def _add_collects(rows):
            for col in rows:
                pid = str(col.get('product_id'))
                cid = col.get('collection_id')
                if cid in titles:
                    prod_cols.setdefault(pid, set()).add((str(cid), titles[cid]))
        _paginate(f"{base}/collects.json", 'collects', _add_collects)

        # {shopify_product_id: {(collection_id, title), ...}}
        return prod_cols

    @staticmethod
    def _collections_map_to_str(value):
        """Normalize a collections-map entry (str or set of (cid, title)) to a
        comma-separated string of titles."""
        if isinstance(value, str):
            return value
        try:
            return ', '.join(sorted({t for _c, t in value if t}))
        except Exception:
            return ''

    def _fetch_collection_product_ids(self, instance, collection_id):
        """Return the list of Shopify product ids (str) in a collection."""
        base = instance._get_base_url()
        headers = instance._get_headers()
        ids = []
        params = {'limit': 250, 'fields': 'id'}
        page_info = None
        while True:
            request_params = {'page_info': page_info, 'fields': 'id'} if page_info else params
            try:
                r = requests.get(f"{base}/collections/{collection_id}/products.json",
                                 headers=headers, params=request_params, timeout=60, verify=certifi.where())
            except Exception:
                break
            if r.status_code != 200:
                break
            prods = r.json().get('products', [])
            if not prods:
                break
            ids.extend(str(p['id']) for p in prods)
            link = r.headers.get('Link', '')
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
        return ids

    def _prepare_product_vals(self, product_data, instance, skip_images=False,
                              is_new=True, import_variants=True, collections_map=None):
        """Prepare product template values from Shopify data.

        Args:
            product_data: Product data from Shopify API
            instance: Shopify instance record
            skip_images: Skip image download for faster bulk processing
            is_new: True when creating (attributes are only set on create)
            import_variants: Whether attribute lines should be built from options
            collections_map: {shopify_product_id: "Col A, Col B"} to fill collections
        """
        # Safely get a representative price/sku from the first variant for the template.
        price = 0.0
        sku = ''
        variants = product_data.get('variants', [])
        if variants:
            first_variant = variants[0]
            price_str = first_variant.get('price', '0')
            try:
                price = float(price_str) if price_str else 0.0
            except (TypeError, ValueError):
                price = 0.0
            sku = first_variant.get('sku', '') or ''

        vals = {
            'name': product_data.get('title', 'Unnamed Product'),
            'description': product_data.get('body_html', ''),
            'shopify_instance_id': instance.id,
            'shopify_product_id': str(product_data['id']),
            'is_shopify_product': True,
            'shopify_product_status': product_data.get('status', 'active'),
            'shopify_product_type': product_data.get('product_type', ''),
            'shopify_vendor': product_data.get('vendor', ''),
            # Brand mirrors the Shopify vendor; a "Brand" metafield with a
            # value (applied later in _apply_metafield_fields) overrides it.
            'shopify_brand': product_data.get('vendor', ''),
            'shopify_tags': product_data.get('tags', ''),
            'shopify_collections': self._collections_map_to_str(
                (collections_map or {}).get(str(product_data['id']), '')),
            'shopify_handle': product_data.get('handle', ''),
            'shopify_template_suffix': product_data.get('template_suffix', ''),
            'shopify_published_at': self._parse_shopify_datetime(product_data.get('published_at')),
            'shopify_created_at': self._parse_shopify_datetime(product_data.get('created_at')),
            'shopify_updated_at': self._parse_shopify_datetime(product_data.get('updated_at')),
            'shopify_options_json': json.dumps(product_data.get('options', []), indent=2),
            'shopify_raw_data': json.dumps(product_data, indent=2, default=str),
            'list_price': price,
            'default_code': sku,
            'sale_ok': True,
            'purchase_ok': True,
            # Track inventory so Shopify quantities are usable in Odoo stock.
            'is_storable': True,
        }

        # Set currency from instance if available
        if instance.currency_id:
            vals['currency_id'] = instance.currency_id.id

        # Build attribute lines (options -> Odoo attributes) only on create, so
        # Odoo generates a product.product variant per combination. Changing
        # attributes on existing products with variants/stock is unsafe.
        if is_new and import_variants:
            attribute_lines = self._build_attribute_lines(product_data.get('options', []))
            if attribute_lines:
                vals['attribute_line_ids'] = attribute_lines

        # Main product image (first image) on the template.
        if not skip_images:
            images = product_data.get('images', [])
            if images:
                main_image_url = images[0].get('src')
                if main_image_url:
                    image_data = self._download_image(main_image_url)
                    if image_data:
                        vals['image_1920'] = image_data

        return vals

    # ─────────────────────────────────────────────────────────────────────────
    # Helpers: attributes, variants, images, metafields
    # ─────────────────────────────────────────────────────────────────────────

    def _is_default_option(self, options):
        """True when the product has no real options (Shopify's default
        'Title / Default Title' placeholder)."""
        if not options:
            return True
        if len(options) == 1:
            opt = options[0]
            values = opt.get('values') or []
            if opt.get('name') == 'Title' and values == ['Default Title']:
                return True
        return False

    def _get_or_create_attribute(self, name):
        Attribute = self.env['product.attribute']
        attr = Attribute.search([('name', '=', name)], limit=1)
        if not attr:
            attr = Attribute.create({'name': name, 'create_variant': 'always'})
        return attr

    def _get_or_create_attribute_value(self, attribute, value_name):
        Value = self.env['product.attribute.value']
        val = Value.search([
            ('attribute_id', '=', attribute.id),
            ('name', '=', value_name),
        ], limit=1)
        if not val:
            val = Value.create({'attribute_id': attribute.id, 'name': value_name})
        return val

    def _build_attribute_lines(self, options):
        """Return attribute_line_ids create-commands for real Shopify options."""
        if self._is_default_option(options):
            return []
        lines = []
        for option in options:
            name = option.get('name')
            values = option.get('values') or []
            if not name or not values:
                continue
            attribute = self._get_or_create_attribute(name)
            value_ids = [self._get_or_create_attribute_value(attribute, v).id for v in values]
            lines.append((0, 0, {
                'attribute_id': attribute.id,
                'value_ids': [(6, 0, value_ids)],
            }))
        return lines

    def _find_variant_for_options(self, option_values):
        """Match a Shopify variant (by its option value names) to one of this
        template's Odoo variants."""
        self.ensure_one()
        wanted = {v for v in option_values if v}
        if not self.attribute_line_ids or not wanted:
            # Single-variant product.
            return self.product_variant_ids[:1]
        for variant in self.product_variant_ids:
            names = {ptav.name for ptav in variant.product_template_attribute_value_ids}
            if names == wanted:
                return variant
        return self.env['product.product']

    def _apply_variant_data(self, product_data, instance):
        """Populate every Odoo variant with the full Shopify variant data."""
        self.ensure_one()
        for sv in product_data.get('variants', []):
            option_values = [sv.get('option1'), sv.get('option2'), sv.get('option3')]
            variant = self._find_variant_for_options(option_values)
            if not variant:
                _logger.warning(
                    'No matching Odoo variant for Shopify variant %s of product %s',
                    sv.get('id'), self.shopify_product_id)
                continue
            try:
                price = float(sv.get('price') or 0.0)
            except (TypeError, ValueError):
                price = 0.0
            try:
                compare_at = float(sv.get('compare_at_price') or 0.0)
            except (TypeError, ValueError):
                compare_at = 0.0
            variant.with_context(shopify_sync_skip=True).write({
                'default_code': sv.get('sku') or variant.default_code,
                'barcode': sv.get('barcode') or False,
                'weight': sv.get('weight') or 0.0,
                'lst_price': price,
                'shopify_variant_id': str(sv['id']),
                'shopify_inventory_item_id': str(sv.get('inventory_item_id') or ''),
                'shopify_sku': sv.get('sku') or '',
                'shopify_barcode': sv.get('barcode') or '',
                'shopify_position': sv.get('position') or 1,
                'shopify_weight': sv.get('weight') or 0.0,
                'shopify_weight_unit': sv.get('weight_unit') or 'g',
                'shopify_requires_shipping': sv.get('requires_shipping', True),
                'shopify_taxable': sv.get('taxable', True),
                'shopify_inventory_policy': sv.get('inventory_policy') or 'deny',
                'shopify_inventory_management': sv.get('inventory_management') or '',
                'shopify_fulfillment_service': sv.get('fulfillment_service') or 'manual',
                'shopify_compare_at_price': compare_at,
                'shopify_inventory_quantity': sv.get('inventory_quantity') or 0.0,
                'shopify_option1': sv.get('option1') or '',
                'shopify_option2': sv.get('option2') or '',
                'shopify_option3': sv.get('option3') or '',
                'shopify_variant_title': sv.get('title') or '',
                'shopify_variant_created_at': self._parse_shopify_datetime(sv.get('created_at')),
                'shopify_variant_updated_at': self._parse_shopify_datetime(sv.get('updated_at')),
                'shopify_variant_raw_data': json.dumps(sv, indent=2, default=str),
            })
            # First-time stock: reflect Shopify's quantity as Odoo On Hand
            # (only when Odoo holds no stock for this variant yet).
            qty = sv.get('inventory_quantity')
            if qty:
                self._set_initial_stock(variant, qty)

    def _set_initial_stock(self, variant, qty):
        """Set Odoo on-hand quantity for a variant from Shopify.

        Applied only when the variant has no internal stock quants yet, so
        stock managed in Odoo is never overwritten by a re-import.
        """
        try:
            if not variant.is_storable:
                return
            existing = self.env['stock.quant'].search_count([
                ('product_id', '=', variant.id),
                ('location_id.usage', '=', 'internal'),
            ])
            if existing:
                return
            warehouse = self.env['stock.warehouse'].search([], limit=1)
            if not warehouse:
                return
            quant = self.env['stock.quant'].with_context(inventory_mode=True).create({
                'product_id': variant.id,
                'location_id': warehouse.lot_stock_id.id,
                'inventory_quantity': qty,
            })
            quant.action_apply_inventory()
            _logger.info('Initial stock set for %s: %s', variant.display_name, qty)
        except Exception as e:
            _logger.warning('Could not set initial stock for %s: %s', variant.display_name, e)

    def _import_all_images(self, product_data, instance):
        """Import every product image into the Shopify Media gallery, set the
        main template image, and map variant-specific images."""
        self.ensure_one()
        images = product_data.get('images', [])
        if not images:
            return
        Media = self.env['shopify.product.media']
        # Map Shopify variant id -> Odoo variant for per-variant images.
        variant_by_sid = {
            v.shopify_variant_id: v for v in self.product_variant_ids if v.shopify_variant_id
        }
        # Reset existing media for a clean re-import.
        self.shopify_image_ids.unlink()
        for idx, img in enumerate(images):
            src = img.get('src')
            if not src:
                continue
            data = self._download_image(src)
            if not data:
                continue
            # Every image goes into the media gallery.
            Media.create({
                'name': img.get('alt') or f"Image {img.get('id', idx + 1)}",
                'image_1920': data,
                'src': src,
                'alt': img.get('alt') or '',
                'sequence': img.get('position') or (idx + 1),
                'shopify_image_id': str(img.get('id') or ''),
                'product_tmpl_id': self.id,
                'shopify_instance_id': instance.id,
            })
            # First image is also the main template image.
            if idx == 0:
                self.with_context(shopify_sync_skip=True).image_1920 = data
            # Attach to specific variants if Shopify links it.
            for vid in img.get('variant_ids', []):
                variant = variant_by_sid.get(str(vid))
                if variant and not variant.image_1920:
                    variant.with_context(shopify_sync_skip=True).image_1920 = data

    def _import_product_metafields(self, product_data, instance, include_variants=True,
                                   product_defs=None, variant_defs=None):
        """Store all metafields for the product (and variants), merging the
        store-wide definitions with the actual values so that every defined
        field appears — empty ones marked "Not configured" in the UI."""
        self.ensure_one()
        Metafield = self.env['shopify.product.metafield']
        # Clear previous metafields for a clean re-sync.
        self.shopify_metafield_ids.unlink()

        product_id = product_data['id']
        prod_values = self._fetch_metafields(instance, f"products/{product_id}")
        merged_product = self._merge_defs_and_values(product_defs or [], prod_values)
        for row in merged_product:
            vals = self._metafield_vals(row, instance, owner_type='product')
            vals['product_tmpl_id'] = self.id
            Metafield.create(vals)
        # Surface selected metafields as dedicated product fields (General Info).
        self._apply_metafield_fields(merged_product)

        if include_variants:
            for variant in self.product_variant_ids:
                if not variant.shopify_variant_id:
                    continue
                variant.shopify_metafield_ids.unlink()
                var_values = self._fetch_metafields(instance, f"variants/{variant.shopify_variant_id}")
                for row in self._merge_defs_and_values(variant_defs or [], var_values):
                    vals = self._metafield_vals(row, instance, owner_type='variant')
                    vals['product_variant_id'] = variant.id
                    vals['product_tmpl_id'] = self.id
                    Metafield.create(vals)

    def _apply_metafield_fields(self, merged_metafields):
        """Fill the dedicated product fields (Brand, MPN, etc.) from the
        matching Shopify metafield, matched by definition label."""
        self.ensure_one()
        by_label = {}
        for row in merged_metafields:
            label = (row.get('name') or '').strip().lower()
            value = row.get('value')
            if label and value not in (None, ''):
                by_label[label] = value
        updates = {}
        for fname, mlabel in SHOPIFY_METAFIELD_FIELD_MAP.items():
            value = by_label.get(mlabel.strip().lower())
            if value is None:
                continue
            if not isinstance(value, str):
                value = json.dumps(value, default=str)
            updates[fname] = value
        if updates:
            self.with_context(shopify_sync_skip=True).write(updates)
            _logger.debug('Applied %s metafield fields to product %s', len(updates), self.id)

    @staticmethod
    def _merge_defs_and_values(definitions, values):
        """Combine metafield definitions (all defined fields) with the actual
        values. Returns a list of dicts with: namespace, key, name, type,
        value (None when empty), description, shopify_metafield_id."""
        by_key = {(v.get('namespace'), v.get('key')): v for v in values}
        merged = []
        seen = set()
        for d in definitions:
            k = (d.get('namespace'), d.get('key'))
            seen.add(k)
            v = by_key.get(k)
            merged.append({
                'namespace': d.get('namespace'),
                'key': d.get('key'),
                'name': d.get('name') or d.get('key'),
                'type': (v.get('type') if v else None) or d.get('type'),
                'value': v.get('value') if v else None,
                'description': d.get('description'),
                'shopify_metafield_id': v.get('id') if v else None,
            })
        # Include any values that have no matching definition (e.g. app/category).
        for k, v in by_key.items():
            if k in seen:
                continue
            merged.append({
                'namespace': v.get('namespace'),
                'key': v.get('key'),
                'name': v.get('key'),
                'type': v.get('type'),
                'value': v.get('value'),
                'description': v.get('description'),
                'shopify_metafield_id': v.get('id'),
            })
        return merged

    def _fetch_metafields(self, instance, owner_path):
        """GET metafields for an owner resource path, e.g. 'products/123'."""
        url = f"{instance._get_base_url()}/{owner_path}/metafields.json"
        try:
            response = requests.get(url, headers=instance._get_headers(),
                                    params={'limit': 250}, timeout=30, verify=certifi.where())
            if response.status_code == 200:
                return response.json().get('metafields', [])
            _logger.warning('Metafields fetch %s failed: %s', owner_path, response.status_code)
        except Exception as e:
            _logger.warning('Metafields fetch %s error: %s', owner_path, e)
        return []

    def _metafield_vals(self, mf, instance, owner_type='product'):
        value = mf.get('value')
        if value is not None and not isinstance(value, str):
            value = json.dumps(value, default=str)
        return {
            'shopify_metafield_id': str(mf.get('shopify_metafield_id') or ''),
            'label': mf.get('name') or mf.get('key') or '',
            'namespace': mf.get('namespace') or '',
            'key': mf.get('key') or '',
            'type': mf.get('type') or '',
            'value': value or False,
            'description': mf.get('description') or '',
            'owner_type': owner_type,
            'shopify_instance_id': instance.id,
        }

    # ─────────────────────────────────────────────────────────────────────────
    # Odoo → Shopify auto-sync on product create / edit
    # ─────────────────────────────────────────────────────────────────────────

    @api.model_create_multi
    def create(self, vals_list):
        products = super().create(vals_list)
        if self.env.context.get('shopify_sync_skip'):
            return products
        for product in products:
            # Keep the General-Information attribute fields and the Shopify-tab
            # metafield rows in sync from the very first save.
            try:
                product._sync_attribute_fields_to_metafields()
            except Exception as e:
                _logger.warning(f'Attribute→metafield mirror failed for {product.name}: {e}')
            # Only push when a Shopify instance is assigned and not already
            # linked (prevents duplicate on import)
            if product.shopify_instance_id and not product.shopify_product_id:
                try:
                    # Full push: variants, images and metafields all exist now
                    # (the o2m lines were created by super().create above).
                    product.with_context(shopify_sync_skip=True).export_product_to_shopify(
                        export_images=True, export_metafields=True, export_inventory=True)
                except Exception as e:
                    _logger.warning(f'Auto-create product in Shopify failed for {product.name}: {str(e)}')
        return products

    def write(self, vals):
        result = super().write(vals)
        if self.env.context.get('shopify_sync_skip'):
            return result
        # Changed General-Information attribute fields → mirror into the
        # Shopify-tab metafield rows and push the metafields when linked.
        if any(f in vals for f in SHOPIFY_METAFIELD_FIELD_MAP):
            for product in self:
                try:
                    product._sync_attribute_fields_to_metafields()
                    if product.shopify_product_id and product.shopify_instance_id:
                        product.with_context(shopify_sync_skip=True)._export_metafields_to_shopify()
                except Exception as e:
                    _logger.warning(f'Attribute→metafield sync failed for {product.name}: {e}')
        sync_trigger_fields = {
            'name', 'description', 'list_price', 'default_code',
            'shopify_product_status', 'shopify_vendor', 'shopify_tags', 'shopify_product_type',
        }
        if any(f in vals for f in sync_trigger_fields):
            for product in self:
                if product.is_shopify_product and product.shopify_product_id and product.shopify_instance_id:
                    try:
                        product.with_context(shopify_sync_skip=True)._push_product_update_to_shopify()
                    except Exception as e:
                        _logger.warning(f'Auto-sync product to Shopify failed for {product.name}: {str(e)}')
        return result

    def _build_shopify_product_payload(self):
        """Build the REST API product dict (create or update)."""
        self.ensure_one()
        has_variants = len(self.product_variant_ids) > 1

        payload = {
            'product': {
                'title': self.name,
                'body_html': self.description or '',
                'vendor': self.shopify_vendor or 'Odoo',
                'product_type': self.shopify_product_type or '',
                'tags': self.shopify_tags or '',
                'status': self.shopify_product_status or 'active',
            }
        }

        if has_variants:
            payload['product']['options'] = [
                {
                    'name': attr_line.attribute_id.name,
                    'values': [v.name for v in attr_line.value_ids],
                }
                for attr_line in self.attribute_line_ids
            ]
            variants = []
            for variant in self.product_variant_ids:
                v = {
                    'price': str(variant.lst_price),
                    'sku': variant.default_code or '',
                    'inventory_management': 'shopify',
                    'option1': variant.product_template_attribute_value_ids[0].name
                    if variant.product_template_attribute_value_ids else 'Default',
                }
                if len(variant.product_template_attribute_value_ids) > 1:
                    v['option2'] = variant.product_template_attribute_value_ids[1].name
                if len(variant.product_template_attribute_value_ids) > 2:
                    v['option3'] = variant.product_template_attribute_value_ids[2].name
                if variant.shopify_variant_id:
                    v['id'] = int(variant.shopify_variant_id)
                variants.append(v)
            payload['product']['variants'] = variants
        else:
            variant = self.product_variant_ids[:1]
            v = {
                'price': str(self.list_price),
                'sku': self.default_code or '',
                'inventory_management': 'shopify',
                'option1': 'Default Title',
            }
            if variant and variant.shopify_variant_id:
                v['id'] = int(variant.shopify_variant_id)
            payload['product']['options'] = [{'name': 'Title', 'values': ['Default Title']}]
            payload['product']['variants'] = [v]

        return payload

    def _create_product_in_shopify(self):
        """Create this product in Shopify and store the returned IDs on the record."""
        self.ensure_one()
        instance = self.shopify_instance_id
        if not instance:
            return

        payload = self._build_shopify_product_payload()
        url = f"{instance._get_base_url()}/products.json"
        response = requests.post(
            url,
            headers=instance._get_headers(),
            json=payload,
            timeout=30,
            verify=certifi.where(),
        )

        if response.status_code == 201:
            result = response.json().get('product', {})
            self.with_context(shopify_sync_skip=True).write({
                'shopify_product_id': str(result['id']),
                'is_shopify_product': True,
                'shopify_created_at': self._parse_shopify_datetime(result.get('created_at')),
                'shopify_updated_at': self._parse_shopify_datetime(result.get('updated_at')),
            })
            # Store variant IDs back on each product variant
            shopify_variants = result.get('variants', [])
            odoo_variants = list(self.product_variant_ids)
            for sv, ov in zip(shopify_variants, odoo_variants):
                ov.with_context(shopify_sync_skip=True).write({
                    'shopify_variant_id': str(sv['id']),
                    'shopify_inventory_item_id': str(sv.get('inventory_item_id', '')),
                })
            _logger.info(f'Product "{self.name}" created in Shopify with ID {result["id"]}')
        else:
            _logger.warning(
                f'Failed to create product "{self.name}" in Shopify: '
                f'{response.status_code} - {response.text}'
            )

    def _push_product_update_to_shopify(self):
        """Push field changes to Shopify for an already-linked product."""
        self.ensure_one()
        instance = self.shopify_instance_id
        if not instance or not self.shopify_product_id:
            return

        payload = self._build_shopify_product_payload()
        url = f"{instance._get_base_url()}/products/{self.shopify_product_id}.json"
        response = requests.put(
            url,
            headers=instance._get_headers(),
            json=payload,
            timeout=30,
            verify=certifi.where(),
        )

        if response.status_code == 200:
            result = response.json().get('product', {})
            updated_at = self._parse_shopify_datetime(result.get('updated_at'))
            self.with_context(shopify_sync_skip=True).write(
                {'shopify_updated_at': updated_at or fields.Datetime.now()}
            )
            _logger.info(f'Product "{self.name}" updated in Shopify successfully')
        else:
            _logger.warning(
                f'Failed to update product "{self.name}" in Shopify: '
                f'{response.status_code} - {response.text}'
            )

    def action_open_shopify_sync_wizard(self):
        """Open the 'Sync to Shopify' wizard for the selected product(s)."""
        return {
            'name': _('Sync to Shopify'),
            'type': 'ir.actions.act_window',
            'res_model': 'shopify.product.sync.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'active_model': 'product.template',
                'active_ids': self.ids,
                'active_id': self.id if len(self) == 1 else False,
            },
        }

    def export_product_to_shopify(self, export_images=False, export_metafields=True,
                                  export_inventory=True):
        """Export a single product to Shopify (create or update) with all its
        details: variants (price/sku/barcode/compare-at/policy), options,
        images, product + variant metafields and available quantity.

        Args:
            export_images: upload product images to Shopify (replace-all).
            export_metafields: upsert product + variant metafields in Shopify.
            export_inventory: push each variant's available qty (free_qty) to Shopify.
        """
        self.ensure_one()

        if not self.shopify_instance_id:
            raise UserError(_('Please select a Shopify instance first'))

        instance = self.shopify_instance_id

        try:
            # Check if product has variants
            has_variants = len(self.product_variant_ids) > 1

            product_data = {
                'product': {
                    'title': self.name,
                    'body_html': self.description or '',
                    'vendor': self.shopify_vendor or 'Odoo',
                    'product_type': self.shopify_product_type or '',
                    'tags': self.shopify_tags or '',
                    'status': self.shopify_product_status or 'active',
                }
            }

            # Add options (required by Shopify even for products without variants)
            if has_variants and self.attribute_line_ids:
                # Variants defined by Odoo attributes → map options 1:1
                product_data['product']['options'] = []
                for attribute_line in self.attribute_line_ids:
                    product_data['product']['options'].append({
                        'name': attribute_line.attribute_id.name,
                        'values': [v.name for v in attribute_line.value_ids]
                    })

                product_data['product']['variants'] = []
                for variant in self.product_variant_ids:
                    variant_data = {
                        'price': str(variant.lst_price),
                        'sku': variant.default_code or '',
                        'inventory_management': 'shopify',
                        'option1': variant.product_template_attribute_value_ids[0].name if len(variant.product_template_attribute_value_ids) > 0 else 'Default',
                    }
                    if len(variant.product_template_attribute_value_ids) > 1:
                        variant_data['option2'] = variant.product_template_attribute_value_ids[1].name
                    if len(variant.product_template_attribute_value_ids) > 2:
                        variant_data['option3'] = variant.product_template_attribute_value_ids[2].name
                    self._fill_variant_export_fields(variant_data, variant)
                    product_data['product']['variants'].append(variant_data)
            elif has_variants:
                # Multiple variants but NO Odoo attributes (added manually in the
                # grid). Use each variant's title/SKU as a distinct "Title" option
                # so Shopify creates separate variants instead of collapsing them.
                product_data['product']['variants'] = []
                option_values = []
                for idx, variant in enumerate(self.product_variant_ids, start=1):
                    title = variant.shopify_variant_title or variant.default_code or f'Variant {idx}'
                    # Ensure uniqueness (Shopify rejects duplicate option values)
                    if title in option_values:
                        title = f'{title} ({idx})'
                    option_values.append(title)
                    variant_data = {
                        'price': str(variant.lst_price),
                        'sku': variant.default_code or '',
                        'inventory_management': 'shopify',
                        'option1': title,
                    }
                    self._fill_variant_export_fields(variant_data, variant)
                    product_data['product']['variants'].append(variant_data)
                product_data['product']['options'] = [{'name': 'Title', 'values': option_values}]
            else:
                # Single variant product - add default option
                variant = self.product_variant_ids[:1]
                product_data['product']['options'] = [{'name': 'Title', 'values': ['Default Title']}]
                single = {
                    'price': str(self.list_price),
                    'sku': self.default_code or '',
                    'inventory_management': 'shopify',
                    'option1': 'Default Title',
                }
                if variant:
                    self._fill_variant_export_fields(single, variant)
                product_data['product']['variants'] = [single]

            headers = instance._get_headers()

            if self.shopify_product_id:
                # Update existing product
                url = f"{instance._get_base_url()}/products/{self.shopify_product_id}.json"
                response = requests.put(url, headers=headers, json=product_data, timeout=30, verify=certifi.where())
            else:
                # Create new product
                url = f"{instance._get_base_url()}/products.json"
                response = requests.post(url, headers=headers, json=product_data, timeout=30, verify=certifi.where())

            if response.status_code in [200, 201]:
                result_data = response.json().get('product', {})

                # Parse Shopify datetime (ISO 8601 format with timezone) and convert to naive datetime
                updated_at = result_data.get('updated_at')
                if updated_at:
                    try:
                        # Parse the datetime with timezone info
                        parsed_dt = date_parser.parse(updated_at)
                        # Convert to naive datetime (remove timezone info) for Odoo
                        updated_at = parsed_dt.replace(tzinfo=None)
                    except Exception as e:
                        _logger.warning(f'Failed to parse updated_at: {updated_at}, error: {str(e)}')
                        updated_at = fields.Datetime.now()
                else:
                    updated_at = fields.Datetime.now()

                self.with_context(shopify_sync_skip=True).write({
                    'shopify_product_id': str(result_data['id']),
                    'is_shopify_product': True,
                    'shopify_updated_at': updated_at,
                })
                # Store variant IDs returned by Shopify back on Odoo variants.
                shopify_variants = result_data.get('variants', [])
                for sv, ov in zip(shopify_variants, self.product_variant_ids):
                    ov.with_context(shopify_sync_skip=True).write({
                        'shopify_variant_id': str(sv['id']),
                        'shopify_inventory_item_id': str(sv.get('inventory_item_id', '')),
                    })
                # Upload images (replace-all semantics avoids duplicates on re-sync).
                if export_images:
                    try:
                        self._export_images_to_shopify(instance)
                    except Exception as img_err:
                        _logger.warning('Image export to Shopify failed for %s: %s', self.name, img_err)
                # Push product + variant metafields (upsert, create or update).
                if export_metafields:
                    try:
                        self._export_metafields_to_shopify(instance)
                    except Exception as mf_err:
                        _logger.warning('Metafield export to Shopify failed for %s: %s', self.name, mf_err)
                # Push available quantity per variant (entered Shopify Qty or free_qty).
                push_warnings = []
                if export_inventory:
                    try:
                        push_warnings += self._export_inventory_to_shopify(instance) or []
                    except Exception as inv_err:
                        _logger.warning('Inventory export to Shopify failed for %s: %s', self.name, inv_err)
                # Sync selected collections (custom) to Shopify.
                if self.shopify_collection_ids:
                    try:
                        self._export_collections_to_shopify(instance)
                    except Exception as col_err:
                        _logger.warning('Collections export to Shopify failed for %s: %s', self.name, col_err)
                message = _('Product exported to Shopify successfully')
                if push_warnings:
                    message += '\n\n' + '\n'.join(push_warnings)
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': _('Success') if not push_warnings else _('Exported with warnings'),
                        'message': message,
                        'type': 'success' if not push_warnings else 'warning',
                        'sticky': bool(push_warnings),
                    }
                }
            else:
                raise UserError(_('Failed to export product: %s - %s') % (response.status_code, response.text))

        except Exception as e:
            _logger.error(f'Error exporting product: {str(e)}')
            raise UserError(_('Error exporting product: %s') % str(e))

    def _export_images_to_shopify(self, instance=None):
        """Upload this product's images to Shopify as base64 attachments.
        Sends the main image first, then any extra Shopify media images."""
        self.ensure_one()
        instance = instance or self.shopify_instance_id
        if not instance or not self.shopify_product_id:
            return
        # Collect images: main template image first, then the media gallery.
        image_datas = []
        if self.image_1920:
            image_datas.append(self.image_1920)
        for media in self.shopify_image_ids:
            if media.image_1920:
                image_datas.append(media.image_1920)
        if not image_datas:
            return

        base = instance._get_base_url()
        # Replace-all: delete existing Shopify images first so re-syncs don't duplicate.
        try:
            existing = requests.get(
                f"{base}/products/{self.shopify_product_id}/images.json",
                headers=instance._get_headers(), timeout=30, verify=certifi.where(),
            )
            if existing.status_code == 200:
                for img in existing.json().get('images', []):
                    requests.delete(
                        f"{base}/products/{self.shopify_product_id}/images/{img['id']}.json",
                        headers=instance._get_headers(), timeout=30, verify=certifi.where(),
                    )
        except Exception as e:
            _logger.warning('Could not clear existing Shopify images for %s: %s', self.name, e)

        url = f"{base}/products/{self.shopify_product_id}/images.json"
        for position, data in enumerate(image_datas, start=1):
            attachment = data.decode() if isinstance(data, bytes) else data
            payload = {'image': {'attachment': attachment, 'position': position}}
            try:
                resp = requests.post(url, headers=instance._get_headers(), json=payload,
                                     timeout=60, verify=certifi.where())
                if resp.status_code not in (200, 201):
                    _logger.warning('Image upload failed (%s): %s', resp.status_code, resp.text[:200])
            except Exception as e:
                _logger.warning('Image upload error for %s: %s', self.name, e)

        # Variant-specific images: upload each variant's OWN image (not the
        # template fallback) and attach it to that variant in Shopify.
        for variant in self.product_variant_ids:
            vimg = variant.image_variant_1920
            if not vimg or not variant.shopify_variant_id:
                continue
            attachment = vimg.decode() if isinstance(vimg, bytes) else vimg
            payload = {'image': {
                'attachment': attachment,
                'variant_ids': [int(variant.shopify_variant_id)],
            }}
            try:
                resp = requests.post(url, headers=instance._get_headers(), json=payload,
                                     timeout=60, verify=certifi.where())
                if resp.status_code in (200, 201):
                    _logger.info('Variant image uploaded for %s', variant.display_name)
                else:
                    _logger.warning('Variant image upload failed for %s (%s): %s',
                                    variant.display_name, resp.status_code, resp.text[:200])
            except Exception as e:
                _logger.warning('Variant image upload error for %s: %s', variant.display_name, e)

    @staticmethod
    def _fill_variant_export_fields(variant_data, variant):
        """Add the extra variant fields (barcode, compare-at, policy, taxable)
        to a Shopify variant payload dict."""
        if variant.shopify_variant_id:
            variant_data['id'] = int(variant.shopify_variant_id)
        if variant.barcode:
            variant_data['barcode'] = variant.barcode
        if variant.shopify_compare_at_price:
            variant_data['compare_at_price'] = str(variant.shopify_compare_at_price)
        if variant.shopify_inventory_policy:
            variant_data['inventory_policy'] = variant.shopify_inventory_policy
        variant_data['taxable'] = bool(variant.shopify_taxable)

    @staticmethod
    def _prepare_metafield_value(mtype, value):
        """Coerce an Odoo-entered value into the format Shopify expects for the
        metafield's type. Returns None when the value cannot be made valid."""
        mtype = (mtype or '').strip()
        val = value if isinstance(value, str) else str(value)
        val = val.strip()
        if not val:
            return None
        if not mtype:
            return val
        # List types must contain a JSON array. NOTE: check this BEFORE the
        # single-reference test — 'list.metaobject_reference' also ends with
        # '_reference' but its value is an ARRAY of GIDs, not a bare GID.
        if mtype.startswith('list.'):
            inner = mtype[5:]
            is_ref = inner.endswith('_reference')
            try:
                parsed = json.loads(val)
            except Exception:
                parsed = None
            if isinstance(parsed, list):
                items = [str(i) for i in parsed]
                if is_ref:
                    gids = [i for i in items if i.startswith('gid://shopify/')]
                    return json.dumps(gids) if gids else None
                return json.dumps(items)
            if is_ref:
                return json.dumps([val]) if val.startswith('gid://shopify/') else None
            # plain text: split on commas into list items ("0-2, 3-5" → 2 items)
            items = [s.strip() for s in val.split(',') if s.strip()] or [val]
            return json.dumps(items)
        # Single reference types (metaobject/file/product/...): Shopify only
        # accepts GIDs — a plain text value can never be valid.
        if mtype.endswith('_reference'):
            return val if val.startswith('gid://shopify/') else None
        if mtype == 'json':
            try:
                json.loads(val)
                return val
            except Exception:
                return json.dumps(val)
        if mtype in ('number_integer', 'number_decimal'):
            try:
                float(val)
                return val
            except ValueError:
                return None
        if mtype == 'boolean':
            return 'true' if val.lower() in ('1', 'true', 'yes', 'y') else 'false'
        if mtype == 'rating':
            try:
                json.loads(val)
                return val
            except Exception:
                try:
                    float(val)
                    return json.dumps({'scale_min': '0.0', 'scale_max': '5.0', 'value': val})
                except ValueError:
                    return None
        return val

    def _export_metafields_to_shopify(self, instance=None):
        """Upsert product and variant metafields in Shopify via metafieldsSet.

        Values are coerced to match the metafield type, other apps' reserved
        namespaces (app--*) are skipped, and when a batch is rejected each
        metafield is retried individually so one bad value never blocks the rest.
        """
        self.ensure_one()
        instance = instance or self.shopify_instance_id
        if not instance or not self.shopify_product_id:
            return

        Definition = self.env['shopify.metafield.definition']

        def _resolve_type(mf, owner_type, ns):
            """The store DEFINITION's type wins — a stale/empty type stored on
            the row causes 'Value must be an array'-style rejections."""
            if mf.definition_id and mf.definition_id.type:
                return mf.definition_id.type
            domain = [('owner_type', '=', owner_type), ('namespace', '=', ns), ('key', '=', mf.key)]
            definition = Definition.search(domain + [('shopify_instance_id', '=', instance.id)], limit=1) \
                or Definition.search(domain, limit=1)
            return definition.type or mf.type or 'single_line_text_field'

        def _collect(owner_gid, metafields, owner_type):
            rows = []
            for mf in metafields:
                if not mf.key or mf.value in (None, '', False):
                    continue
                ns = mf.namespace or 'custom'
                if ns.startswith('app--'):
                    # Reserved namespace of another Shopify app — our token
                    # cannot write there; skip instead of failing the batch.
                    _logger.info('[metafieldsSet] skipping app-reserved metafield %s.%s', ns, mf.key)
                    continue
                mtype = _resolve_type(mf, owner_type, ns)
                value = self._prepare_metafield_value(mtype, mf.value)
                if value is None:
                    _logger.warning(
                        '[metafieldsSet] skipping %s.%s — value %r cannot be expressed as type %s '
                        '(reference types need Shopify GIDs)', ns, mf.key, mf.value, mtype)
                    continue
                rows.append({
                    'ownerId': owner_gid,
                    'namespace': ns,
                    'key': mf.key,
                    'type': mtype,
                    'value': value,
                })
            return rows

        inputs = _collect(f"gid://shopify/Product/{self.shopify_product_id}",
                          self.shopify_metafield_ids, 'product')
        for variant in self.product_variant_ids:
            if not variant.shopify_variant_id:
                continue
            inputs += _collect(f"gid://shopify/ProductVariant/{variant.shopify_variant_id}",
                               variant.shopify_metafield_ids, 'variant')

        if not inputs:
            return

        mutation = """
        mutation metafieldsSet($metafields: [MetafieldsSetInput!]!) {
          metafieldsSet(metafields: $metafields) {
            metafields { id namespace key }
            userErrors { field message code }
          }
        }
        """

        def _send(batch):
            data = instance._execute_graphql(mutation, {'metafields': batch})
            return data.get('metafieldsSet', {}).get('userErrors', [])

        # Shopify allows up to 25 metafields per metafieldsSet call.
        for i in range(0, len(inputs), 25):
            batch = inputs[i:i + 25]
            try:
                errs = _send(batch)
            except Exception as e:
                _logger.warning('[metafieldsSet] call failed for %s: %s', self.name, e)
                errs = True
            if not errs:
                _logger.info('[metafieldsSet] pushed %s metafields for %s', len(batch), self.name)
                continue
            # Batch rejected — retry one by one so good values still land.
            _logger.warning('[metafieldsSet] batch rejected for %s (%s) — retrying individually',
                            self.name, errs if errs is not True else 'exception')
            ok = 0
            for row in batch:
                try:
                    single_errs = _send([row])
                    if single_errs:
                        _logger.warning('[metafieldsSet] %s.%s failed: %s',
                                        row['namespace'], row['key'], single_errs)
                    else:
                        ok += 1
                except Exception as e:
                    _logger.warning('[metafieldsSet] %s.%s failed: %s', row['namespace'], row['key'], e)
            _logger.info('[metafieldsSet] individual retry: %s/%s pushed for %s', ok, len(batch), self.name)

    def _export_inventory_to_shopify(self, instance=None):
        """Push each variant's quantity to Shopify. Uses the manually entered
        "Shopify Qty" when set, else the real available stock (free_qty).

        Returns a list of human-readable warning strings (empty when all OK).
        """
        self.ensure_one()
        warnings = []
        instance = instance or self.shopify_instance_id
        if not instance or not self.shopify_product_id:
            return warnings
        inv_sync = self.env['shopify.inventory.sync']
        try:
            location_id = inv_sync._get_shopify_location_id(instance)
        except Exception as e:
            _logger.warning('No Shopify location for inventory push (%s): %s', self.name, e)
            return [_('Quantity not synced: no Shopify location found.')]
        for variant in self.product_variant_ids:
            if not (variant.shopify_variant_id or variant.shopify_inventory_item_id):
                continue
            # Prefer the manually entered "Shopify Qty"; fall back to the real
            # available stock (on hand - reserved) when it is not set.
            qty = variant.shopify_inventory_quantity or variant.free_qty
            try:
                inv_sync._set_variant_inventory(instance, variant, qty, location_id)
            except Exception as e:
                if 'write_inventory' in str(e):
                    msg = _('Quantity NOT synced: the Shopify app is missing the '
                            '"write_inventory" permission. Approve that scope in your '
                            'Shopify app settings, then click Generate Access Token and sync again.')
                    _logger.warning('%s (%s)', msg, variant.display_name)
                    if msg not in warnings:
                        warnings.append(msg)
                else:
                    _logger.warning('Inventory push failed for %s: %s', variant.display_name, e)
                    warnings.append(_('Quantity failed for %s.') % variant.display_name)
        return warnings

    def _export_collections_to_shopify(self, instance=None):
        """Sync the product's selected collections to Shopify via /collects.

        Custom collections: added/removed so Shopify matches the Odoo selection.
        Smart collections: rule-based in Shopify, cannot be assigned — skipped.
        """
        self.ensure_one()
        instance = instance or self.shopify_instance_id
        if not instance or not self.shopify_product_id:
            return
        base = instance._get_base_url()
        headers = instance._get_headers()

        selected = self.shopify_collection_ids.filtered(
            lambda c: c.shopify_instance_id == instance and c.shopify_collection_id)
        smart = selected.filtered(lambda c: c.collection_type == 'smart')
        if smart:
            _logger.info('[collections] smart collections are assigned automatically by '
                         'Shopify rules, skipping: %s', ', '.join(smart.mapped('name')))
        wanted = {c.shopify_collection_id for c in selected if c.collection_type == 'custom'}

        # Current custom-collection membership (collects)
        current = {}
        try:
            r = requests.get(f"{base}/collects.json", headers=headers,
                             params={'product_id': self.shopify_product_id, 'limit': 250},
                             timeout=30, verify=certifi.where())
            if r.status_code == 200:
                current = {str(c['collection_id']): c['id'] for c in r.json().get('collects', [])}
        except Exception as e:
            _logger.warning('[collections] could not fetch collects for %s: %s', self.name, e)

        # Add missing memberships
        for cid in wanted - set(current):
            try:
                resp = requests.post(
                    f"{base}/collects.json", headers=headers,
                    json={'collect': {'product_id': int(self.shopify_product_id),
                                      'collection_id': int(cid)}},
                    timeout=30, verify=certifi.where())
                if resp.status_code in (200, 201):
                    _logger.info('[collections] added %s to collection %s', self.name, cid)
                else:
                    _logger.warning('[collections] add to %s failed: %s %s',
                                    cid, resp.status_code, resp.text[:150])
            except Exception as e:
                _logger.warning('[collections] add to %s error: %s', cid, e)

        # Remove memberships deselected in Odoo
        for cid, collect_id in current.items():
            if cid not in wanted:
                try:
                    requests.delete(f"{base}/collects/{collect_id}.json", headers=headers,
                                    timeout=30, verify=certifi.where())
                    _logger.info('[collections] removed %s from collection %s', self.name, cid)
                except Exception as e:
                    _logger.warning('[collections] remove from %s error: %s', cid, e)


