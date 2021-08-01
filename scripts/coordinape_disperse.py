import sys
import pathlib
sys.path.append(str(pathlib.Path(__file__).parent.parent.absolute()))
import csv
import io
import requests
from fractions import Fraction
from ape_safe import ApeSafe
from brownie import *
import math
from tabulate import tabulate
from pytest import approx
from scripts.constants import *
from scripts.configuration import *
from scripts.coordinape_enums import CoordinapeGroup, ExclusionMethod, FundingMethod


class CoordinapeGroupEpoch:
    def __init__(self, group, epoch, exclusion_list, exclusion_type):
        self.group = group
        self.epoch = epoch
        self.exclusion_list = exclusion_list
        self.exclusion_type = exclusion_type

    def get_contributors_from_epoch(self):
        endpoint = EPOCH_RESULTS_ENDPOINT_FORMAT.format(self.group.value, self.epoch)
        r = requests.get(endpoint)
        buff = io.StringIO(r.text)
        return list(csv.DictReader(buff))


    def get_reward_in_usd(self):
        reward_in_usd = DEFAULT_USD_REWARD_DICT[self.group][self.epoch]
        if self.group in LEFTOVER_DICT and self.epoch - 1 in LEFTOVER_DICT[self.group]:
            reward_in_usd += LEFTOVER_DICT[self.group][self.epoch - 1]
        return reward_in_usd


    def get_rewarded_contributors_this_epoch(self):
        rewarded_contributors_this_epoch = [
            contributor
            for contributor in self.get_contributors_from_epoch()
            if int(contributor["received"]) > 0
        ]

        # REDISTRIBUTE_SHARE means we will treat excluded folks as never being in the pool
        # and their share will be distributed to others based on the other votes
        if self.exclusion_type == ExclusionMethod.REDISTRIBUTE_SHARE:
            rewarded_contributors_this_epoch = [
                contributor
                for contributor in rewarded_contributors_this_epoch
                if contributor["address"] not in self.exclusion_list
            ]

        return rewarded_contributors_this_epoch


    def get_total_votes(self):
        total_votes = 0
        for contributor in self.get_rewarded_contributors_this_epoch():
            total_votes += int(contributor["received"])
        return total_votes

