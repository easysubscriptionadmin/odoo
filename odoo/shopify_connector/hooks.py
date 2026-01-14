# -*- coding: utf-8 -*-

import logging

_logger = logging.getLogger(__name__)


def post_init_hook(env):
    """Reset any stale running flags after module install/upgrade"""
    try:
        # Reset any schedulers that were marked as running (from previous crash)
        schedulers = env['shopify.scheduler'].search([('is_running', '=', True)])
        if schedulers:
            _logger.info(f'Resetting {len(schedulers)} stale running flags from previous session')
            schedulers.write({'is_running': False})
    except Exception as e:
        _logger.warning(f'Could not reset running flags in post_init_hook: {str(e)}')
