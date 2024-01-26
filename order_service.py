import logging
import time

from tinkoff.invest import PostOrderResponse, OrderState, OrderExecutionReportStatus
from tinkoff.invest.services import Services

logger = logging.getLogger(__name__)


class OrderService:
    def __init__(self, sync_client: Services, account_id):
        self.sync_client = sync_client
        self.account_id = account_id
        self.unfulfilled_orders_queue = []

    async def post_order(self, quantity: int, instrument_id, direction, order_type, account_id) -> PostOrderResponse:
        res = self.sync_client.orders.post_order(
            quantity=quantity,
            instrument_id=instrument_id,
            direction=direction,
            account_id=account_id,
            order_type=order_type
        )
        logger.debug("posted order: %s", str(res))
        self.unfulfilled_orders_queue.append(res)
        return res

    def put_unfulfilled_orders_to_work(self):
        self.__fill_orders_queue_from_server()

    def __execute_unfullfilled_orders(self):
        while True:
            for order in self.unfulfilled_orders_queue:
                self.__wait_order_fulfillment(order)

    def __fill_orders_queue_from_server(self):
        orders = self.list_orders()
        if len(orders) == 0:
            logger.info("We have no pending orders")
        else:
            self.unfulfilled_orders_queue = orders

    def list_orders(self):
        res = self.sync_client.orders.get_orders(account_id=self.account_id)
        return res.orders

    def cancel_all_orders(self):
        res = self.list_orders()
        for order in res.orders:
            self.sync_client.orders.cancel_order(account_id=self.account_id, order_id=order.order_id)

    def __wait_order_fulfillment(self, order: OrderState | PostOrderResponse):
        order_fulfilled = False
        while not order_fulfilled:
            res = self.sync_client.orders.get_order_state(account_id=self.account_id, order_id=order.order_id)
            status = res.execution_report_status
            if status == OrderExecutionReportStatus.EXECUTION_REPORT_STATUS_FILL:
                executed_price = res.executed_order_price
                logger.info("Executed order_id %s, with price %s and direction %s : %s", order.order_id, executed_price,
                            order.direction, str(res))
                self.unfulfilled_orders_queue = (
                    list(filter(lambda unfulfilled_order: unfulfilled_order.order_id != order.order_id,
                                self.unfulfilled_orders_queue)))
