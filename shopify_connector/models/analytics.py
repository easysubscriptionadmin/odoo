# -*- coding: utf-8 -*-

from odoo import api, fields, models, _
from odoo.exceptions import UserError
import logging
import requests
import certifi
from dateutil import parser as date_parser
from datetime import datetime, timedelta

_logger = logging.getLogger(__name__)


class ShopifyAnalytics(models.TransientModel):
    _name = 'shopify.analytics'
    _description = 'Shopify Analytics Dashboard'

    shopify_instance_id = fields.Many2one('shopify.instance', string='Shopify Instance', required=True)
    date_from = fields.Date('From Date', default=lambda self: fields.Date.today() - timedelta(days=30))
    date_to = fields.Date('To Date', default=fields.Date.today)

    # Sales Analytics
    total_sales = fields.Monetary('Total Sales', currency_field='currency_id', compute='_compute_analytics')
    total_orders = fields.Integer('Total Orders', compute='_compute_analytics')
    average_order_value = fields.Monetary('Average Order Value', currency_field='currency_id', compute='_compute_analytics')
    total_customers = fields.Integer('Total Customers', compute='_compute_analytics')

    # Product Analytics
    top_selling_products = fields.Text('Top Selling Products', compute='_compute_analytics')
    recent_orders_html = fields.Text('Recent Orders', compute='_compute_analytics')
    low_stock_products = fields.Integer('Low Stock Products', compute='_compute_analytics')
    total_products = fields.Integer('Total Products', compute='_compute_analytics')

    # Order Status
    pending_orders = fields.Integer('Pending Orders', compute='_compute_analytics')
    paid_orders = fields.Integer('Paid Orders', compute='_compute_analytics')
    unfulfilled_orders = fields.Integer('Unfulfilled Orders', compute='_compute_analytics')
    fulfilled_orders = fields.Integer('Fulfilled Orders', compute='_compute_analytics')
    cancelled_orders = fields.Integer('Cancelled Orders', compute='_compute_analytics')

    # Additional Metrics
    total_revenue_growth = fields.Float('Revenue Growth %', compute='_compute_analytics')
    total_items_sold = fields.Integer('Total Items Sold', compute='_compute_analytics')
    conversion_rate = fields.Float('Conversion Rate %', compute='_compute_analytics')

    # Collections & Discounts
    total_collections = fields.Integer('Total Collections', compute='_compute_analytics')
    total_discounts = fields.Integer('Total Discounts', compute='_compute_analytics')
    active_discounts = fields.Integer('Active Discounts', compute='_compute_analytics')

    currency_id = fields.Many2one('res.currency', string='Currency', compute='_compute_currency', store=False)

    @api.depends('shopify_instance_id')
    def _compute_currency(self):
        for record in self:
            if record.shopify_instance_id and record.shopify_instance_id.currency_id:
                record.currency_id = record.shopify_instance_id.currency_id
            else:
                record.currency_id = self.env.company.currency_id

    @api.depends('shopify_instance_id', 'date_from', 'date_to')
    def _compute_analytics(self):
        for record in self:
            if not record.shopify_instance_id:
                record.total_sales = 0
                record.total_orders = 0
                record.average_order_value = 0
                record.total_customers = 0
                record.top_selling_products = ''
                record.recent_orders_html = ''
                record.low_stock_products = 0
                record.total_products = 0
                record.pending_orders = 0
                record.paid_orders = 0
                record.unfulfilled_orders = 0
                record.fulfilled_orders = 0
                record.cancelled_orders = 0
                record.total_revenue_growth = 0
                record.total_items_sold = 0
                record.conversion_rate = 0
                record.total_collections = 0
                record.total_discounts = 0
                record.active_discounts = 0
                continue

            # Get orders in date range
            domain = [
                ('shopify_instance_id', '=', record.shopify_instance_id.id),
                ('is_shopify_order', '=', True),
            ]

            if record.date_from:
                domain.append(('date_order', '>=', record.date_from))
            if record.date_to:
                domain.append(('date_order', '<=', record.date_to))

            orders = self.env['sale.order'].search(domain)

            # Calculate metrics
            record.total_orders = len(orders)
            record.total_sales = sum(orders.mapped('amount_total'))
            record.average_order_value = record.total_sales / record.total_orders if record.total_orders else 0

            # Customer count
            record.total_customers = self.env['res.partner'].search_count([
                ('shopify_instance_id', '=', record.shopify_instance_id.id),
                ('is_shopify_customer', '=', True)
            ])

            # Order status counts - use len() instead of count()
            record.pending_orders = len(orders.filtered(lambda o: o.shopify_financial_status == 'pending'))
            record.paid_orders = len(orders.filtered(lambda o: o.shopify_financial_status == 'paid'))
            record.unfulfilled_orders = len(orders.filtered(lambda o: o.shopify_fulfillment_status == 'unfulfilled'))
            record.fulfilled_orders = len(orders.filtered(lambda o: o.shopify_fulfillment_status == 'fulfilled'))
            record.cancelled_orders = len(orders.filtered(lambda o: o.state == 'cancel'))

            # Total items sold
            record.total_items_sold = int(sum(orders.mapped('order_line').mapped('product_uom_qty')))

            # Total products
            record.total_products = self.env['product.template'].search_count([
                ('shopify_instance_id', '=', record.shopify_instance_id.id),
                ('is_shopify_product', '=', True)
            ])

            # Collections and Discounts
            record.total_collections = self.env['shopify.collection'].search_count([
                ('shopify_instance_id', '=', record.shopify_instance_id.id)
            ])
            record.total_discounts = self.env['shopify.discount'].search_count([
                ('shopify_instance_id', '=', record.shopify_instance_id.id)
            ])
            record.active_discounts = self.env['shopify.discount'].search_count([
                ('shopify_instance_id', '=', record.shopify_instance_id.id),
                ('active_discount', '=', True)
            ])

            # Conversion rate (paid orders / total orders * 100)
            record.conversion_rate = (record.paid_orders / record.total_orders * 100) if record.total_orders else 0

            # Revenue growth (compare with previous period)
            previous_period_start = record.date_from - (record.date_to - record.date_from) if record.date_from and record.date_to else None
            if previous_period_start:
                prev_orders = self.env['sale.order'].search([
                    ('shopify_instance_id', '=', record.shopify_instance_id.id),
                    ('is_shopify_order', '=', True),
                    ('date_order', '>=', previous_period_start),
                    ('date_order', '<', record.date_from)
                ])
                prev_sales = sum(prev_orders.mapped('amount_total'))
                if prev_sales:
                    record.total_revenue_growth = ((record.total_sales - prev_sales) / prev_sales) * 100
                else:
                    record.total_revenue_growth = 100 if record.total_sales > 0 else 0
            else:
                record.total_revenue_growth = 0

            # Top selling products with images and revenue
            product_sales = {}
            product_revenue = {}
            product_objects = {}
            for order in orders:
                for line in order.order_line:
                    if line.product_id:
                        product_id = line.product_id.id
                        if product_id not in product_sales:
                            product_sales[product_id] = 0
                            product_revenue[product_id] = 0
                            product_objects[product_id] = line.product_id
                        product_sales[product_id] += line.product_uom_qty
                        product_revenue[product_id] += line.price_subtotal

            # Sort and get top 10
            top_products = sorted(product_sales.items(), key=lambda x: x[1], reverse=True)[:10]

            # Generate HTML table
            if top_products:
                html_rows = []
                for product_id, qty in top_products:
                    product = product_objects[product_id]
                    revenue = product_revenue[product_id]
                    image_url = f'/web/image/product.template/{product.product_tmpl_id.id}/image_128' if product.product_tmpl_id else ''

                    html_rows.append(f'''
                        <tr>
                            <td class="align-middle">
                                <div class="d-flex align-items-center">
                                    <img src="{image_url}" alt="{product.display_name}"
                                         class="rounded me-2" style="width: 40px; height: 40px; object-fit: cover;"
                                         onerror="this.src='/web/static/img/placeholder.png'"/>
                                    <span class="fw-bold">{product.display_name[:40]}</span>
                                </div>
                            </td>
                            <td class="align-middle text-center">
                                <span class="badge bg-primary">{int(qty)} units</span>
                            </td>
                            <td class="align-middle text-end fw-bold text-success">
                                {record.currency_id.symbol}{revenue:,.2f}
                            </td>
                        </tr>
                    ''')
                record.top_selling_products = ''.join(html_rows)
            else:
                record.top_selling_products = '<tr><td colspan="3" class="text-center text-muted py-4">No sales data available</td></tr>'

            # Recent orders with customer info
            recent_orders = orders.sorted(key=lambda r: r.date_order, reverse=True)[:10]
            if recent_orders:
                order_rows = []
                for order in recent_orders:
                    status_color = 'success' if order.shopify_financial_status == 'paid' else 'warning' if order.shopify_financial_status == 'pending' else 'secondary'
                    fulfillment_icon = '✓' if order.shopify_fulfillment_status == 'fulfilled' else '○'

                    order_rows.append(f'''
                        <tr>
                            <td class="align-middle">
                                <span class="fw-bold text-primary">{order.name}</span>
                                <br/>
                                <small class="text-muted">{order.date_order.strftime('%Y-%m-%d %H:%M') if order.date_order else ''}</small>
                            </td>
                            <td class="align-middle">
                                <span class="text-dark">{order.partner_id.name if order.partner_id else 'N/A'}</span>
                            </td>
                            <td class="align-middle text-end fw-bold">
                                {record.currency_id.symbol}{order.amount_total:,.2f}
                            </td>
                            <td class="align-middle text-center">
                                <span class="badge bg-{status_color}">{order.shopify_financial_status or 'N/A'}</span>
                                <br/>
                                <small class="text-muted">{fulfillment_icon} {order.shopify_fulfillment_status or 'N/A'}</small>
                            </td>
                        </tr>
                    ''')
                record.recent_orders_html = ''.join(order_rows)
            else:
                record.recent_orders_html = '<tr><td colspan="4" class="text-center text-muted py-4">No recent orders</td></tr>'

            # Low stock products (qty < 10)
            record.low_stock_products = self.env['product.template'].search_count([
                ('shopify_instance_id', '=', record.shopify_instance_id.id),
                ('is_shopify_product', '=', True),
                ('qty_available', '<', 10)
            ])

    def refresh_analytics(self):
        """Refresh analytics data"""
        self._compute_analytics()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'shopify.analytics',
            'view_mode': 'form',
            'res_id': self.id,
            'target': 'new',
        }

    def action_view_top_products(self):
        """View top selling products"""
        self.ensure_one()

        # Get orders in date range
        domain = [
            ('shopify_instance_id', '=', self.shopify_instance_id.id),
            ('is_shopify_order', '=', True),
        ]
        if self.date_from:
            domain.append(('date_order', '>=', self.date_from))
        if self.date_to:
            domain.append(('date_order', '<=', self.date_to))

        orders = self.env['sale.order'].search(domain)

        # Get unique product IDs from order lines
        product_ids = orders.mapped('order_line').mapped('product_id').ids

        # Use the custom action with kanban and list views
        action = self.env.ref('shopify_connector.action_shopify_top_products_analytics').read()[0]
        action['domain'] = [('id', 'in', product_ids)]
        action['context'] = {
            'default_shopify_instance_id': self.shopify_instance_id.id,
        }
        return action

    def action_view_recent_orders(self):
        """View recent orders"""
        self.ensure_one()

        # Get orders in date range
        domain = [
            ('shopify_instance_id', '=', self.shopify_instance_id.id),
            ('is_shopify_order', '=', True),
        ]
        if self.date_from:
            domain.append(('date_order', '>=', self.date_from))
        if self.date_to:
            domain.append(('date_order', '<=', self.date_to))

        # Use the custom action with kanban and list views
        action = self.env.ref('shopify_connector.action_shopify_recent_orders_analytics').read()[0]
        action['domain'] = domain
        action['context'] = {
            'default_shopify_instance_id': self.shopify_instance_id.id,
        }
        return action

    def fetch_shopify_reports(self):
        """Generate analytics report from synced data"""
        self.ensure_one()

        try:
            # Since Shopify deprecated custom reports API, we'll generate reports from local synced data
            self._compute_analytics()

            # Generate summary message
            message = f"""
            Analytics Summary:
            ------------------
            Total Sales: {self.currency_id.symbol}{self.total_sales:,.2f}
            Total Orders: {self.total_orders}
            Average Order Value: {self.currency_id.symbol}{self.average_order_value:,.2f}
            Total Customers: {self.total_customers}

            Order Status:
            - Paid Orders: {self.paid_orders}
            - Pending Orders: {self.pending_orders}
            - Unfulfilled Orders: {self.unfulfilled_orders}

            Low Stock Products: {self.low_stock_products}
            """

            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Analytics Report Generated'),
                    'message': _(message),
                    'type': 'success',
                    'sticky': True,
                }
            }

        except Exception as e:
            _logger.error(f'Error generating analytics report: {str(e)}')
            raise UserError(_('Error generating analytics report: %s') % str(e))
