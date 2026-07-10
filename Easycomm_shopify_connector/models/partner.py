# -*- coding: utf-8 -*-

from odoo import api, fields, models, _
from odoo.exceptions import UserError
import json
import logging
import requests
import certifi
from dateutil import parser as date_parser

_logger = logging.getLogger(__name__)


class ResPartner(models.Model):
    _inherit = 'res.partner'

    shopify_instance_id = fields.Many2one('shopify.instance', string='Shopify Instance', ondelete='cascade')
    shopify_customer_id = fields.Char('Shopify Customer ID', readonly=True, copy=False)
    is_shopify_customer = fields.Boolean('Is Shopify Customer', default=False, copy=False)
    shopify_email_verified = fields.Boolean('Email Verified')
    shopify_accepts_marketing = fields.Boolean('Accepts Marketing')
    shopify_orders_count = fields.Integer('Shopify Orders Count', default=0)
    shopify_total_spent = fields.Float('Total Spent on Shopify')
    shopify_state = fields.Selection([
        ('disabled', 'Disabled'),
        ('invited', 'Invited'),
        ('enabled', 'Enabled'),
        ('declined', 'Declined')
    ], string='Shopify Account State')
    shopify_created_at = fields.Datetime('Shopify Created At')
    shopify_updated_at = fields.Datetime('Shopify Updated At')
    # Full customer data captured on import
    shopify_customer_tags = fields.Char('Shopify Tags')
    shopify_note = fields.Text('Shopify Note')
    shopify_currency = fields.Char('Customer Currency')
    shopify_last_order_id = fields.Char('Last Order ID', readonly=True)
    shopify_last_order_name = fields.Char('Last Order', readonly=True)
    shopify_tax_exempt = fields.Boolean('Tax Exempt (Shopify)')
    shopify_email_marketing_state = fields.Char('Email Marketing',
        help="Email marketing consent state in Shopify (subscribed / not_subscribed / ...).")
    shopify_sms_marketing_state = fields.Char('SMS Marketing',
        help="SMS marketing consent state in Shopify.")
    shopify_address_id = fields.Char('Shopify Address ID', copy=False,
        help="Set on child contacts imported from the customer's Shopify address book.")
    shopify_raw_data = fields.Text('Shopify Raw Data', copy=False,
        help="Complete untouched JSON of this customer as received from Shopify — every key.")
    shopify_company = fields.Char(
        'Company', compute='_compute_shopify_company',
        help="Shopify store (domain) this customer was imported from / syncs to.")

    @api.depends('shopify_instance_id', 'shopify_instance_id.shop_url')
    def _compute_shopify_company(self):
        for rec in self:
            instance = rec.shopify_instance_id
            rec.shopify_company = instance._get_shop_domain() if instance else False

    @api.model
    def _get_shopify_customer_tag(self):
        """Return the 'Shopify Customer' partner tag (create it if missing)."""
        tag = self.env.ref('Easycomm_shopify_connector.partner_category_shopify',
                           raise_if_not_found=False)
        if not tag:
            Category = self.env['res.partner.category']
            tag = Category.search([('name', '=', 'Shopify Customer')], limit=1)
            if not tag:
                tag = Category.create({'name': 'Shopify Customer', 'color': 2})
        return tag

    def import_shopify_customers(self, instance_id, batch_size=20, notify_uid=None):
        """Import customers from Shopify.

        Fetches pages of up to 250 customers and processes them in sub-batches
        of `batch_size` (default 20). After every batch the records are committed
        (so they appear in Odoo one batch at a time) and a bus notification is
        sent to `notify_uid` for live progress toasts.
        """
        instance = self.env['shopify.instance'].browse(instance_id)
        if not instance:
            raise UserError(_('Shopify instance not found'))

        def _notify(title, message, msg_type='info'):
            """Push a toast to the requesting user via the Odoo bus."""
            if not notify_uid:
                return
            try:
                partner = self.env['res.users'].browse(notify_uid).partner_id
                self.env['bus.bus']._sendone(partner, 'simple_notification', {
                    'title': title,
                    'message': message,
                    'type': msg_type,
                })
                self.env.cr.commit()
            except Exception as bus_err:
                _logger.warning(f'Bus notification failed: {bus_err}')

        try:
            url = f"{instance._get_base_url()}/customers.json"
            params = {'limit': 250}
            headers = instance._get_headers()

            created_count = 0
            updated_count = 0
            total_fetched = 0
            page_info = None
            page_number = 1

            while True:
                # When using page_info, only pass page_info (Shopify API requirement)
                request_params = {'page_info': page_info} if page_info else params

                _logger.info(f'Fetching customers page {page_number}...')
                response = requests.get(
                    url, headers=headers, params=request_params,
                    timeout=60, verify=certifi.where()
                )

                if response.status_code != 200:
                    raise UserError(_('Failed to fetch customers: %s - %s') % (
                        response.status_code, response.text))

                customers = response.json().get('customers', [])
                if not customers:
                    break

                total_fetched += len(customers)
                _logger.info(
                    f'Page {page_number}: fetched {len(customers)} customers '
                    f'(total so far: {total_fetched})'
                )

                # Process and notify every `batch_size` customers
                for i in range(0, len(customers), batch_size):
                    batch = customers[i:i + batch_size]
                    b_created, b_updated = self._process_customer_batch(batch, instance)
                    created_count += b_created
                    updated_count += b_updated

                    # Commit after each micro-batch so they show up progressively
                    self.env.cr.commit()
                    _logger.info(
                        f'Batch {i // batch_size + 1}: '
                        f'created={b_created}, updated={b_updated}'
                    )

                    _notify(
                        _('Customer Import Progress'),
                        _('Fetched %s customers so far — Created: %s | Updated: %s') % (
                            total_fetched, created_count, updated_count
                        ),
                        'info',
                    )

                # Pagination
                link_header = response.headers.get('Link', '')
                if 'rel="next"' in link_header:
                    for link in link_header.split(','):
                        if 'rel="next"' in link:
                            page_info = link.split('page_info=')[1].split('>')[0]
                            break
                    page_number += 1
                else:
                    break

            _logger.info(
                f'Customer import complete — total={total_fetched}, '
                f'created={created_count}, updated={updated_count}'
            )
            instance.write({'last_customer_sync': fields.Datetime.now()})

            _notify(
                _('Customer Import Complete'),
                _('All done! Total: %s | Created: %s | Updated: %s') % (
                    total_fetched, created_count, updated_count
                ),
                'success',
            )

            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Customers Imported'),
                    'message': _('Total: %s, Created: %s, Updated: %s') % (
                        total_fetched, created_count, updated_count),
                    'type': 'success',
                }
            }

        except Exception as e:
            _logger.error(f'Error importing customers: {str(e)}')
            raise UserError(_('Error importing customers: %s') % str(e))

    def _process_customer_batch(self, customers_batch, instance):
        """Create/update a batch of customers from Shopify data."""
        created_count = 0
        updated_count = 0
        ctx = {'shopify_sync_skip': True}

        for customer_data in customers_batch:
            try:
                customer_vals = self._prepare_customer_vals(customer_data, instance)
                existing_customer = self.search([
                    ('shopify_customer_id', '=', str(customer_data['id'])),
                    ('shopify_instance_id', '=', instance.id)
                ], limit=1)

                if existing_customer:
                    existing_customer.with_context(**ctx).write(customer_vals)
                    partner = existing_customer
                    updated_count += 1
                    _logger.debug(f'Updated customer: {customer_data.get("email")}')
                else:
                    partner = self.with_context(**ctx).create(customer_vals)
                    created_count += 1
                    _logger.debug(f'Created customer: {customer_data.get("email")}')
                # Import the customer's full Shopify address book.
                partner.with_context(**ctx)._import_shopify_addresses(customer_data)
            except Exception as e:
                _logger.error(
                    f'Error processing customer {customer_data.get("id")} '
                    f'({customer_data.get("email")}): {str(e)}'
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

    def _prepare_customer_vals(self, customer_data, instance):
        """Prepare customer values from Shopify data"""
        # Get default address
        default_address = customer_data.get('default_address', {})

        # Marketing consent: modern API nests it; fall back to the legacy flag.
        email_consent = (customer_data.get('email_marketing_consent') or {})
        sms_consent = (customer_data.get('sms_marketing_consent') or {})
        accepts_marketing = customer_data.get(
            'accepts_marketing', email_consent.get('state') == 'subscribed')

        vals = {
            'name': f"{customer_data.get('first_name', '')} {customer_data.get('last_name', '')}".strip() or customer_data.get('email', 'Unknown'),
            'email': customer_data.get('email', ''),
            'phone': customer_data.get('phone', ''),
            'shopify_instance_id': instance.id,
            'shopify_customer_id': str(customer_data['id']),
            'is_shopify_customer': True,
            'shopify_email_verified': customer_data.get('verified_email', False),
            'shopify_accepts_marketing': bool(accepts_marketing),
            'shopify_orders_count': customer_data.get('orders_count', 0),
            'shopify_total_spent': float(customer_data.get('total_spent', 0.0) or 0.0),
            'shopify_state': customer_data.get('state', 'disabled'),
            'shopify_created_at': self._parse_shopify_datetime(customer_data.get('created_at')),
            'shopify_updated_at': self._parse_shopify_datetime(customer_data.get('updated_at')),
            # Everything else Shopify sends about the customer
            'shopify_customer_tags': customer_data.get('tags', ''),
            'shopify_note': customer_data.get('note') or '',
            'shopify_currency': customer_data.get('currency', ''),
            'shopify_last_order_id': str(customer_data.get('last_order_id') or ''),
            'shopify_last_order_name': customer_data.get('last_order_name') or '',
            'shopify_tax_exempt': customer_data.get('tax_exempt', False),
            'shopify_email_marketing_state': email_consent.get('state') or '',
            'shopify_sms_marketing_state': sms_consent.get('state') or '',
            'shopify_raw_data': json.dumps(customer_data, indent=2, default=str),
            'customer_rank': 1,
        }

        # Tag the contact as a Shopify customer (visible in the Tags field).
        tag = self._get_shopify_customer_tag()
        if tag:
            vals['category_id'] = [(4, tag.id)]

        # Add address information if available
        if default_address:
            vals.update({
                'street': default_address.get('address1', ''),
                'street2': default_address.get('address2', ''),
                'city': default_address.get('city', ''),
                'zip': default_address.get('zip', ''),
                'country_id': self._get_country_id(default_address.get('country_code')),
                'state_id': self._get_state_id(default_address.get('province_code'), default_address.get('country_code')),
            })

        return vals

    def _import_shopify_addresses(self, customer_data):
        """Create/update child contacts for every address in the customer's
        Shopify address book (the default address stays on the main partner)."""
        self.ensure_one()
        Partner = self.env['res.partner']
        for addr in customer_data.get('addresses', []) or []:
            addr_id = str(addr.get('id') or '')
            if not addr_id or addr.get('default'):
                continue
            name = f"{addr.get('first_name') or ''} {addr.get('last_name') or ''}".strip()
            vals = {
                'parent_id': self.id,
                'type': 'delivery',
                'name': name or addr.get('company') or self.name,
                'street': addr.get('address1') or '',
                'street2': addr.get('address2') or '',
                'city': addr.get('city') or '',
                'zip': addr.get('zip') or '',
                'phone': addr.get('phone') or '',
                'country_id': self._get_country_id(addr.get('country_code')),
                'state_id': self._get_state_id(addr.get('province_code'), addr.get('country_code')),
                'shopify_address_id': addr_id,
            }
            existing = Partner.search([
                ('parent_id', '=', self.id),
                ('shopify_address_id', '=', addr_id),
            ], limit=1)
            try:
                if existing:
                    existing.write(vals)
                else:
                    Partner.create(vals)
            except Exception as e:
                _logger.warning('Address import failed for customer %s (address %s): %s',
                                self.display_name, addr_id, e)

    def _get_country_id(self, country_code):
        """Get country ID from country code"""
        if not country_code:
            return False
        country = self.env['res.country'].search([('code', '=', country_code.upper())], limit=1)
        return country.id if country else False

    def _get_state_id(self, state_code, country_code):
        """Get state ID from state code and country code"""
        if not state_code or not country_code:
            return False
        state = self.env['res.country.state'].search([
            ('code', '=', state_code.upper()),
            ('country_id.code', '=', country_code.upper())
        ], limit=1)
        return state.id if state else False

    # ─────────────────────────────────────────────────────────────────────────
    # Odoo → Shopify auto-sync on customer edit
    # ─────────────────────────────────────────────────────────────────────────

    @api.model_create_multi
    def create(self, vals_list):
        partners = super().create(vals_list)
        if self.env.context.get('shopify_sync_skip'):
            return partners
        for partner in partners:
            # Only auto-create in Shopify when a Shopify instance is assigned
            # and no shopify_customer_id yet (prevents duplicate on import)
            if partner.shopify_instance_id and not partner.shopify_customer_id:
                try:
                    partner.with_context(shopify_sync_skip=True)._create_customer_in_shopify()
                except Exception as e:
                    _logger.warning(f'Auto-create customer in Shopify failed for {partner.name}: {str(e)}')
        return partners

    def write(self, vals):
        result = super().write(vals)
        if self.env.context.get('shopify_sync_skip'):
            return result
        sync_trigger_fields = {'name', 'email', 'phone', 'street', 'street2', 'city', 'zip', 'country_id', 'state_id'}
        if any(f in vals for f in sync_trigger_fields):
            for partner in self:
                if partner.is_shopify_customer and partner.shopify_customer_id and partner.shopify_instance_id:
                    try:
                        partner.with_context(shopify_sync_skip=True)._push_customer_update_to_shopify()
                    except Exception as e:
                        _logger.warning(f'Auto-sync customer to Shopify failed for {partner.name}: {str(e)}')
        return result

    def _create_customer_in_shopify(self):
        """Create this partner as a new customer in Shopify and store the returned ID"""
        self.ensure_one()
        instance = self.shopify_instance_id
        if not instance:
            return

        name_parts = (self.name or '').split(' ', 1)
        first_name = name_parts[0]
        last_name = name_parts[1] if len(name_parts) > 1 else ''

        customer_payload = {
            'customer': {
                'first_name': first_name,
                'last_name': last_name,
                'email': self.email or '',
                'accepts_marketing': self.shopify_accepts_marketing,
                'addresses': [{
                    'first_name': first_name,
                    'last_name': last_name,
                    'address1': self.street or '',
                    'address2': self.street2 or '',
                    'city': self.city or '',
                    'province': self.state_id.name if self.state_id else '',
                    'country': self.country_id.code if self.country_id else '',
                    'zip': self.zip or '',
                    'phone': self.phone or '',
                }],
            }
        }
        if self.phone:
            customer_payload['customer']['phone'] = self.phone

        url = f"{instance._get_base_url()}/customers.json"
        response = requests.post(
            url,
            headers=instance._get_headers(),
            json=customer_payload,
            timeout=30,
            verify=certifi.where(),
        )

        if response.status_code == 201:
            result = response.json().get('customer', {})
            link_vals = {
                'shopify_customer_id': str(result['id']),
                'is_shopify_customer': True,
                'shopify_created_at': self._parse_shopify_datetime(result.get('created_at')),
                'shopify_updated_at': self._parse_shopify_datetime(result.get('updated_at')),
            }
            tag = self._get_shopify_customer_tag()
            if tag:
                link_vals['category_id'] = [(4, tag.id)]
            self.with_context(shopify_sync_skip=True).write(link_vals)
            _logger.info(f'Customer {self.name} created in Shopify with ID {result["id"]}')
        else:
            _logger.warning(
                f'Failed to create customer {self.name} in Shopify: '
                f'{response.status_code} - {response.text}'
            )

    def _push_customer_update_to_shopify(self):
        """Push customer changes to Shopify using customerUpdate GraphQL mutation"""
        self.ensure_one()
        if not self.shopify_customer_id or not self.shopify_instance_id:
            return

        instance = self.shopify_instance_id
        customer_gid = f"gid://shopify/Customer/{self.shopify_customer_id}"

        name_parts = (self.name or '').split(' ', 1)
        first_name = name_parts[0]
        last_name = name_parts[1] if len(name_parts) > 1 else ''

        mutation = """
        mutation customerUpdate($input: CustomerInput!) {
          customerUpdate(input: $input) {
            customer {
              id
              updatedAt
            }
            userErrors {
              field
              message
            }
          }
        }
        """

        input_data = {
            "id": customer_gid,
            "firstName": first_name,
            "lastName": last_name,
            "email": self.email or '',
            "addresses": [{
                "address1": self.street or '',
                "address2": self.street2 or '',
                "city": self.city or '',
                "province": self.state_id.name if self.state_id else '',
                "country": self.country_id.code if self.country_id else '',
                "zip": self.zip or '',
                "phone": self.phone or '',
                "firstName": first_name,
                "lastName": last_name,
            }],
        }
        # phone is optional — only include it if set (empty string causes validation errors)
        if self.phone:
            input_data["phone"] = self.phone

        variables = {"input": input_data}

        try:
            data = instance._execute_graphql(mutation, variables)
            result = data.get('customerUpdate', {})

            user_errors = result.get('userErrors', [])
            if user_errors:
                msgs = [f"{e.get('field', '')}: {e.get('message', '')}" for e in user_errors]
                _logger.warning(f'Shopify customerUpdate errors for {self.name}: {msgs}')
                return

            customer_data = result.get('customer', {})
            if customer_data.get('updatedAt'):
                from dateutil import parser as date_parser
                updated_at = date_parser.parse(customer_data['updatedAt']).replace(tzinfo=None)
                self.with_context(shopify_sync_skip=True).write({'shopify_updated_at': updated_at})

            _logger.info(f'Customer {self.name} synced to Shopify successfully')

        except Exception as e:
            _logger.error(f'Error pushing customer update to Shopify for {self.name}: {str(e)}')
            raise

    def action_open_shopify_customer_sync_wizard(self):
        """Open the 'Sync to Shopify' wizard for the selected customer(s)."""
        return {
            'name': _('Sync to Shopify'),
            'type': 'ir.actions.act_window',
            'res_model': 'shopify.customer.sync.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'active_model': 'res.partner',
                'active_ids': self.ids,
                'active_id': self.id if len(self) == 1 else False,
            },
        }

    def export_customer_to_shopify(self):
        """Export a single customer to Shopify"""
        self.ensure_one()

        if not self.shopify_instance_id:
            raise UserError(_('Please select a Shopify instance first'))

        instance = self.shopify_instance_id

        try:
            customer_data = {
                'customer': {
                    'email': self.email or '',
                    'phone': self.phone or '',
                    'first_name': self.name.split()[0] if self.name else '',
                    'last_name': ' '.join(self.name.split()[1:]) if self.name and len(self.name.split()) > 1 else '',
                    'accepts_marketing': self.shopify_accepts_marketing,
                    'addresses': [{
                        'address1': self.street or '',
                        'address2': self.street2 or '',
                        'city': self.city or '',
                        'province': self.state_id.name if self.state_id else '',
                        'country': self.country_id.name if self.country_id else '',
                        'zip': self.zip or '',
                        'phone': self.phone or '',
                    }]
                }
            }

            headers = instance._get_headers()

            if self.shopify_customer_id:
                # Update existing customer
                url = f"{instance._get_base_url()}/customers/{self.shopify_customer_id}.json"
                response = requests.put(url, headers=headers, json=customer_data, timeout=30, verify=certifi.where())
            else:
                # Create new customer
                url = f"{instance._get_base_url()}/customers.json"
                response = requests.post(url, headers=headers, json=customer_data, timeout=30, verify=certifi.where())

            if response.status_code in [200, 201]:
                result_data = response.json().get('customer', {})
                self.write({
                    'shopify_customer_id': str(result_data['id']),
                    'is_shopify_customer': True,
                    'shopify_updated_at': result_data.get('updated_at'),
                })
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': _('Success'),
                        'message': _('Customer exported to Shopify successfully'),
                        'type': 'success',
                    }
                }
            else:
                raise UserError(_('Failed to export customer: %s - %s') % (response.status_code, response.text))

        except Exception as e:
            _logger.error(f'Error exporting customer: {str(e)}')
            raise UserError(_('Error exporting customer: %s') % str(e))
