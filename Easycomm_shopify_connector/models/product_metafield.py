# -*- coding: utf-8 -*-

import json

from odoo import api, fields, models, _


class ShopifyMetaobject(models.Model):
    """One selectable entry of a Shopify metaobject (e.g. one 'Diaper Size'
    option). Metaobject-reference metafields pick their values from these."""
    _name = 'shopify.metaobject'
    _description = 'Shopify Metaobject Entry'
    _order = 'metaobject_type, name'

    name = fields.Char('Name', required=True)
    handle = fields.Char('Handle')
    shopify_gid = fields.Char('Shopify GID', required=True, index=True,
        help="gid://shopify/Metaobject/... — the value Shopify stores in the metafield.")
    metaobject_type = fields.Char('Type', index=True,
        help="The metaobject definition type handle, e.g. 'diaper_size'.")
    definition_gid = fields.Char('Metaobject Definition GID', index=True,
        help="gid://shopify/MetaobjectDefinition/... used to match metafield definitions.")
    shopify_instance_id = fields.Many2one('shopify.instance', string='Shopify Instance',
                                          ondelete='cascade', index=True)

    _uniq_gid_instance = models.Constraint(
        'unique(shopify_instance_id, shopify_gid)',
        'This metaobject entry already exists for this instance.')


class ShopifyMetafieldDefinition(models.Model):
    """The metafield definitions available in a Shopify store, used to offer a
    dropdown when entering metafield values on a product/variant."""
    _name = 'shopify.metafield.definition'
    _description = 'Shopify Metafield Definition'
    _order = 'owner_type, namespace, key'
    _rec_name = 'display_name'

    name = fields.Char('Label', required=True)  # human label e.g. "Brand"
    namespace = fields.Char('Namespace')
    key = fields.Char('Key')
    type = fields.Char('Type')
    description = fields.Char('Description')
    metaobject_definition_gid = fields.Char('Metaobject Definition GID',
        help="For metaobject-reference metafields: which metaobject provides the selectable values.")
    owner_type = fields.Selection([
        ('product', 'Product'),
        ('variant', 'Variant'),
    ], string='Owner Type', default='product', required=True)
    shopify_instance_id = fields.Many2one('shopify.instance', string='Shopify Instance',
                                          ondelete='cascade', index=True)
    display_name = fields.Char('Name', compute='_compute_display_name', store=True)

    _uniq_def = models.Constraint(
        'unique(shopify_instance_id, owner_type, namespace, key)',
        'This metafield definition already exists for this instance.')

    @api.depends('name', 'namespace', 'key')
    def _compute_display_name(self):
        for rec in self:
            tech = f"{rec.namespace or ''}.{rec.key or ''}".strip('.')
            rec.display_name = f"{rec.name} ({tech})" if rec.name else tech


