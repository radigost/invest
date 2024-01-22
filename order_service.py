import logging
import time

from tinkoff.invest import PostOrderResponse, OrderState, OrderExecutionReportStatus
from tinkoff.invest.services import Services

logger = logging.getLogger(__name__)


class OrderService():
    def __init__(self, sync_client: Services):
        self.sync_client = sync_client

    def post_order(self, quantity: int, instrument_id, direction, order_type, account_id) -> PostOrderResponse:
        res = self.sync_client.orders.post_order(
            quantity=quantity,
            instrument_id=instrument_id,
            direction=direction,
            account_id=account_id,
            order_type=order_type
        )
        logger.debug("posted order: %s", str(res))
        return res

    def get_order_to_work(self, account_id):
        orders = self.list_orders(account_id)
        if len(orders) == 0:
            logger.info("We have no pending orders")
            return None
        else:
            # TODO if we start 2 bots one after another, first generated order, second bot starts after pending order is created, then 2 bots will work with same order, which is a bug, but at the moment I have 0 working instancesm hence need to change the logic before the prod
            return orders[0]

    def list_orders(self, account_id):
        res = self.sync_client.orders.get_orders(account_id=account_id)
        return res.orders

    def cancel_all_orders(self, account_id):
        res = self.list_orders(account_id)
        for order in res.orders:
            self.sync_client.orders.cancel_order(account_id=account_id, order_id=order.order_id)

    def wait_order_fulfillment(self, order: OrderState | PostOrderResponse, account_id):
        order_fulfilled = False
        while order_fulfilled == False:
            res = self.sync_client.orders.get_order_state(account_id=account_id, order_id=order.order_id)
            status = res.execution_report_status
            if status != OrderExecutionReportStatus.EXECUTION_REPORT_STATUS_FILL:
                time.sleep(3)
            else:
                executed_price = res.executed_order_price
                logger.info("Executed order_id %s, with price %s and direction %s : %s", order.order_id, executed_price,
                            order.direction, str(res))
                return executed_price