def make_table(contributors_this_epoch, amounts, yfi_decimal_multiplicand, yfi_in_usd, price_per_share, total_votes):
    l = [
        [
            contributor["name"],
            contributor["address"][:6],
            contributor["received"],
            amount / yfi_decimal_multiplicand,
            "${:0.2f}".format(
                amount / yfi_decimal_multiplicand * yfi_in_usd * price_per_share
            ),
        ]
        for contributor, amount in zip(contributors_this_epoch, amounts)
    ]

    l.append(
        [
            "TOTAL",
            "------",
            total_votes,
            sum(amounts) / yfi_decimal_multiplicand,
            "${:0.2f}".format(
                sum(amounts) / yfi_decimal_multiplicand * yfi_in_usd * price_per_share
            ),
        ]
    )

    table = tabulate(
        l,
        headers=["Name", "Address", "Received Votes", "Amount yvYFI", "Amount USD"],
        tablefmt="orgtbl",
    )

    return table


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
        self.deposit_yfi()


    def transfer_yfi_from_treasury(self):
        assert self.contracts.treasury.governance() == self.contract.safe.account

        yfi_to_transfer = self.yfi_allocated
        if self.needs_yfi() and self.needs_yvyfi():
            yfi_to_transfer += self.yfi_allocated * EXPECTED_YVYFI_BUFFER * self.yvyfi_ratio

        assert self.contracts.yfi.balanceOf(treasury) >= yfi_to_transfer
        self.contract.treasury.toGovernance(yfi, yfi_to_transfer)
        self.deposit_yfi()


    def deposit_yfi(self):
        safe_yfi_balance = self.contracts.yfi.balanceOf(self.contracts.safe.account)
        safe_yvyfi_balance = self.contracts.yvyfi.balanceOf(self.contracts.safe.account)
        yfi_to_deposit = self.yfi_allocated
        if self.needs_yvyfi():
            yfi_to_deposit += yfi_to_deposit * EXPECTED_YVYFI_BUFFER * self.yvyfi_ratio

        assert safe_yfi_balance >= yfi_to_deposit
        self.contracts.yfi.approve(self.contracts.yvyfi, yfi_to_deposit)
        self.contracts.yvyfi.deposit(yfi_to_deposit)


    def deposit_all_yfi_to_yvyfi(self):
        yfi_balance = self.contracts.yfi.balanceOf(self.contracts.safe.account)
        self.contracts.yfi.approve(self.contracts.yvyfi, yfi_balance)
        self.contracts.yvyfi.deposit(yfi_balance)
        assert self.yvyfi_to_disperse <= self.contracts.yvyfi.balanceOf(self.contracts.safe.account)


    def transfer_yvyfi_from_treasury(self):
        self.contracts.treasury = self.contracts.safe.contract(YEARN_TREASURY_ADDRESS)
        assert self.contracts.treasury.governance() == self.contracts.safe.account
        yvyfi_to_transfer = self.yvyfi_to_disperse
        if self.needs_yvyfi():
            yvyfi_to_transfer += yvyfi_to_transfer * EXPECTED_YVYFI_BUFFER
        assert self.contracts.yvyfi.balanceOf(self.contracts.treasury) >= yvyfi_to_transfer
        self.contracts.treasury.toGovernance(self.contracts.yvyfi, yvyfi_to_transfer)


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
    
    def check_asserts(self, yvyfi_removed_by_exclusion):
        if self.funding_method == FundingMethod.DEPOSIT_YFI or self.funding_method == FundingMethod.TRANSFER_YFI_FROM_TREASURY or self.funding_method == FundingMethod.MARKET_BUY:
            # Make sure we sent all the new yvYFI and only used as much YFI as expected
            assert float(self.contracts.yvyfi.balanceOf(self.contracts.safe.account) - self.yvyfi_before) == approx(EXPECTED_YVYFI_BUFFER * self.yfi_allocated, 0.0001)
            assert float(self.yfi_before - self.yfi_allocated) == approx(self.contracts.yfi.balanceOf(self.contracts.safe.account), 0.0001)
        elif self.funding_method == FundingMethod.TRANSFER_YVYFI:
            # Make sure we didn't use YFI for some reason and only used as much yvYFI as expected
            assert self.yfi_before == self.contracts.yfi.balanceOf(self.contracts.safe.account)
            assert self.yvyfi_before - self.yvyfi_to_disperse == self.contracts.yvyfi.balanceOf(self.contracts.safe.account)
        elif self.funding_method == FundingMethod.TRANSFER_YVYFI_FROM_TREASURY:
            # Make sure we didn't use YFI and only used the yvYFI from the treasury
            assert self.yfi_before == self.contracts.yfi.balanceOf(self.contracts.safe.account)
            assert self.yvyfi_before + yvyfi_removed_by_exclusion == self.contracts.yvyfi.balanceOf(
                self.contracts.safe.account
            )
            assert self.treasury_yvyfi_before - (
                self.yvyfi_to_disperse + yvyfi_removed_by_exclusion
            ) == self.contracts.yvyfi.balanceOf(self.contracts.treasury)
    
    def get_amounts(self, coordinape_group_epoch):
        # Converting here will leave some dust
        rewarded_contributors_this_epoch = coordinape_group_epoch.get_rewarded_contributors_this_epoch()
        amounts = [
            Wei(
                self.yvyfi_to_disperse
                * (Fraction(contributor["received"]) / Fraction(coordinape_group_epoch.get_total_votes()))
            )
            for contributor in rewarded_contributors_this_epoch
        ]

        # REMOVE_SHARE means we will remove the excluded
        # contributors and not distribute their share. Subtract their
        # amount from the yvyfi to disperse.
        yvyfi_removed_by_exclusion = 0
        if coordinape_group_epoch.exclusion_type == ExclusionMethod.REMOVE_SHARE:
            for contributor_address in coordinape_group_epoch.exclusion_list:
                contributor_to_exclude = next(
                    x
                    for x in rewarded_contributors_this_epoch
                    if x["address"] == contributor_address
                )
                index = rewarded_contributors_this_epoch.index(contributor_to_exclude)
                del rewarded_contributors_this_epoch[index]
                yvyfi_removed_by_exclusion += amounts[index]
                self.yvyfi_to_disperse -= amounts[index]
                yfi_allocated -= amounts[index] * (
                    self.contracts.yvyfi.totalAssets() / self.contracts.yvyfi.yvyfi.totalSupply()
                )
                del amounts[index]

        # Dust should be less than or equal to 1 Wei per contributor due to the previous floor
        dust = self.yvyfi_to_disperse - sum(amounts)
        assert dust <= len(coordinape_group_epoch.get_rewarded_contributors_this_epoch())

        # Some lucky folks can get some dust, woot
        for i in range(math.floor(dust)):
            amounts[i] += 1

        assert sum(amounts) == self.yvyfi_to_disperse
        assert float(self.yfi_allocated) == approx(
            self.yvyfi_to_disperse * (self.contracts.yvyfi.totalAssets() / self.contracts.yvyfi.totalSupply()),
            Wei("0.000001 ether") / self.contracts.yfi_decimal_multiplicand,
        )

        return (amounts, yvyfi_removed_by_exclusion)


