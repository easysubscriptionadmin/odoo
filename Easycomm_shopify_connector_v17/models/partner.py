# -*- coding: utf-8 -*-

from odoo import api, fields, models, _
from odoo.exceptions import UserError
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

    def import_shopify_customers(self, instance_id):
        """Import customers from Shopify"""
        instance = self.env['shopify.instance'].browse(instance_id)
        if not instance:
            raise UserError(_('Shopify instance not found'))

        try:
            url = f"{instance._get_base_url()}/customers.json"
            params = {'limit': 250}
            headers = instance._get_headers()

            all_customers = []
            page_info = None

            while True:
                if page_info:
                    params['page_info'] = page_info

                response = requests.get(url, headers=headers, params=params, timeout=30, verify=certifi.where())

                if response.status_code != 200:
                    raise UserError(_('Failed to fetch customers: %s - %s') % (response.status_code, response.text))

                data = response.json()
                customers = data.get('customers', [])

                if not customers:
                    break

                all_customers.extend(customers)

                # Check for pagination
                link_header = response.headers.get('Link', '')
                if 'rel="next"' in link_header:
                    for link in link_header.split(','):
                        if 'rel="next"' in link:
                            page_info = link.split('page_info=')[1].split('>')[0]
                            break
                else:
                    break

            _logger.info(f'Fetched {len(all_customers)} customers from Shopify')

            # Process customers
            created_count = 0
            updated_count = 0

            for customer_data in all_customers:
                customer_vals = self._prepare_customer_vals(customer_data, instance)
                existing_customer = self.search([
                    ('shopify_customer_id', '=', str(customer_data['id'])),
                    ('shopify_instance_id', '=', instance.id)
                ], limit=1)

                ctx = {'shopify_sync_skip': True}
                if existing_customer:
                    existing_customer.with_context(**ctx).write(customer_vals)
                    updated_count += 1
                else:
                    self.with_context(**ctx).create(customer_vals)
                    created_count += 1

            # Update sync timestamp
            instance.write({'last_customer_sync': fields.Datetime.now()})

            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Customers Imported'),
                    'message': _('Created: %s, Updated: %s') % (created_count, updated_count),
                    'type': 'success',
                }
            }

        except Exception as e:
            _logger.error(f'Error importing customers: {str(e)}')
            raise UserError(_('Error importing customers: %s') % str(e))

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

        vals = {
            'name': f"{customer_data.get('first_name', '')} {customer_data.get('last_name', '')}".strip() or customer_data.get('email', 'Unknown'),
            'email': customer_data.get('email', ''),
            'phone': customer_data.get('phone', ''),
            'shopify_instance_id': instance.id,
            'shopify_customer_id': str(customer_data['id']),
            'is_shopify_customer': True,
            'shopify_email_verified': customer_data.get('verified_email', False),
            'shopify_accepts_marketing': customer_data.get('accepts_marketing', False),
            'shopify_orders_count': customer_data.get('orders_count', 0),
            'shopify_total_spent': float(customer_data.get('total_spent', 0.0)),
            'shopify_state': customer_data.get('state', 'disabled'),
            'shopify_created_at': self._parse_shopify_datetime(customer_data.get('created_at')),
            'shopify_updated_at': self._parse_shopify_datetime(customer_data.get('updated_at')),
            'customer_rank': 1,
        }

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
            self.with_context(shopify_sync_skip=True).write({
                'shopify_customer_id': str(result['id']),
                'is_shopify_customer': True,
                'shopify_created_at': self._parse_shopify_datetime(result.get('created_at')),
                'shopify_updated_at': self._parse_shopify_datetime(result.get('updated_at')),
            })
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
