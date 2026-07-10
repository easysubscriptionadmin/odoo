# -*- coding: utf-8 -*-

import logging

_logger = logging.getLogger(__name__)


def post_init_hook(env):
    """Reset any stale running flags after module install/upgrade and make sure
    the features the connector relies on are enabled."""
    try:
        # Reset any schedulers that were marked as running (from previous crash)
        schedulers = env['shopify.scheduler'].search([('is_running', '=', True)])
        if schedulers:
            _logger.info(f'Resetting {len(schedulers)} stale running flags from previous session')
            schedulers.write({'is_running': False})
    except Exception as e:
        _logger.warning(f'Could not reset running flags in post_init_hook: {str(e)}')

    try:
        # Enable the "Variants" feature (Attributes & Variants tab on products)
        # so imported Shopify options/variants are visible out of the box.
        variant_group = env.ref('product.group_product_variant', raise_if_not_found=False)
        user_group = env.ref('base.group_user', raise_if_not_found=False)
        if variant_group and user_group and variant_group not in user_group.implied_ids:
            user_group.write({'implied_ids': [(4, variant_group.id)]})
            _logger.info('Enabled product variants feature (group_product_variant)')
    except Exception as e:
        _logger.warning(f'Could not enable variants feature in post_init_hook: {str(e)}')