def disperse(
    group,
    epoch,
    safe_name=YCHAD_ETH,
    funding_method=FundingMethod.DEPOSIT_YFI,
    exclusion_list=[],
    exclusion_type=ExclusionMethod.REDISTRIBUTE_SHARE,
):
    assert (
        group in DEFAULT_USD_REWARD_DICT
    ), f"{group.name} does not have a default usd reward entry"
    assert (
        epoch in DEFAULT_USD_REWARD_DICT[group]
    ), f"{group.name}'s epoch #{epoch} does not have a default usd reward entry"

    coordinape_group_epoch = CoordinapeGroupEpoch(group, epoch, exclusion_list, exclusion_type)
    # Figure out the reward and handle leftovers from previous epoch
    reward_in_usd = coordinape_group_epoch.get_reward_in_usd()
    rewarded_contributors_this_epoch = coordinape_group_epoch.get_rewarded_contributors_this_epoch()

    assert (
        len(rewarded_contributors_this_epoch) > 0
    ), f"{group.name}'s epoch #{epoch} does not have any contributors with votes received..."

    contracts = Contracts(safe_name)
    disbursement = Disbursement(reward_in_usd, funding_method, contracts, EXPECTED_YVYFI_BUFFER)
    disbursement.prep_reward()

    amounts, yvyfi_removed_by_exclusion = disbursement.get_amounts(coordinape_group_epoch)

    contracts.yvyfi.approve(contracts.disperse, sum(amounts))
    recipients = [contributor["address"] for contributor in rewarded_contributors_this_epoch]
    recipients_yvfi_before = [contracts.yvyfi.balanceOf(recipient) for recipient in recipients]

    contracts.disperse.disperseToken(contracts.yvyfi, recipients, amounts)
    history[-1].info()

    disbursement.check_asserts(yvyfi_removed_by_exclusion)

    # For each recipient, make sure their yvYFI amount increased by the expected amount
    for recipient, yvyfi_before, amount in zip(
        recipients, recipients_yvfi_before, amounts
    ):
        assert contracts.yvyfi.balanceOf(recipient) == yvyfi_before + amount

    # Print out a table
    price_per_share = contracts.yvyfi.pricePerShare() / contracts.yfi_decimal_multiplicand
    table = make_table(rewarded_contributors_this_epoch, amounts, contracts.yfi_decimal_multiplicand, disbursement.yfi_in_usd, price_per_share, coordinape_group_epoch.get_total_votes())

    print(
        f"{group.name} epoch #{epoch}\nDistributing ${reward_in_usd}\nYFI price ${disbursement.yfi_in_usd}\nyvYFI price per share {price_per_share}\n"
    )
    print(table)

    safe_tx = contracts.safe.multisend_from_receipts()
    contracts.safe.preview(safe_tx)
    contracts.safe.post_transaction(safe_tx)


def disperse_yearn_community_epoch_3():
    # Exclude Orb, redistribute his share
    # Transfer yvyfi from treasury
    disperse(
        CoordinapeGroup.COMMUNITY,
        3,
        YCHAD_ETH,
        FundingMethod.TRANSFER_YVYFI_FROM_TREASURY,
        ["0x710295b5f326c2e47e6dd2e7f6b5b0f7c5ac2f24"],
        ExclusionMethod.REDISTRIBUTE_SHARE,
    )


def disperse_yearn_community_epoch_4():
    disperse(
        CoordinapeGroup.COMMUNITY,
        4,
        YCHAD_ETH,
        FundingMethod.DEPOSIT_YFI,
    )

def disperse_yearn_community_epoch_5():
    disperse(
        CoordinapeGroup.COMMUNITY,
        5,
        YCHAD_ETH,
        FundingMethod.DEPOSIT_YFI,
    )


def disperse_strategist_2():
    disperse(
        CoordinapeGroup.YSTRATEGIST, 2, BRAIN_YCHAD_ETH, FundingMethod.TRANSFER_YVYFI
    )


def disperse_strategist_3():
    disperse(
        CoordinapeGroup.YSTRATEGIST, 3, BRAIN_YCHAD_ETH, FundingMethod.TRANSFER_YVYFI
    )


def disperse_strategist_4():
    disperse(CoordinapeGroup.YSTRATEGIST, 4, BRAIN_YCHAD_ETH, FundingMethod.DEPOSIT_ALL_YFI_TO_YVYFI)


if __name__ == "__main__":
    network.connect("mainnet-fork")
    disperse_yearn_community_epoch_3()
