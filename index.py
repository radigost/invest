import time
from datetime import datetime
from enum import Enum
from pprint import pprint
import logging
from grpc._cython.cygrpc import Optional
from tinkoff.invest import Client, MoneyValue, OrderDirection, OrderType, OrderExecutionReportStatus, CandleInterval, \
    SecurityTradingStatus, Quotation, OrderState, PostOrderResponse, PortfolioPosition
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
        # self.client: Optional[AsyncServices]
        self.sync_client: Optional[Services]
        self.account_id: Optional[str]
        self.commision = 0.0004
        self.target_daily_profitability = 0.01
        # self.market_data_cache: Optional[MarketDataCache] = None
        self.sync_client = Client(self.token, target=self.target).__enter__()
        self.init()

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

    def start_strategy(self, order: OrderState = None):
        if order is None:
            logger.info("Create new order")
            instrument_id = self.__get_instrument_of_the_strategy()
            quantity = 1
            direction = OrderDirection.ORDER_DIRECTION_BUY
            order_type = OrderType.ORDER_TYPE_BESTPRICE

            order = self.__post_order(quantity, instrument_id, direction,
                                      order_type)
        else:
            logger.info("Start to work with present order, order %s", str(order))

        bought_price = self.__wait_order_fulfillment(order)
        position = self.__get_position_to_sell(order.instrument_uid, bought_price)
        sell_order = self.__post_order(quantity=position.quantity_lots.units,
                                       instrument_id=position.instrument_uid,
                                       direction=OrderDirection.ORDER_DIRECTION_SELL,
                                       order_type=OrderType.ORDER_TYPE_BESTPRICE)
        selled_price = self.__wait_order_fulfillment(sell_order)

        # TODO can be different currency
        result = self.to_float(selled_price) - self.to_float(bought_price)
        logger.info("result is %s (without commissions)", result)

    def __wait_order_fulfillment(self, order: OrderState | PostOrderResponse):
        order_fulfilled = False
        while order_fulfilled == False:
            res = self.sync_client.orders.get_order_state(account_id=self.account_id, order_id=order.order_id)
            status = res.execution_report_status
            if status != OrderExecutionReportStatus.EXECUTION_REPORT_STATUS_FILL:
                time.sleep(3)
            else:
                executed_price = res.executed_order_price
                logger.info("Executed order_id %s, with price %s and direction %s : %s", order.order_id, executed_price,
                            order.direction, str(res))
                return executed_price

    def __post_order(self, quantity, instrument_id, direction, order_type) -> PostOrderResponse:
        res = self.sync_client.orders.post_order(
            quantity=quantity,
            instrument_id=instrument_id,
            direction=direction,
            account_id=self.account_id,
            order_type=order_type
        )
        logger.debug("posted order: %s", str(res))
        return res

    def __get_instrument_of_the_strategy(self):
        GAZPROM_SHARES = '962e2a95-02a9-4171-abd7-aa198dbe643a'
        return GAZPROM_SHARES

    def __get_position_to_sell(self, instrument_id, bought_price: Quotation) -> PortfolioPosition:
        logger.info("waiting to sell instrument (instrument_id : %s)", instrument_id)
        res = self.sync_client.operations.get_portfolio(account_id=self.account_id)
        position = next((x for x in res.positions if x.instrument_uid == instrument_id), None)
        # TODO it can happen that we do not have that instrument in the portpolio
        logger.info("We have %s lots of %s", position.quantity_lots.units, instrument_id)

        self.__wait_market_to_open(instrument_id)

        sell_signal = False
        while sell_signal == False:
            time.sleep(10)
            sell_signal = self.__get_compared_difference(bought_price=bought_price,
                                                         instrument_id=instrument_id)

        # res = self.sync_client.market_data.get_candles(instrument_id=instrument_id,
        #                                                from_=datetime(2023, 1, 17, 00, 00),
        #                                                to=datetime(2023, 1, 18, 9, 0),
        #                                                interval=CandleInterval.CANDLE_INTERVAL_30_MIN)
        # pprint(res)

        return position

    def __get_compared_difference(self, bought_price, instrument_id):
        res = self.sync_client.market_data.get_last_prices(instrument_id=[instrument_id])
        sell_price = res.last_prices[0].price
        compared_difference_with_commissions = self.to_float(sell_price) - self.to_float(bought_price) - self.to_float(
            bought_price) * self.commision - self.to_float(sell_price) * self.commision
        margin = 100 * compared_difference_with_commissions / self.to_float(bought_price)
        logger.info("BuyPrice: %s,Price: %s, Difference: %s, Margin: %s",
                    self.to_float(bought_price),
                    self.to_float(sell_price),
                    compared_difference_with_commissions, margin)
        return margin > 1

    def __wait_market_to_open(self, instrument_id):
        can_trade = False
        while can_trade != True:
            res = self.sync_client.market_data.get_trading_status(instrument_id=instrument_id)
            # TODO there can be values of api_trade_available_flag,market_order_available_flag, limit_order_available_flag , they also can affect availability to trade
            if res.trading_status != SecurityTradingStatus.SECURITY_TRADING_STATUS_NORMAL_TRADING:
                time_to_sleep = 20
                logger.info("Trade is not open for work (status is %s), wait for %d seconds", res.trading_status,
                            time_to_sleep)
                time.sleep(time_to_sleep)
            else:
                can_trade = True

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

        order = self.get_order_to_work()
        # TODO can start the strategy with selected account,or the porfolio item
        self.start_strategy(order)
        self.list_portfolio()
        # list_securities(client)

    def get_order_to_work(self):
        orders = self.list_orders()
        if len(orders) == 0:
            logger.info("We have no pending orders")
            return None
        else:
            # TODO if we start 2 bots one after another, first generated order, second bot starts after pending order is created, then 2 bots will work with same order, which is a bug, but at the moment I have 0 working instancesm hence need to change the logic before the prod
            return orders[0]

    def to_float(self, value: MoneyValue | Quotation) -> float:
        return float(str(value.units) + '.' + str(value.nano))


bot = TradingBot(TOKEN, TARGET, sandbox=True)
bot.main()
