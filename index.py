import asyncio
import logging
import os
import random
import time
from decimal import Decimal
from enum import Enum
from pprint import pprint

from dotenv import load_dotenv
from grpc._cython.cygrpc import Optional
from tinkoff.invest import Client, MoneyValue, OrderDirection, OrderType, SecurityTradingStatus, Quotation, OrderState, \
    AsyncClient
from tinkoff.invest.services import Services
from tinkoff.invest.utils import decimal_to_quotation

from analytic import Analytic
from order_service import OrderService
from utils import to_float

load_dotenv()
TOKEN = os.getenv('TINKOFF_API_TOKEN')
TARGET = os.getenv('TINKOFF_CLIENT_TARGET')
log_level = logging.INFO

logging.basicConfig(
    # filename='example.log',
    # filemode='w',
    level=log_level,
    format="[%(levelname)-5s] %(asctime)-19s %(name)s:%(lineno)d: %(message)s",
)
logging.getLogger("tinkoff").setLevel(logging.WARN)
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
        # self.client = AsyncClient(self.token, target=self.target).__aenter__()
        self.sync_client: Optional[Services]
        self.account_id: Optional[str]

        # self.market_data_cache: Optional[MarketDataCache] = None
        self.sync_client = Client(self.token, target=self.target).__enter__()
        res = self.sync_client.users.get_accounts()
        account = res.accounts[0]
        self.account_id = account.id
        logger.info("set up account to trade [%s]", self.account_id)
        self.order_service = OrderService(self.sync_client, self.account_id)
        self.analytics = Analytic(self.sync_client, account_id=self.account_id)
        self.positions_in_work = {}
        self.previous_capitalisation = 0

    async def main(self):
        self.order_service.put_unfulfilled_orders_to_work()
        # TODO can start the strategy with selected account
        while True:
            free_capital = self.previous_capitalisation

            await self.run_strategy()

            new_free_capital = self.sync_client.operations.get_portfolio(
                account_id=self.account_id).total_amount_currencies.units
            logger.info("Old Capital: %s, New Free capital in currencies (rub): %s", free_capital, new_free_capital)
            self.previous_capitalisation = new_free_capital

    async def run_strategy(self):
        async with asyncio.TaskGroup() as tg:
            free_positions_in_portfolio = self.__get_free_position_to_sell()
            if (len(free_positions_in_portfolio)) > 0:
                for position_in_portfolio in free_positions_in_portfolio:
                    self.__fill_in_free_positions(position_in_portfolio)
                    task = tg.create_task(
                        self.__wait_to_sell_and_get_position_to_sell(instrument_id=position_in_portfolio.instrument_uid,
                                                                     quantity_lots=position_in_portfolio.quantity_lots.units,
                                                                     average_bought_price=position_in_portfolio.average_position_price
                                                                     )
                    )

                    task.add_done_callback(
                        lambda res: self.order_service.post_order(quantity=position_in_portfolio.quantity_lots.units,
                                                                  instrument_id=position_in_portfolio.instrument_uid,
                                                                  direction=OrderDirection.ORDER_DIRECTION_SELL,
                                                                  order_type=OrderType.ORDER_TYPE_BESTPRICE,
                                                                  account_id=self.account_id)
                    )

            else:
                self.__buy_new_instrument()

    def __fill_in_free_positions(self, position_in_portfolio):
        logger.info("We found position in portfolio, start to work with it (sell), %s", str(position_in_portfolio))
        instrument_id = position_in_portfolio.instrument_uid
        self.positions_in_work[instrument_id] = position_in_portfolio

    def __buy_new_instrument(self):
        logger.info(" No position in portfolio to work with, will find new instrument to work with")
        instrument_id = self.analytics.get_instrument_of_the_strategy()
        logger.info("will buy %s", instrument_id)
        quantity_lots = self.analytics.get_amount_to_buy(instrument_id)
        direction = OrderDirection.ORDER_DIRECTION_BUY
        order_type = OrderType.ORDER_TYPE_BESTPRICE
        self.order_service.post_order(quantity_lots, instrument_id, direction, order_type, self.account_id)

    def __get_free_position_to_sell(self):
        positions = self.sync_client.operations.get_portfolio(account_id=self.account_id).positions
        free_positions = list(filter(lambda position: self.positions_in_work.get(
            position.instrument_uid) is None and position.instrument_type == 'share',
                                     positions))

        chosen_position = random.choice(free_positions)
        return free_positions

    async def __wait_to_sell_and_get_position_to_sell(self, instrument_id, quantity_lots: int,
                                                      average_bought_price) -> bool:
        logger.info("waiting to sell instrument (instrument_id : %s)", instrument_id)
        self.__wait_market_to_open(instrument_id)
        sell_signal = False
        while not sell_signal:
            await asyncio.sleep(10)
            sell_signal = self.analytics.calculate_sell_signal(instrument_id=instrument_id,
                                                               quantity_lots=quantity_lots,
                                                               average_bought_price=average_bought_price)
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
asyncio.run(bot.main())
