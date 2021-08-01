from scripts.constants import *
from scripts.configuration import *
from scripts.coordinape_enums import CoordinapeGroup, ExclusionMethod, FundingMethod
from ape_safe import ApeSafe

class Contracts:
    def __init__(self, safe_name):
        self.safe = ApeSafe(safe_name)
        self.yfi = self.safe.contract(YFI_ADDRESS)
        self.yfi_decimal_multiplicand = 10 ** self.yfi.decimals()

        self.yvyfi = self.safe.contract(YEARN_VAULT_YFI_ADDRESS)
        self.disperse = self.safe.contract(DISPERSE_APP_ADDRESS)
        self.treasury = self.safe.contract(YEARN_TREASURY_ADDRESS)

        self.sushiswap = self.safe.contract(SUSHISWAP_ADDRESS)
        self.usdc = self.safe.contract(USDC_ADDRESS)
        self.weth = self.safe.contract(WETH_ADDRESS)
        self.yfi_usd_oracle = self.safe.contract(YFI_USD_ORACLE_ADDRESS)