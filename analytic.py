import logging
import random
from pprint import pprint

from tinkoff.invest import InstrumentType, InstrumentIdType, InstrumentStatus
from tinkoff.invest.services import Services

from utils import to_float

logger = logging.getLogger(__name__)


class Analytic():
    def __init__(self, sync_client, account_id):
        self.stop_loss_profitability = 0.01
        self.buy_free_capital_percentage = 0.1
        self.commission = 0.0004
        self.target_daily_profitability = 0.01
        self.sync_client: Services = sync_client
        self.account_id = account_id

    def get_instrument_of_the_strategy(self) -> str:
        GAZPROM_SHARES = '962e2a95-02a9-4171-abd7-aa198dbe643a'
        YANDEX_SHARES = '10e17a87-3bce-4a1f-9dfc-720396f98a3c'

        instruments = self.sync_client.instruments.get_favorites().favorite_instruments
        instruments = list(filter(lambda instrument:
                                  instrument.instrument_kind == InstrumentType.INSTRUMENT_TYPE_SHARE and
                                  instrument.api_trade_available_flag is True and instrument,
                                  instruments))

        # for instrument in instruments:
        #     pprint(instrument)
        random_share = self.sync_client.instruments.share_by(id_type=InstrumentIdType.INSTRUMENT_ID_TYPE_FIGI,
                                                             id=random.choice(instruments).figi)
        pprint(random_share)
        return random_share.instrument.uid

    def get_amount_to_buy(self, instrument_id) -> int:
        prices = self.sync_client.market_data.get_last_prices(instrument_id=[instrument_id])
        price = to_float(prices.last_prices[0].price)
        logger.info("Price - %s", str(price))

        res = self.sync_client.operations.get_portfolio(account_id=self.account_id)
        free_capital = res.total_amount_currencies.units
        logger.info("Free capital in currencies (rub): %s", free_capital)

        instrument_infos = self.sync_client.instruments.find_instrument(
            query=instrument_id,
            instrument_kind=InstrumentType.INSTRUMENT_TYPE_SHARE)
        instrument = instrument_infos.instruments[0] if len(instrument_infos.instruments) == 1 else None
        if instrument:
            share_info = self.sync_client.instruments.share_by(id=instrument_id,
                                                               id_type=InstrumentIdType.INSTRUMENT_ID_TYPE_UID,
                                                               class_code=instrument.class_code)
            number_of_shares_in_lot = share_info.instrument.lot
            amount = int((free_capital * self.buy_free_capital_percentage) / (price * number_of_shares_in_lot))
            amount = 1 if amount < 0 else amount
            logger.info("We will buy %s lots", amount)
            return amount

    def calculate_sell_signal(self, total_buy_price, instrument_id, quantity_lots, average_bought_price):
        buy_price_float = to_float(average_bought_price) if average_bought_price is not None else to_float(
            total_buy_price) / quantity_lots
        stop_position_price = buy_price_float * (1 - self.stop_loss_profitability)

        last_prices = self.sync_client.market_data.get_last_prices(instrument_id=[instrument_id])
        sell_price = last_prices.last_prices[0].price
        profit_with_commissions = (
                to_float(sell_price) - buy_price_float - buy_price_float * self.commission - to_float(
            sell_price) * self.commission
        )
        margin = 100 * profit_with_commissions / buy_price_float
        logger.debug(
            "Buy Price: %s, Current Price: %s, Total profit(with comissions) : %s, Margin(with comissions): %s%%",
            round(buy_price_float, 2),
            round(to_float(sell_price), 2),
            round(profit_with_commissions * quantity_lots, 2),
            round(margin, 2)
        )
        is_sell_signal = margin > self.target_daily_profitability * 100 or to_float(sell_price) <= stop_position_price
        if is_sell_signal:
            logger.info(
                "Buy Price: %s, Current Price: %s, Total profit(with comissions) : %s, Margin(with comissions): %s%%",
                round(buy_price_float, 2),
                round(to_float(sell_price), 2),
                round(profit_with_commissions * quantity_lots, 2),
                round(margin, 2)
            )


        return is_sell_signal
