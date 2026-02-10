==============================
Easycomm Shopify Connector
==============================

Seamlessly integrate your Shopify store with Odoo to automate workflows,
reduce manual work, and scale your e-commerce business faster.

Getting Started
===============

Prerequisites
-------------

* Odoo 19.0 with the following modules installed: Sales, Inventory, Contacts
* A Shopify store with Admin API access
* Python ``requests`` library (installed automatically)

Creating a Shopify Custom App
------------------------------

To connect Odoo with your Shopify store, you need to create a Custom App in Shopify:

1. Log in to your **Shopify Admin** panel.
2. Go to **Settings** > **Apps and sales channels** > **Develop apps**.
3. Click **Create an app** and give it a name (e.g., "Odoo Connector").
4. Under **Configuration**, click **Configure Admin API scopes** and enable the following permissions:

   - ``read_products``, ``write_products``
   - ``read_orders``, ``write_orders``
   - ``read_customers``, ``write_customers``
   - ``read_inventory``, ``write_inventory``
   - ``read_locations``
   - ``read_price_rules``
   - ``read_gift_cards``
   - ``read_draft_orders``, ``write_draft_orders``

5. Click **Install app** and copy the **Admin API Access Token**.

Connecting Odoo to Shopify
---------------------------

1. In Odoo, go to **Shopify Connector** > **Configuration** > **Shopify Instances**.
2. Click **Create** and fill in the details:

   - **Name**: A descriptive name for your store (e.g., "My Shopify Store")
   - **Shop URL**: Your Shopify store name (e.g., ``mystore`` for ``mystore.myshopify.com``)
   - **Access Token**: Paste the Admin API Access Token from Shopify
   - **API Version**: Default is ``2024-01`` (use the latest stable version)

3. Click **Test Connection** to verify the setup.
4. On success, the store currency is automatically detected and activated in Odoo.

Operations
==========

The **Shopify Operations Wizard** (Shopify Connector > Operations) provides a central
place to run all import and export operations. Select your Shopify Instance and the
desired operation, then click **Perform Operation**.

Importing Products
------------------

- Imports all products from Shopify into Odoo with full details: title, description,
  price, SKU, vendor, tags, status, and timestamps.
- Products are matched by Shopify Product ID to avoid duplicates.
- Supports pagination for stores with large product catalogs (250+ products per page).
- Batch processing ensures stability and progress is saved after each batch.

Exporting Products
------------------

- Export individual products or bulk export from Odoo to Shopify.
- Full support for **product variants** with attributes (size, color, etc.).
- Creates new products or updates existing ones based on Shopify Product ID.
- Single-variant products are automatically formatted with default options.

Importing Orders
----------------

- Imports all orders (any status) from Shopify with complete details.
- Automatically creates or links customers based on Shopify Customer ID or email.
- Creates order lines with correct products, quantities, and prices.
- Tracks financial status (pending, paid, refunded, etc.) and fulfillment status.
- Shipping addresses are created as delivery contacts linked to the customer.
- Filter by date to import only recent orders.

Exporting Orders
----------------

- Export Odoo sale orders to Shopify as **Draft Orders**.
- Links customer information and line items automatically.

Importing Customers
-------------------

- Imports all customers from Shopify with address details.
- Tracks marketing preferences, email verification, order count, and total spent.
- Customers are matched by Shopify ID or email to prevent duplicates.
- Automatically resolves country and state from Shopify country/province codes.

Exporting Customers
-------------------

- Export Odoo contacts to Shopify as new customers or update existing ones.
- Sends name, email, phone, address, and marketing preferences.

Importing Collections
---------------------

- Imports both **Smart Collections** and **Custom Collections** from Shopify.
- Automatically fetches and links products belonging to each collection.
- Supports pagination for collections with many products.

Importing Gift Cards
--------------------

- Imports gift cards from Shopify with balance, initial value, status, and expiry date.
- Links gift cards to existing Odoo customers when possible.

Importing Discounts
-------------------

- Imports all price rules and discount codes from Shopify.
- Tracks discount type (percentage, fixed amount), target products/collections,
  usage limits, validity dates, and prerequisites (minimum purchase, minimum quantity).

Importing Locations
-------------------

- Imports all Shopify fulfillment locations into Odoo.
- Fetches inventory levels for each product at each location.

Inventory Sync
==============

- Sync inventory quantities from Odoo to Shopify.
- Reads ``qty_available`` from Odoo product variants and updates Shopify inventory levels.
- Automatically detects the primary Shopify location.
- Each sync creates a tracked record with status (draft, in progress, done, failed) and error logs.

Webhooks
========

Set up real-time synchronization using Shopify Webhooks:

1. Go to **Shopify Connector** > **Configuration** > **Webhooks**.
2. Create a webhook by selecting the **Topic** and entering your **Odoo server URL**.
3. Click **Create Webhook in Shopify** to register it.

Supported webhook topics:

- **Products**: Created, Updated, Deleted
- **Orders**: Created, Updated, Cancelled, Fulfilled
- **Customers**: Created, Updated
- **Inventory**: Levels Updated
- **Refunds**: Created

When Shopify sends a webhook, the connector automatically processes it:

- Product webhooks create/update/archive products in Odoo.
- Order webhooks create/update orders in Odoo.
- Customer webhooks create/update contacts in Odoo.

Automated Scheduler
===================

Set up recurring automatic synchronization:

1. Go to **Shopify Connector** > **Configuration** > **Schedulers**.
2. Create a scheduler and configure:

   - **Shopify Instance**: Select the store to sync.
   - **Interval**: Set the frequency (minutes, hours, days, or weeks).
   - **Sync Options**: Enable/disable sync for Products, Customers, Orders,
     Inventory, Collections, Gift Cards, Locations, and Discounts.

3. The scheduler creates an Odoo **Scheduled Action** (cron job) automatically.
4. Use **Run Now** to trigger a sync manually at any time.
5. A built-in lock mechanism prevents concurrent executions.

Analytics Dashboard
===================

Access the analytics dashboard from **Shopify Connector** > **Analytics**:

- **Sales Metrics**: Total sales, total orders, average order value, revenue growth percentage.
- **Order Status**: Pending, paid, unfulfilled, fulfilled, and cancelled order counts.
- **Top Selling Products**: Top 10 products by quantity sold with product images and revenue.
- **Recent Orders**: Latest 10 orders with customer name, amount, and status.
- **Inventory Alerts**: Count of low-stock products (below 10 units).
- **Conversion Rate**: Percentage of paid orders vs total orders.
- **Collections & Discounts**: Total counts and active discount tracking.

Filter analytics by date range and Shopify instance.

Sync Logs
=========

All synchronization activities are logged under **Shopify Connector** > **Sync Logs**:

- Tracks sync type (product, order, customer, inventory, webhook).
- Records direction (import/export) and status (success/failure).
- Stores detailed error messages for troubleshooting.

Multi-Store Support
===================

Connect multiple Shopify stores to a single Odoo database:

- Create a separate **Shopify Instance** for each store.
- All data (products, orders, customers) is tagged with its source instance.
- Run operations and schedulers independently per store.
- Analytics can be filtered by instance.

Support
=======

For technical support, please contact:

- **Email**: support@easycomm.com
- **Website**: https://easycomm.io
