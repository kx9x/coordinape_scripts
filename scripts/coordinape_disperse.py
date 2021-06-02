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


def contributors_from_epoch(group, epoch):
    endpoint = EPOCH_RESULTS_ENDPOINT_FORMAT.format(group.value, epoch)
    r = requests.get(endpoint)
    buff = io.StringIO(r.text)
    return list(csv.DictReader(buff))


def get_reward_in_usd(group, epoch):
    reward_in_usd = DEFAULT_USD_REWARD_DICT[group][epoch]
    if group in LEFTOVER_DICT and epoch - 1 in LEFTOVER_DICT[group]:
        reward_in_usd += LEFTOVER_DICT[group][epoch - 1]
    return reward_in_usd


def get_rewarded_contributors_this_epoch(contributors_this_epoch, exclusion_list, exclusion_type):
    rewarded_contributors_this_epoch = [
        contributor
        for contributor in contributors_this_epoch
        if int(contributor["received"]) > 0
    ]

    # REDISTRIBUTE_SHARE means we will treat excluded folks as never being in the pool
    # and their share will be distributed to others based on the other votes
    if exclusion_type == ExclusionMethod.REDISTRIBUTE_SHARE:
        rewarded_contributors_this_epoch = [
            contributor
            for contributor in rewarded_contributors_this_epoch
            if contributor["address"] not in exclusion_list
        ]
    
    return rewarded_contributors_this_epoch


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