class ShopifyProductMetafield(models.Model):
    _name = 'shopify.product.metafield'
    _description = 'Shopify Product/Variant Metafield'
    _order = 'namespace, key'

    name = fields.Char('Technical Key', compute='_compute_name', store=True)
    label = fields.Char('Field', help="Human-readable metafield label from its Shopify definition.")
    definition_id = fields.Many2one(
        'shopify.metafield.definition', string='Metafield',
        help="Pick a metafield defined in your Shopify store; its namespace, key and type "
             "are filled in automatically. Then enter the value.")
    metaobject_definition_gid = fields.Char(
        related='definition_id.metaobject_definition_gid', readonly=True)
    metaobject_ids = fields.Many2many(
        'shopify.metaobject', string='Select Values',
        help="For metaobject metafields (like Diaper Size): pick the predefined "
             "Shopify entries — they are synced as the metafield value.")
    collection_ids = fields.Many2many(
        'shopify.collection', 'shopify_metafield_collection_rel', 'metafield_id', 'collection_id',
        string='Select Collections',
        help="For collection-reference metafields (like Age): pick Shopify collections.")
    ref_product_ids = fields.Many2many(
        'product.template', 'shopify_metafield_product_rel', 'metafield_id', 'product_tmpl_id',
        string='Select Products',
        help="For product-reference metafields (like Product Bought Together): pick Shopify products.")
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

    @api.onchange('definition_id')
    def _onchange_definition_id(self):
        """Autofill namespace/key/type/label from the chosen definition (UI)."""
        for rec in self:
            if rec.definition_id:
                rec.namespace = rec.definition_id.namespace
                rec.key = rec.definition_id.key
                rec.type = rec.definition_id.type
                rec.label = rec.definition_id.name
                if rec.definition_id.owner_type:
                    rec.owner_type = rec.definition_id.owner_type

    @api.model
    def _apply_definition_vals(self, vals):
        """Force namespace/key/type/label from definition_id on the server, so
        they are stored even though those columns are readonly in the form
        (Odoo does not send readonly field values back on save)."""
        if vals.get('definition_id'):
            d = self.env['shopify.metafield.definition'].browse(vals['definition_id'])
            if d.exists():
                vals['namespace'] = d.namespace or ''
                vals['key'] = d.key or ''
                vals['type'] = d.type or ''
                vals['label'] = d.name or ''
                vals['owner_type'] = d.owner_type or vals.get('owner_type') or 'product'
        return vals

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            self._apply_definition_vals(vals)
        records = super().create(vals_list)
        records._sync_value_from_metaobjects()
        records._mirror_to_template_fields()
        return records

    _SELECTION_FIELDS = ('metaobject_ids', 'collection_ids', 'ref_product_ids')

    def write(self, vals):
        if 'definition_id' in vals:
            self._apply_definition_vals(vals)
        result = super().write(vals)
        touched_selection = any(f in vals for f in self._SELECTION_FIELDS)
        if touched_selection or 'definition_id' in vals:
            self._sync_value_from_metaobjects()
        if 'value' in vals or 'definition_id' in vals or touched_selection:
            self._mirror_to_template_fields()
        return result

    def _mirror_to_template_fields(self):
        """Keep the General-Information 'Shopify Attributes' fields showing the
        same value as this metafield row (matched by label)."""
        if self.env.context.get('shopify_sync_skip'):
            return
        from .product import SHOPIFY_METAFIELD_FIELD_MAP
        label_to_field = {label.strip().lower(): fname
                          for fname, label in SHOPIFY_METAFIELD_FIELD_MAP.items()}
        for rec in self:
            if rec.owner_type != 'product' or not rec.product_tmpl_id or not rec.label:
                continue
            fname = label_to_field.get(rec.label.strip().lower())
            if not fname:
                continue
            val = rec.value or False
            if rec.metaobject_ids:
                # Show the picked entry names ("4-8 Years"), not raw GIDs.
                val = ', '.join(rec.metaobject_ids.mapped('name'))
            elif rec.collection_ids:
                val = ', '.join(rec.collection_ids.mapped('name'))
            elif rec.ref_product_ids:
                val = ', '.join(rec.ref_product_ids.mapped('name'))
            elif isinstance(val, str) and ('gid://shopify/' in val):
                continue
            if getattr(rec.product_tmpl_id, fname) != val:
                rec.product_tmpl_id.with_context(shopify_sync_skip=True).write({fname: val})

    def _sync_value_from_metaobjects(self):
        """Encode the selected references (metaobject / collection / product)
        into `value` in the exact format Shopify expects: a single GID, or a
        JSON array of GIDs for `list.*` types."""
        for rec in self:
            gids = []
            if rec.metaobject_ids:
                gids = [m.shopify_gid for m in rec.metaobject_ids if m.shopify_gid]
            elif rec.collection_ids:
                gids = [f"gid://shopify/Collection/{c.shopify_collection_id}"
                        for c in rec.collection_ids if c.shopify_collection_id]
            elif rec.ref_product_ids:
                gids = [f"gid://shopify/Product/{p.shopify_product_id}"
                        for p in rec.ref_product_ids if p.shopify_product_id]
            if not gids:
                continue
            if (rec.type or '').startswith('list.'):
                new_value = json.dumps(gids)
            else:
                new_value = gids[0]
            if rec.value != new_value:
                super(ShopifyProductMetafield, rec).write({'value': new_value})

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
