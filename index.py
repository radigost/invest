import time
from enum import Enum
from pprint import pprint
import logging
from grpc._cython.cygrpc import Optional
from tinkoff.invest import Client, MoneyValue, OrderDirection, OrderType, OrderExecutionReportStatus
from decimal import Decimal
from tinkoff.invest.constants import INVEST_GRPC_API, INVEST_GRPC_API_SANDBOX
from dotenv import load_dotenv
from tinkoff.invest.services import Services
from tinkoff.invest.utils import decimal_to_quotation, quotation_to_decimal
import os

load_dotenv()
TOKEN = os.getenv('TINKOFF_API_TOKEN')
TARGET = os.getenv('TINKOFF_CLIENT_TARGET')
log_level = logging.DEBUG

logging.basicConfig(
    level=log_level,
    format="[%(levelname)-5s] %(asctime)-19s %(name)s:%(lineno)d: %(message)s",
)
logging.getLogger("tinkoff").setLevel(log_level)
logger = logging.getLogger(__name__)


class TypeOfStrategy(Enum):
    FIND_FIRST_ITEM_IN_PORTFOLIO_AND_SELL = 1
    PLACE_NEW_ORDER_AND_THEN_SELL_IT = 2


# Assumption: Bot trades whole list of shares

class TradingBot:
    def __init__(self, token, target, sandbox: bool):
        self.token = token
        self.target = target
        self.sandbox = sandbox
        # self.client: Optional[AsyncServices] = None
        self.sync_client: Optional[Services] = None
        self.account_id: Optional[str] = None
        # self.market_data_cache: Optional[MarketDataCache] = None

    def init(self):
        # if (self.sandbox == True):
        # self.sandox_flush_all_accounts_and_reinitiate_one()
        res = self.sync_client.users.get_accounts()
        account = res.accounts[0]
        self.account_id = account.id
        logger.info("set up account to trade [%s]", self.account_id)

    def sandox_flush_all_accounts_and_reinitiate_one(self):
        res = self.sync_client.users.get_accounts()
        for account in res.accounts:
            self.sync_client.sandbox.close_sandbox_account(account_id=account.id)
        self.sync_client.sandbox.open_sandbox_account()

        account = self.sync_client.users.get_accounts().accounts[0]

        money = 1000000
        currency = "rub"
        money = decimal_to_quotation(Decimal(money))
        return self.sync_client.sandbox.sandbox_pay_in(
            account_id=account.id,
            amount=MoneyValue(units=money.units, nano=money.nano, currency=currency),
        )

    def start_strategy(self, order=False):
        if order == False:

            instrument_id = self.get_instrument_of_the_strategy()
            quantity = 1
            direction = OrderDirection.ORDER_DIRECTION_BUY
            order_type = OrderType.ORDER_TYPE_BESTPRICE

            res = self.sync_client.orders.post_order(
                quantity=quantity,
                instrument_id=instrument_id,
                direction=direction,
                account_id=self.account_id,
                order_type=order_type
            )
            pprint(res)
            order_id = res.order_id
            logger.info("Posted order with order_id %s", order_id)

            order_fulfilled = False
            while order_fulfilled == False:
                res = self.sync_client.orders.get_order_state(account_id=self.account_id, order_id=order_id)
                status = res.execution_report_status
                if status != OrderExecutionReportStatus.EXECUTION_REPORT_STATUS_FILL:
                    time.sleep(3)
                else:
                    logger.info("Order %s executed", order_id)
                    order_fulfilled = True
                    pprint(res)


        else:
            logger.info("Start to work with present order")

    def get_order_to_work(self):
        orders = self.list_orders()
        if len(orders) == 0:
            logger.info("We have no pending orders")
            return False
        else:
            # TODO if we start 2 bots one after another, first generated order, second bot starts after pending order is created, then 2 bots will work with same order, which is a bug, but at the moment I have 0 working instancesm hence need to change the logic before the prod
            return orders[0]

    def get_instrument_of_the_strategy(self):
        GAZPROM_SHARES = '962e2a95-02a9-4171-abd7-aa198dbe643a'
        return GAZPROM_SHARES

    def list_orders(self):
        res = self.sync_client.orders.get_orders(account_id=self.account_id)
        return res.orders

    def cancel_all_orders(self):
        res = self.list_orders()
        for order in res.orders:
            self.sync_client.orders.cancel_order(account_id=self.account_id, order_id=order.order_id)

    def list_securities(client):
        pprint("list_secrutities")

    def list_portfolio(self):
        res = self.sync_client.operations.get_portfolio(account_id=self.account_id)
        pprint(res)

    def main(self):

        stock_name = "GAZP"
        buy_or_sell = "BUY"
        amount = 1

        self.sync_client = Client(self.token, target=self.target).__enter__()
        self.init()

        order = self.get_order_to_work()
        # TODO can start the strategy with selected account,or the porfolio item
        self.start_strategy(order)
        # self.list_portfolio()
        # list_securities(client)


bot = TradingBot(TOKEN, TARGET, sandbox=True)
bot.main()
