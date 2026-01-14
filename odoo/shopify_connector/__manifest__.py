# -*- coding: utf-8 -*-
{
    'name': 'Easycomm Shopify Connector',
    'version': '19.0.1.0.0',
    'category': 'Sales/Integration',
    'summary': 'Integrate Shopify with Odoo - Sync Products, Orders, and Customers',
    'description': """
Easycomm Shopify Connector for Odoo
====================================

This module provides seamless integration between Shopify and Odoo.

Key Features:
-------------
* **Bi-directional Product Sync** - Sync products between Shopify and Odoo with images
* **Product Variants** - Full support for product variants and options
* **Order Import & Management** - Import orders with customer data and line items
* **Customer Synchronization** - Auto-create/update customers
* **Real-time Webhooks** - Instant synchronization via Shopify webhooks
* **Payment Transaction Sync** - Track all payment transactions from Shopify
* **Inventory Sync** - Sync inventory quantities from Odoo to Shopify
* **Location-wise Inventory** - View and manage inventory by Shopify location
* **Product Collections** - Sync and manage Shopify collections
* **Gift Card Management** - Track gift cards from Shopify
* **Discount Management** - Sync and track discount codes and price rules from Shopify
* **Automated Scheduler** - Set up recurring automatic synchronization
* **Advanced Analytics Dashboard** - Track sales, orders, and product performance
* **Comprehensive Sync Logs** - Track all synchronization activities with error details
* **Multi-Store Support** - Connect multiple Shopify stores to a single Odoo database
* **Low Stock Alerts** - Get notified about low inventory levels
* **Top Selling Products** - Identify your best performers
* **Purchase Orders Integration** - Access purchase orders from Products menu
* **Stock Transfers** - Quick access to inventory transfers

    """,
    'author': 'Easycomm',
    'website': 'https://www.easycomm.com',
    'support': 'support@easycomm.com',
    'license': 'LGPL-3',
    'depends': [
        'base',
        'sale_management',
        'stock',
        'product',
        'contacts',
    ],
    'external_dependencies': {
        'python': ['requests'],
    },
    'data': [
        'security/ir.model.access.csv',
        'data/sequence.xml',
        'views/shopify_instance_view.xml',
        'views/product_view.xml',
        'views/partner_view.xml',
        'views/order_view.xml',
        'views/inventory_sync_view.xml',
        'views/analytics_view.xml',
        'views/analytics_dashboard_view.xml',
        'views/scheduler_view.xml',
        'views/sync_log_view.xml',
        'views/webhook_view.xml',
        'views/payment_transaction_view.xml',
        'views/collection_view.xml',
        'views/gift_card_view.xml',
        'views/inventory_location_view.xml',
        'views/discount_view.xml',
        'wizard/shopify_operation_view.xml',
        'views/menu_view.xml',
    ],
    'demo': [],
    'images': [
        'static/description/images/main_screenshot.png',
        'static/description/images/dashboard.png',
        'static/description/images/product_sync.png',
        'static/description/images/order_management.png',
    ],
    'installable': True,
    'application': True,
    'auto_install': False,
    'post_init_hook': 'post_init_hook',
}
