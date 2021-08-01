from scripts.constants import *
from scripts.configuration import *
from scripts.coordinape_enums import CoordinapeGroup, ExclusionMethod, FundingMethod
from fractions import Fraction
from pytest import approx
import math
from brownie import *


class Disbursement:
    def __init__(self, reward_in_usd, funding_method, contracts, buffer):
        self.reward_in_usd = reward_in_usd
        self.funding_method = funding_method
        self.contracts = contracts
        self.buffer = buffer
        self.yfi_in_usd = self.contracts.yfi_usd_oracle.latestAnswer() / 10 ** self.contracts.yfi_usd_oracle.decimals()
        self.yfi_allocated = (self.reward_in_usd / self.yfi_in_usd) * self.contracts.yfi_decimal_multiplicand
        self.yvyfi_before = self.contracts.yvyfi.balanceOf(self.contracts.safe.account)
        self.treasury_yvyfi_before = self.contracts.yvyfi.balanceOf(self.contracts.treasury)
        self.yfi_before =  self.contracts.yfi.balanceOf(self.contracts.safe.account)
        self.yvyfi_ratio = self.contracts.yvyfi.totalAssets() / self.contracts.yvyfi.totalSupply()
        self.yvyfi_to_disperse = Wei((self.yfi_allocated *  self.contracts.yvyfi.totalSupply()) /  self.contracts.yvyfi.totalAssets())
        self.amounts = None
        self.yvyfi_removed_by_exclusion = None
        self.yfi_to_deposit = self.yfi_allocated
        self.yvyfi_to_transfer = self.yvyfi_to_disperse


    def needs_yvyfi(self):
        return self.yvyfi_before / self.yvyfi_to_disperse < EXPECTED_YVYFI_BUFFER


    def needs_yfi(self):
        return self.yfi_before / self.yfi_allocated < EXPECTED_YVYFI_BUFFER


    # After this, treasury should have enough yvYFI
    def market_buy(self):
        # do market buy
        usdc_to_swap = self.reward_in_usd * 10 ** self.contracts.usdc.decimals()
        usdc_balance = self.contracts.usdc.balanceOf(self.contracts.safe.account)
        if usdc_balance < usdc_to_swap:
            usdc_need = usdc_to_swap - usdc_balance
            assert self.contracts.treasury.governance() == self.contracts.safe.account
            assert self.contracts.usdc.balanceOf(self.contracts.treasury) >= usdc_need
            self.contracts.treasury.toGovernance(self.contracts.usdc, usdc_need)

        if self.needs_yfi() and self.needs_yvyfi():
            usdc_to_swap += EXPECTED_YVYFI_BUFFER * usdc_to_swap * self.yvyfi_ratio

        self.contracts.usdc.approve(self.contracts.sushiswap, usdc_to_swap)
        self.contracts.sushiswap.swapExactTokensForTokens(usdc_to_swap, 0, [self.contracts.usdc, self.contracts.weth, self.contracts.yfi], self.contracts.safe.account, 2**256-1)
        self.yfi_in_usd = self.reward_in_usd / (self.yfi_allocated / self.contracts.yfi_decimal_multiplicand)
        self.yfi_before = self.contracts.yfi.balanceOf(self.contracts.safe.account)
        self.deposit_yfi()


    def transfer_yfi_from_treasury(self):
        assert self.contracts.treasury.governance() == self.contracts.safe.account

        yfi_to_transfer = self.yfi_allocated
        if self.needs_yfi() and self.needs_yvyfi():
            yfi_to_transfer += self.yfi_allocated * EXPECTED_YVYFI_BUFFER * self.yvyfi_ratio

        assert self.contracts.yfi.balanceOf(self.contracts.treasury) >= yfi_to_transfer
        self.contract.treasury.toGovernance(self.contracts.yfi, yfi_to_transfer)
        self.yfi_before = self.contracts.yfi.balanceOf(self.contracts.safe.account)
        self.deposit_yfi()


    def deposit_yfi(self):
        safe_yfi_balance = self.contracts.yfi.balanceOf(self.contracts.safe.account)
        safe_yvyfi_balance = self.contracts.yvyfi.balanceOf(self.contracts.safe.account)
        if self.needs_yvyfi():
            self.yfi_to_deposit += self.yfi_to_deposit * EXPECTED_YVYFI_BUFFER * self.yvyfi_ratio

        assert safe_yfi_balance >= self.yfi_to_deposit
        self.contracts.yfi.approve(self.contracts.yvyfi, self.yfi_to_deposit)
        self.contracts.yvyfi.deposit(self.yfi_to_deposit)


    def deposit_all_yfi_to_yvyfi(self):
        yfi_balance = self.contracts.yfi.balanceOf(self.contracts.safe.account)
        self.contracts.yfi.approve(self.contracts.yvyfi, yfi_balance)
        self.contracts.yvyfi.deposit(yfi_balance)
        assert self.yvyfi_to_disperse <= self.contracts.yvyfi.balanceOf(self.contracts.safe.account)


    def transfer_yvyfi_from_treasury(self):
        self.contracts.treasury = self.contracts.safe.contract(YEARN_TREASURY_ADDRESS)
        assert self.contracts.treasury.governance() == self.contracts.safe.account
        self.yvyfi_to_transfer = self.yvyfi_to_disperse
        if self.needs_yvyfi():
            self.yvyfi_to_transfer +=self. yvyfi_to_transfer * EXPECTED_YVYFI_BUFFER
        assert self.contracts.yvyfi.balanceOf(self.contracts.treasury) >= self.yvyfi_to_transfer
        self.contracts.treasury.toGovernance(self.contracts.yvyfi, self.yvyfi_to_transfer)


    def prep_reward(self):
        if self.funding_method == FundingMethod.MARKET_BUY:
            self.market_buy()
        elif self.funding_method == FundingMethod.TRANSFER_YFI_FROM_TREASURY:
            self.transfer_yfi_from_treasury()
        elif self.funding_method == FundingMethod.DEPOSIT_YFI:
            self.deposit_yfi()
        elif self.funding_method == FundingMethod.DEPOSIT_ALL_YFI_TO_YVYFI:
            self.deposit_all_yfi_to_yvyfi()
        elif self.funding_method == FundingMethod.TRANSFER_YVYFI_FROM_TREASURY:
            self.transfer_yvyfi_from_treasury()
        
        self.check_buffer()
    
    def check_buffer(self):
        # Make sure we have a buffer of yvyfi to avoid some errors
        percentage_yvyfi_buffer = (
            self.contracts.yvyfi.balanceOf(self.contracts.safe.account) - self.yvyfi_to_disperse
        ) / self.yvyfi_to_disperse

        assert (
            percentage_yvyfi_buffer >= EXPECTED_YVYFI_BUFFER
            ), f"This TX could fail if yvYFI's pricePerShare changes before execution.\nThe yvyfi buffer is only {percentage_yvyfi_buffer}%\n"
    
    def check_asserts(self):
        if self.funding_method == FundingMethod.DEPOSIT_YFI or self.funding_method == FundingMethod.TRANSFER_YFI_FROM_TREASURY or self.funding_method == FundingMethod.MARKET_BUY:
            # we should have the yvyfi we had before plus any extra after we dispersed
            yvyfi_approx_after = self.yvyfi_before + (self.yfi_to_deposit / self.yvyfi_ratio - self.yvyfi_to_disperse)
            # Make sure we sent all the new yvYFI and only used as much YFI as expected
            assert float(self.contracts.yvyfi.balanceOf(self.contracts.safe.account)) == approx(yvyfi_approx_after, 0.0001)
            assert float(self.yfi_before - self.yfi_to_deposit) == approx(self.contracts.yfi.balanceOf(self.contracts.safe.account), 0.0001)
        elif self.funding_method == FundingMethod.TRANSFER_YVYFI:
            # Make sure we didn't use YFI for some reason and only used as much yvYFI as expected
            assert self.yfi_before == self.contracts.yfi.balanceOf(self.contracts.safe.account)
            assert self.yvyfi_before - self.yvyfi_to_disperse == self.contracts.yvyfi.balanceOf(self.contracts.safe.account)
        elif self.funding_method == FundingMethod.TRANSFER_YVYFI_FROM_TREASURY:
            # Make sure we didn't use YFI and only used the yvYFI from the treasury
            assert self.yfi_before == self.contracts.yfi.balanceOf(self.contracts.safe.account)
            assert self.yvyfi_before + self.yvyfi_removed_by_exclusion == self.contracts.yvyfi.balanceOf(
                self.contracts.safe.account
            )
            assert self.treasury_yvyfi_before - (
                self.yvyfi_to_disperse + self.yvyfi_removed_by_exclusion
            ) == self.contracts.yvyfi.balanceOf(self.contracts.treasury)
    
    def get_amounts(self, coordinape_group_epoch):
        # Converting here will leave some dust
        rewarded_contributors_this_epoch = coordinape_group_epoch.get_rewarded_contributors_this_epoch()
        self.amounts = [
            Wei(
                self.yvyfi_to_disperse
                * (Fraction(contributor["received"]) / Fraction(coordinape_group_epoch.get_total_votes()))
            )
            for contributor in rewarded_contributors_this_epoch
        ]

        # REMOVE_SHARE means we will remove the excluded
        # contributors and not distribute their share. Subtract their
        # amount from the yvyfi to disperse.
        self.yvyfi_removed_by_exclusion = 0
        if coordinape_group_epoch.exclusion_type == ExclusionMethod.REMOVE_SHARE:
            for contributor_address in coordinape_group_epoch.exclusion_list:
                contributor_to_exclude = next(
                    x
                    for x in rewarded_contributors_this_epoch
                    if x["address"] == contributor_address
                )
                index = rewarded_contributors_this_epoch.index(contributor_to_exclude)
                del rewarded_contributors_this_epoch[index]
                self.yvyfi_removed_by_exclusion += self.amounts[index]
                self.yvyfi_to_disperse -= self.amounts[index]
                yfi_allocated -= self.amounts[index] * (
                    self.contracts.yvyfi.totalAssets() / self.contracts.yvyfi.yvyfi.totalSupply()
                )
                del self.amounts[index]

        # Dust should be less than or equal to 1 Wei per contributor due to the previous floor
        dust = self.yvyfi_to_disperse - sum(self.amounts)
        assert dust <= len(coordinape_group_epoch.get_rewarded_contributors_this_epoch())

        # Some lucky folks can get some dust, woot
        for i in range(math.floor(dust)):
            self.amounts[i] += 1

        assert sum(self.amounts) == self.yvyfi_to_disperse
        assert float(self.yfi_allocated) == approx(
            self.yvyfi_to_disperse * (self.contracts.yvyfi.totalAssets() / self.contracts.yvyfi.totalSupply()),
            Wei("0.000001 ether") / self.contracts.yfi_decimal_multiplicand,
        )

        return self.amounts