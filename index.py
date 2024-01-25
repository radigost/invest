import logging
import os
import time
from decimal import Decimal
from enum import Enum
from pprint import pprint

from dotenv import load_dotenv
from grpc._cython.cygrpc import Optional
from tinkoff.invest import Client, MoneyValue, OrderDirection, OrderType, SecurityTradingStatus, Quotation, OrderState
from tinkoff.invest.services import Services
from tinkoff.invest.utils import decimal_to_quotation

from analytic import Analytic
from order_service import OrderService
from utils import to_float

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

        # self.market_data_cache: Optional[MarketDataCache] = None
        self.sync_client = Client(self.token, target=self.target).__enter__()
        res = self.sync_client.users.get_accounts()
        account = res.accounts[0]
        self.account_id = account.id
        logger.info("set up account to trade [%s]", self.account_id)
        self.order_service = OrderService(self.sync_client)
        self.analytics = Analytic(self.sync_client, account_id=self.account_id)

    # if (self.sandbox == True):
    # self.sandox_flush_all_accounts_and_reinitiate_one()

    def main(self):

        # stock_name = "GAZP"
        # buy_or_sell = "BUY"
        amount = 1

        order = None
        # order = self.order_service.get_order_to_work(self.account_id)
        # TODO can start the strategy with selected account,or the porfolio item
        while True:
            free_capital = self.sync_client.operations.get_portfolio(account_id=self.account_id).total_amount_currencies.units
            self.run_strategy(order)
            new_free_capital = self.sync_client.operations.get_portfolio(account_id=self.account_id).total_amount_currencies.units
            logger.info("Old Capital: %s, New Free capital in currencies (rub): %s", free_capital,new_free_capital)

    def run_strategy(self, order: OrderState = None):
        instrument_id = None
        quantity_lots = 1
        bought_price = 0
        average_bought_price = None
        if order is None:
            instrument_id = self.analytics.get_instrument_of_the_strategy()
            # we are starting from scratch
            position_in_portfolio = self.__get_position_to_sell(instrument_id)
            if (position_in_portfolio) is None:
                logger.info("Create new order")
                quantity_lots = self.analytics.get_amount_to_buy(instrument_id)
                direction = OrderDirection.ORDER_DIRECTION_BUY
                order_type = OrderType.ORDER_TYPE_BESTPRICE
                order = self.order_service.post_order(quantity_lots, instrument_id, direction,
                                                      order_type, self.account_id)
                bought_price = self.order_service.wait_order_fulfillment(order, self.account_id)
            else:
                logger.info("We have this position in portfolio, start to work with it")
                quantity_lots = position_in_portfolio.quantity_lots.units
                average_bought_price = position_in_portfolio.average_position_price
        else:
            # TODO also need to wait until market is open
            logger.info("Start to work with present unfullfilled order, order %s", str(order))
            instrument_id = order.instrument_uid
            quantity_lots = order.lots_requested
            bought_price = self.order_service.wait_order_fulfillment(order, self.account_id)

        self.__wait_to_sell_and_get_position_to_sell(instrument_id, bought_price, quantity_lots,average_bought_price)
        sell_order = self.order_service.post_order(quantity=quantity_lots,
                                                   instrument_id=instrument_id,
                                                   direction=OrderDirection.ORDER_DIRECTION_SELL,
                                                   order_type=OrderType.ORDER_TYPE_BESTPRICE,
                                                   account_id=self.account_id)
        selled_price = self.order_service.wait_order_fulfillment(sell_order, self.account_id)

        # TODO can be different currency
        result = to_float(selled_price) - to_float(bought_price)
        logger.info("result is %s (without commissions)", result)

    def __get_position_to_sell(self, instrument_id):
        res = self.sync_client.operations.get_portfolio(account_id=self.account_id)
        position_to_sell = next((x for x in res.positions if x.instrument_uid == instrument_id), None)
        # TODO it can happen that we do not have that instrument in the portpolio
        if position_to_sell:
            logger.info("We have %s lots of %s", position_to_sell.quantity_lots.units, instrument_id)
        return position_to_sell

    def __wait_to_sell_and_get_position_to_sell(self, instrument_id, bought_price: Quotation, quantity_lots:int,average_bought_price) -> bool:
        logger.info("waiting to sell instrument (instrument_id : %s)", instrument_id)
        self.__wait_market_to_open(instrument_id)
        sell_signal = False
        while sell_signal == False:
            time.sleep(10)
            sell_signal = self.analytics.get_compared_difference(total_buy_price=bought_price,
                                                   instrument_id=instrument_id, quantity_lots=quantity_lots,average_bought_price=average_bought_price)
            # sell_signal =False
        return True

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



    def __wait_market_to_open(self, instrument_id):

        can_trade = False
        while can_trade != True:
            res = self.sync_client.market_data.get_trading_status(instrument_id=instrument_id)
            # TODO there can be values of api_trade_available_flag,market_order_available_flag, limit_order_available_flag , they also can affect availability to trade
            if res.trading_status != SecurityTradingStatus.SECURITY_TRADING_STATUS_NORMAL_TRADING and res.trading_status != SecurityTradingStatus.SECURITY_TRADING_STATUS_DEALER_NORMAL_TRADING:

                time_to_sleep = 2000
                logger.info("Trade is not open for work (status is %s), wait for %d seconds", res.trading_status,
                            time_to_sleep)
                time.sleep(time_to_sleep)
            else:
                can_trade = True

    def list_securities(client):
        pprint("list_secrutities")

    def get_portfolio(self):
        res = self.sync_client.operations.get_portfolio(account_id=self.account_id)
        pprint(res)
        return res


bot = TradingBot(TOKEN, TARGET, sandbox=True)
bot.main()
