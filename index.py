import decimal
import math
import time
from datetime import datetime
from enum import Enum
from pprint import pprint
from order_service import OrderService
import logging
from grpc._cython.cygrpc import Optional
from tinkoff.invest import Client, MoneyValue, OrderDirection, OrderType, OrderExecutionReportStatus, CandleInterval, \
    SecurityTradingStatus, Quotation, OrderState, PostOrderResponse, PortfolioPosition, PriceType, StopOrderDirection, \
    StopOrderType, InstrumentType, InstrumentIdType
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

def to_float(value: MoneyValue | Quotation) -> float:
    return float(str(value.units) + '.' + str(value.nano))


def get_decimal_part(fl):
    return int(math.ceil((fl % 1) * pow(10, str(fl)[::-1].find('.'))))


class TradingBot:

    def __init__(self, token, target, sandbox: bool):
        self.token = token
        self.target = target
        self.sandbox = sandbox
        # self.client: Optional[AsyncServices]
        self.sync_client: Optional[Services]
        self.account_id: Optional[str]
        self.commission = 0.0004
        self.target_daily_profitability = 0.01
        self.stop_loss_profitability = 0.01
        self.buy_free_capital_percentage = 0.1
        # self.market_data_cache: Optional[MarketDataCache] = None
        self.sync_client = Client(self.token, target=self.target).__enter__()
        res = self.sync_client.users.get_accounts()
        account = res.accounts[0]
        self.account_id = account.id
        logger.info("set up account to trade [%s]", self.account_id)
        self.order_service = OrderService(self.sync_client)

    # if (self.sandbox == True):
    # self.sandox_flush_all_accounts_and_reinitiate_one()

    def main(self):

        # stock_name = "GAZP"
        # buy_or_sell = "BUY"
        amount = 1

        order = self.order_service.get_order_to_work(self.account_id)
        # TODO can start the strategy with selected account,or the porfolio item
        self.start_strategy(order)
        self.get_portfolio()

    def start_strategy(self, order: OrderState = None):
        instrument_id = self.__get_instrument_of_the_strategy()
        quantity_lots = 1
        bought_price = 0
        if order is None:
            # we are starting from scratch
            position_in_portfolio = self.__get_position_to_sell(instrument_id)
            if (position_in_portfolio) is None:
                logger.info("Create new order")
                quantity_lots = self.__get_amount_to_buy(instrument_id)
                direction = OrderDirection.ORDER_DIRECTION_BUY
                order_type = OrderType.ORDER_TYPE_BESTPRICE
                order = self.order_service.post_order(quantity_lots, instrument_id, direction,
                                                      order_type, self.account_id)
                bought_price = self.order_service.wait_order_fulfillment(order, self.account_id)
            else:
                logger.info("We have this position in portfolio, start to work with it")
                quantity_lots = position_in_portfolio.quantity_lots
                bought_price = position_in_portfolio.average_position_price
        else:
            # TODO also need to wait until market is open
            logger.info("Start to work with present unfullfilled order, order %s", str(order))
            quantity_lots = order.lots_requested
            bought_price = self.order_service.wait_order_fulfillment(order, self.account_id)

        # quote = Quotation(first_part, decimal_part)
        # self.sync_client.stop_orders.post_stop_order(
        #     quantity=quantity_lots.units,
        #     instrument_id=instrument_id,
        #     stop_price=quote,
        #     account_id=self.account_id,
        #     direction=StopOrderDirection.STOP_ORDER_DIRECTION_SELL,
        #     stop_order_type=StopOrderType.STOP_ORDER_TYPE_STOP_LOSS
        # )
        self.__wait_to_sell_and_get_position_to_sell(instrument_id, bought_price)
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
        logger.info("We have %s lots of %s", position_to_sell.quantity_lots.units, instrument_id)
        return position_to_sell

    def __wait_to_sell_and_get_position_to_sell(self, instrument_id, bought_price: Quotation) -> bool:
        logger.info("waiting to sell instrument (instrument_id : %s)", instrument_id)
        self.__wait_market_to_open(instrument_id)
        sell_signal = False
        while sell_signal == False:
            time.sleep(10)
            sell_signal = self.__get_compared_difference(bought_price=bought_price,
                                                         instrument_id=instrument_id)
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

    def __get_instrument_of_the_strategy(self):
        GAZPROM_SHARES = '962e2a95-02a9-4171-abd7-aa198dbe643a'
        YANDEX_SHARES = '10e17a87-3bce-4a1f-9dfc-720396f98a3c'
        return YANDEX_SHARES

    def __get_amount_to_buy(self, instrument_id):
        prices = self.sync_client.market_data.get_last_prices(instrument_id=[instrument_id])
        price = to_float(prices.last_prices[0].price)
        logger.info("Price - %s", str(price))

        res = self.sync_client.operations.get_portfolio(account_id=self.account_id)
        free_capital = res.total_amount_currencies.units
        logger.info("Free capital in currencies (rub): %s", free_capital)

        instrument_infos = self.sync_client.instruments.find_instrument(query=instrument_id,
                                                                        instrument_kind=InstrumentType.INSTRUMENT_TYPE_SHARE)
        instrument = instrument_infos.instruments[0] if len(instrument_infos.instruments) == 1 else False
        if (instrument != False):
            share_info = self.sync_client.instruments.share_by(id=instrument_id,
                                                               id_type=InstrumentIdType.INSTRUMENT_ID_TYPE_UID,
                                                               class_code=instrument.class_code)
            number_of_shares_in_lot = share_info.instrument.lot
            amount = int((free_capital * self.buy_free_capital_percentage) / (price * number_of_shares_in_lot))
            amount = 1 if amount < 0 else amount
            logger.info("We will by %s lots",amount)
            return amount

    def __get_compared_difference(self, bought_price, instrument_id):
        stop_position_price = to_float(bought_price) * (1 - self.stop_loss_profitability)

        res = self.sync_client.market_data.get_last_prices(instrument_id=[instrument_id])
        sell_price = res.last_prices[0].price
        compared_difference_with_commissions = to_float(sell_price) - to_float(bought_price) - to_float(
            bought_price) * self.commission - to_float(sell_price) * self.commission
        margin = 100 * compared_difference_with_commissions / to_float(bought_price)
        logger.info("BuyPrice: %s,Price: %s, Difference: %s, Margin: %s",
                    to_float(bought_price),
                    to_float(sell_price),
                    compared_difference_with_commissions, margin)

        return margin > 1 or to_float(sell_price) <= stop_position_price

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
