from enum import Enum

class CoordinapeGroup(Enum):
    COMMUNITY = 1
    YSTRATEGIST = 2
    _003 = 3
    SUSHI = 4
    COORDINAPETESTING = 5
    CREAM = 6
    GITCOIN = 7


class ExclusionMethod(Enum):
    REDISTRIBUTE_SHARE = 1  # An excluded person's share is redistributed to the pool
    REMOVE_SHARE = 2  # An excluded person's share is removed from the pool of funds


class FundingMethod(Enum):
    DEPOSIT_YFI = 1
    TRANSFER_YVYFI = 2
    TRANSFER_YVYFI_FROM_TREASURY = 3
    MARKET_BUY = 4
