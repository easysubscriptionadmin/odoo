==============================
Easycomm Shopify Connector
==============================

Overview
========

The Easycomm Shopify Connector provides seamless integration between Shopify and Odoo 19.0.
Synchronize products, customers, orders, inventory, and more with your Shopify store.

Installation
============

Requirements
------------

* Odoo 19.0 (Enterprise or Community)
* Python ``requests`` library
* Shopify store with API access

Steps
-----

1. Install the required Python library::

    pip install requests

2. Copy the ``shopify_connector`` module to your Odoo addons directory
3. Update the Apps list in Odoo (Settings > Apps > Update Apps List)
4. Search for "Easycomm Shopify Connector" and install

Configuration
=============

Setting up Shopify Instance
---------------------------

1. Navigate to **Shopify > Configuration > Shopify Instances**
2. Click **Create** to add a new instance
3. Fill in the required fields:

   * **Name**: A descriptive name for your store
   * **Store URL**: Your Shopify store URL (e.g., ``your-store.myshopify.com``)
   * **API Access Token**: Your Shopify Admin API access token
   * **API Version**: The Shopify API version (default: 2024-01)

4. Click **Test Connection** to verify your credentials
5. Save the instance

Getting Shopify API Credentials
-------------------------------

1. Log in to your Shopify admin panel
2. Go to **Settings > Apps and sales channels > Develop apps**
3. Click **Create an app** and give it a name
4. Configure the Admin API scopes:

   * ``read_products``, ``write_products``
   * ``read_customers``, ``write_customers``
   * ``read_orders``, ``write_orders``
   * ``read_inventory``, ``write_inventory``
   * ``read_locations``

5. Install the app and copy the **Admin API access token**

Features
========

Product Synchronization
-----------------------

* Import products from Shopify to Odoo
* Export products from Odoo to Shopify
* Sync product variants with options
* Sync product images
* Update prices and inventory

Customer Management
-------------------

* Import Shopify customers to Odoo contacts
* Export Odoo contacts to Shopify
* Sync addresses and contact information
* Automatic customer matching

Order Synchronization
---------------------

* Import Shopify orders to Odoo sales orders
* Track order status and fulfillment
* Monitor financial status (paid, pending, refunded)
* Sync order line items and discounts

Inventory Management
--------------------

* Sync inventory levels from Odoo to Shopify
* Support for multiple Shopify locations
* Real-time inventory updates via webhooks
* Low stock alerts

Webhooks
--------

* Real-time order notifications
* Product update notifications
* Customer update notifications
* Automatic webhook registration

Analytics Dashboard
-------------------

* Sales performance tracking
* Order statistics
* Top-selling products
* Revenue analytics

Usage
=====

Importing Data from Shopify
---------------------------

1. Go to **Shopify > Operations > Shopify Operations**
2. Select your Shopify instance
3. Choose the operation type (Import Products, Import Customers, Import Orders)
4. Click **Execute**

Exporting Data to Shopify
-------------------------

1. Go to **Shopify > Operations > Shopify Operations**
2. Select your Shopify instance
3. Choose the operation type (Export Products, Export Customers)
4. Click **Execute**

You can also export individual records using the **Export to Shopify** button on product, customer, or order forms.

Viewing Sync Logs
-----------------

1. Go to **Shopify > Sync Logs**
2. View synchronization history and any errors
3. Filter by instance, operation type, or status

Troubleshooting
===============

Connection Errors
-----------------

* Verify your API credentials are correct
* Check that your API token has the required permissions
* Ensure your store URL is correct (without https://)

Sync Failures
-------------

* Check the Sync Logs for detailed error messages
* Verify that required fields are filled in Odoo records
* Ensure products have valid SKUs for matching

Support
=======

For support and questions, please contact:

* Email: support@easycomm.com
* Website: https://www.easycomm.com

License
=======

This module is licensed under LGPL-3.

Changelog
=========

Version 19.0.1.0.0
------------------

* Initial release for Odoo 19.0
* Product synchronization (import/export)
* Customer synchronization (import/export)
* Order import from Shopify
* Webhook support for real-time sync
* Multi-store support
* Analytics dashboard
* Inventory synchronization
* Gift card management
* Discount and price rule sync
