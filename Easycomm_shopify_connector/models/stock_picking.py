# -*- coding: utf-8 -*-

# NOTE: the Shopify auto-fulfillment hook lives in models/order.py
# (StockPicking._action_done) and fulfills only the DELIVERED quantities of the
# validated picking. The previous duplicate button_validate hook here was
# removed: it fired a second, full fulfillment right after the partial one.
