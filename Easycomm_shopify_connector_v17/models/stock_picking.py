# -*- coding: utf-8 -*-

from odoo import models, _
import logging

_logger = logging.getLogger(__name__)


class StockPicking(models.Model):
    _inherit = 'stock.picking'

    def button_validate(self):
        """Override to auto-create Shopify fulfillment when a delivery order is validated."""
        result = super().button_validate()

        # After validation, check which pickings are now 'done'
        for picking in self:
            if (
                picking.state == 'done'
                and picking.picking_type_code == 'outgoing'
                and picking.sale_id
                and picking.sale_id.is_shopify_order
                and picking.sale_id.shopify_order_id
                and picking.sale_id.shopify_instance_id
                and picking.sale_id.shopify_fulfillment_status != 'fulfilled'
            ):
                try:
                    _logger.info(
                        f'Auto-fulfilling Shopify order {picking.sale_id.shopify_order_id} '
                        f'from delivery {picking.name}'
                    )
                    picking.sale_id.create_fulfillment_in_shopify()
                except Exception as e:
                    _logger.warning(
                        f'Auto-fulfill in Shopify failed for delivery {picking.name}: {str(e)}'
                    )

        return result
