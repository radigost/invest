import logging

from tinkoff.invest import InstrumentType, InstrumentIdType

from utils import to_float

logger = logging.getLogger(__name__)


class Analytic():
    def __init__(self, sync_client, account_id):
        self.stop_loss_profitability = 0.01
        self.buy_free_capital_percentage = 0.1
        self.commission = 0.0004
        self.sync_client = sync_client
        self.account_id = account_id

    def get_instrument_of_the_strategy(self):
        GAZPROM_SHARES = '962e2a95-02a9-4171-abd7-aa198dbe643a'
        YANDEX_SHARES = '10e17a87-3bce-4a1f-9dfc-720396f98a3c'
        return YANDEX_SHARES

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
            logger.info("We will by %s lots", amount)
            return amount

    def get_compared_difference(self, bought_price, instrument_id):
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