def disperse(
    group,
    epoch,
    safe=YCHAD_ETH,
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

    # Figure out the reward and handle leftovers from previous epoch
    reward_in_usd = get_reward_in_usd(group, epoch)

    contributors_this_epoch = contributors_from_epoch(group, epoch)
    rewarded_contributors_this_epoch = get_rewarded_contributors_this_epoch(contributors_this_epoch, exclusion_list, exclusion_type)

    assert (
        len(rewarded_contributors_this_epoch) > 0
    ), f"{group.name}'s epoch #{epoch} does not have any contributors with votes received..."

    total_votes = 0
    for contributor in rewarded_contributors_this_epoch:
        total_votes += int(contributor["received"])

    safe = ApeSafe(safe)

    yfi = safe.contract(YFI_ADDRESS)
    yfi_decimal_multiplicand = 10 ** yfi.decimals()


    yvyfi = safe.contract(YEARN_VAULT_YFI_ADDRESS)
    disperse = safe.contract(DISPERSE_APP_ADDRESS)

    if funding_method == FundingMethod.MARKET_BUY:
        sushiswap = safe.contract(SUSHISWAP_ADDRESS)
        usdc = safe.contract(USDC_ADDRESS)
        weth = safe.contract(WETH_ADDRESS)
        usdc_to_swap = reward_in_usd * 10 ** usdc.decimals()
        usdc_balance = usdc.balanceOf(safe.account)
        if usdc_balance < usdc_to_swap:
            usdc_need = usdc_to_swap - usdc_balance
            treasury = safe.contract(YEARN_TREASURY_ADDRESS)
            assert treasury.governance() == safe.account
            assert usdc.balanceOf(treasury) >= usdc_need
            treasury.toGovernance(usdc, usdc_need)

        usdc.approve(sushiswap, usdc_to_swap)
        yfi_before = yfi.balanceOf(safe.account)
        sushiswap.swapExactTokensForTokens(usdc_to_swap, 0, [usdc, weth, yfi], safe.account, 2**256-1)
        yfi_allocated = yfi.balanceOf(safe.account) - yfi_before
        yfi_in_usd = reward_in_usd / (yfi_allocated / yfi_decimal_multiplicand)
        funding_method = FundingMethod.DEPOSIT_YFI
    else:
        # Use price oracle to find how much YFI to allocate
        yfi_usd_oracle = safe.contract(YFI_USD_ORACLE_ADDRESS)
        yfi_in_usd = yfi_usd_oracle.latestAnswer() / 10 ** yfi_usd_oracle.decimals()
        yfi_allocated = (reward_in_usd / yfi_in_usd) * yfi_decimal_multiplicand

    yvyfi_before = yvyfi.balanceOf(safe.account)
    yfi_before = yfi.balanceOf(safe.account)
    yvyfi_to_disperse = Wei((yfi_allocated * yvyfi.totalSupply()) / yvyfi.totalAssets())

    if funding_method == FundingMethod.DEPOSIT_YFI:
        safe_yfi_balance = yfi.balanceOf(safe.account)
        assert safe_yfi_balance >= yfi_allocated
        yfi.approve(yvyfi, yfi_allocated)
        yvyfi.deposit(yfi_allocated)
        yvyfi_to_disperse = Wei(yvyfi.balanceOf(safe.account) - yvyfi_before)

        percentage_yvyfi_buffer = (
            yvyfi.balanceOf(safe.account) - yvyfi_to_disperse
        ) / yvyfi_to_disperse

        within_buffer = percentage_yvyfi_buffer >= EXPECTED_YVYFI_BUFFER
        if safe.account == treasury.governance() and not within_buffer:
            yvyfi_buffer_needed = Wei(Fraction(EXPECTED_YVYFI_BUFFER - percentage_yvyfi_buffer) * yvyfi_to_disperse)
            assert yvyfi.balanceOf(treasury) >= yvyfi_buffer_needed
            treasury.toGovernance(yvyfi, yvyfi_buffer_needed)
        else:
            assert (
                within_buffer
            ), f"This TX could fail if yvYFI's pricePerShare changes before execution.\nThe yvyfi buffer is only {percentage_yvyfi_buffer}%\n"
    elif funding_method == FundingMethod.TRANSFER_YVYFI_FROM_TREASURY:
        treasury = safe.contract(YEARN_TREASURY_ADDRESS)
        assert treasury.governance() == safe.account
        assert yvyfi.balanceOf(treasury) >= yvyfi_to_disperse
        treasury_yvyfi_before = yvyfi.balanceOf(treasury)
        treasury.toGovernance(yvyfi, yvyfi_to_disperse)

    if (
        funding_method == FundingMethod.TRANSFER_YVYFI
        or FundingMethod.TRANSFER_YVYFI_FROM_TREASURY
    ):
        assert yvyfi.balanceOf(safe.account) >= yvyfi_to_disperse

    # Converting here will leave some dust
    amounts = [
        Wei(
            yvyfi_to_disperse
            * (Fraction(contributor["received"]) / Fraction(total_votes))
        )
        for contributor in rewarded_contributors_this_epoch
    ]

    # REMOVE_SHARE means we will remove the excluded
    # contributors and not distribute their share. Subtract their
    # amount from the yvyfi to disperse.
    yvyfi_removed_by_exclusion = 0
    if exclusion_type == ExclusionMethod.REMOVE_SHARE:
        for contributor_address in exclusion_list:
            contributor_to_exclude = next(
                x
                for x in rewarded_contributors_this_epoch
                if x["address"] == contributor_address
            )
            index = rewarded_contributors_this_epoch.index(contributor_to_exclude)
            del rewarded_contributors_this_epoch[index]
            yvyfi_removed_by_exclusion += amounts[index]
            yvyfi_to_disperse -= amounts[index]
            yfi_allocated -= amounts[index] * (
                yvyfi.totalAssets() / yvyfi.totalSupply()
            )
            del amounts[index]

    # Dust should be less than or equal to 1 Wei per contributor due to the previous floor
    dust = yvyfi_to_disperse - sum(amounts)
    assert dust <= len(rewarded_contributors_this_epoch)

    # Some lucky folks can get some dust, woot
    for i in range(math.floor(dust)):
        amounts[i] += 1

    assert sum(amounts) == yvyfi_to_disperse
    assert float(yfi_allocated) == approx(
        yvyfi_to_disperse * (yvyfi.totalAssets() / yvyfi.totalSupply()),
        Wei("0.000001 ether") / yfi_decimal_multiplicand,
    )

    yvyfi.approve(disperse, sum(amounts))
    recipients = [contributor["address"] for contributor in rewarded_contributors_this_epoch]
    recipients_yvfi_before = [yvyfi.balanceOf(recipient) for recipient in recipients]

    disperse.disperseToken(yvyfi, recipients, amounts)
    history[-1].info()

    if funding_method == FundingMethod.DEPOSIT_YFI:
        # Make sure we sent all the new yvYFI and only used as much YFI as expected
        assert abs(yvyfi.balanceOf(safe.account) - yvyfi_before) <= EXPECTED_YVYFI_BUFFER * yfi_allocated
        assert yfi_before - yfi_allocated == yfi.balanceOf(safe.account)
    elif funding_method == FundingMethod.TRANSFER_YVYFI:
        # Make sure we didn't use YFI for some reason and only used as much yvYFI as expected
        assert yfi_before == yfi.balanceOf(safe.account)
        assert yvyfi_before - yvyfi_to_disperse == yvyfi.balanceOf(safe.account)
    elif funding_method == FundingMethod.TRANSFER_YVYFI_FROM_TREASURY:
        # Make sure we didn't use YFI and only used the yvYFI from the treasury
        assert yfi_before == yfi.balanceOf(safe.account)
        assert yvyfi_before + yvyfi_removed_by_exclusion == yvyfi.balanceOf(
            safe.account
        )
        assert treasury_yvyfi_before - (
            yvyfi_to_disperse + yvyfi_removed_by_exclusion
        ) == yvyfi.balanceOf(treasury)

    # For each recipient, make sure their yvYFI amount increased by the expected amount
    for recipient, yvyfi_before, amount in zip(
        recipients, recipients_yvfi_before, amounts
    ):
        assert yvyfi.balanceOf(recipient) == yvyfi_before + amount

    # Print out a table
    price_per_share = yvyfi.pricePerShare() / yfi_decimal_multiplicand
    table = make_table(rewarded_contributors_this_epoch, amounts, yfi_decimal_multiplicand, yfi_in_usd, price_per_share, total_votes)

    print(
        f"{group.name} epoch #{epoch}\nDistributing ${reward_in_usd}\nYFI price ${yfi_in_usd}\nyvYFI price per share {price_per_share}\n"
    )
    print(table)

    safe_tx = safe.multisend_from_receipts()
    safe.preview(safe_tx)
    safe.post_transaction(safe_tx)


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
        FundingMethod.MARKET_BUY,
    )


def disperse_strategist_2():
    disperse(
        CoordinapeGroup.YSTRATEGIST, 2, BRAIN_YCHAD_ETH, FundingMethod.TRANSFER_YVYFI
    )


def disperse_strategist_3():
    disperse(
        CoordinapeGroup.YSTRATEGIST, 3, BRAIN_YCHAD_ETH, FundingMethod.TRANSFER_YVYFI
    )


if __name__ == "__main__":
    network.connect("mainnet-fork")
    disperse_yearn_community_epoch_4()
