import csv
import io
import requests
from fractions import Fraction
from ape_safe import ApeSafe
from brownie import *
import math
from enum import Enum
from tabulate import tabulate

EPOCH_RESULTS_ENDPOINT_FORMAT = "https://coordinape.me/api/{0}/csv?epoch={1}"

class CoordinapeGroup(Enum):
    COMMUNITY = 1
    YSTRATEGIST = 2
    _003 = 3
    SUSHI = 4
    COORDINAPETESTING = 5
    CREAM = 6
    GITCOIN = 7


class ExclusionMethod(Enum):
    REDISTRIBUTE_SHARE = 1 # An excluded person's share is redistributed to the pool
    REMOVE_SHARE = 2 # An excluded person's share is removed from the pool of funds


class FundingMethod(Enum):
    DEPOSIT_YFI = 1
    TRANSFER_YVYFI = 2
    TRANSFER_YVYFI_FROM_TREASURY = 3


# For epoch 3 in the yearn community, we need to add the leftover USDC from epoch 2.
# Below we can see that 33,098 was awarded, meaning 40,000 - 33,098 = $6902 is leftover:
# https://etherscan.io/tx/0xf401d432dcaaea39e1b593379d3d63dcdc82f5f694d83b098bb6110eaa19bbde
LEFTOVER_DICT = {CoordinapeGroup.COMMUNITY: {2: 6902}}
DEFAULT_USD_REWARD_DICT = {
    CoordinapeGroup.COMMUNITY: {1: 40000, 2: 40000, 3: 40000},
    CoordinapeGroup.YSTRATEGIST: {1: 40000, 2: 40000},
}


def contributors_from_epoch(group, epoch):
    endpoint = EPOCH_RESULTS_ENDPOINT_FORMAT.format(group.value, epoch)
    r = requests.get(endpoint)
    buff = io.StringIO(r.text)
    return list(csv.DictReader(buff))


def disperse(group, epoch, safe="ychad.eth", funding_method=FundingMethod.DEPOSIT_YFI, exclusion_list=[], exclusion_type=ExclusionMethod.REDISTRIBUTE_SHARE):
    assert (
        group in DEFAULT_USD_REWARD_DICT
    ), f"{group.name} does not have a default usd reward entry"
    assert (
        epoch in DEFAULT_USD_REWARD_DICT[group]
    ), f"{group.name}'s epoch #{epoch} does not have a default usd reward entry"

    # Figure out the reward and handle leftovers from previous epoch
    reward_in_usd = DEFAULT_USD_REWARD_DICT[group][epoch]
    if group in LEFTOVER_DICT and epoch - 1 in LEFTOVER_DICT[group]:
        reward_in_usd += LEFTOVER_DICT[group][epoch - 1]

    contributors_this_epoch = [
        contributor
        for contributor in contributors_from_epoch(group, epoch)
        if int(contributor["received"]) > 0
    ]

    # REDISTRIBUTE_SHARE means we will treat excluded folks as never being in the pool
    # and their share will be distributed to others based on the other votes
    if exclusion_type == ExclusionMethod.REDISTRIBUTE_SHARE:
        contributors_this_epoch = [
            contributor 
            for contributor in contributors_this_epoch 
            if contributor["address"] not in exclusion_list
        ]

    assert (
        len(contributors_this_epoch) > 0
    ), "{group.name}'s epoch #{epoch} does not have any contributors with votes received..."

    total_votes = 0
    for contributor in contributors_this_epoch:
        total_votes += int(contributor["received"])

    safe = ApeSafe(safe)
    yfi = safe.contract("0x0bc529c00C6401aEF6D220BE8C6Ea1667F6Ad93e")
    yfi_usd_oracle = safe.contract("yfi-usd.data.eth")

    # Use price oracle to find how much YFI to allocate
    yfi_decimal_multiplicand = 10 ** yfi.decimals()
    yfi_in_usd = yfi_usd_oracle.latestAnswer() / 10 ** yfi_usd_oracle.decimals()
    yfi_allocated = (reward_in_usd / yfi_in_usd) * yfi_decimal_multiplicand

    yvyfi = safe.contract("0xE14d13d8B3b85aF791b2AADD661cDBd5E6097Db1")
    disperse = safe.contract("0xD152f549545093347A162Dce210e7293f1452150")

    yvyfi_before = yvyfi.balanceOf(safe.account)
    yfi_before = yfi.balanceOf(safe.account)
    yvyfi_to_disperse = Wei(
        (yfi_allocated * yvyfi.totalSupply()) / yvyfi.totalAssets()
    )

    if funding_method == FundingMethod.DEPOSIT_YFI:
        safe_yfi_balance = yfi.balanceOf(safe.account)
        assert safe_yfi_balance >= yfi_allocated
        yfi.approve(yvyfi, yfi_allocated)
        yvyfi.deposit(yfi_allocated)
        yvyfi_to_disperse = Wei(yvyfi.balanceOf(safe.account) - yvyfi_before)

        percentage_yvyfi_buffer = (yvyfi.balanceOf(safe.account) - yvyfi_to_disperse) / yvyfi_to_disperse
        if percentage_yvyfi_buffer < 0.01:
            print(f"Warning! This TX could fail if yvYFI's pricePerShare changes before execution.\nThe yvyfi buffer is only {percentage_yvyfi_buffer}%\n")
            print(f"Press enter to continue or Ctrl-C to exit")
            input()
    elif funding_method == FundingMethod.TRANSFER_YVYFI_FROM_TREASURY:
        treasury = safe.contract("0x93A62dA5a14C80f265DAbC077fCEE437B1a0Efde")
        assert treasury.governance() == safe.account
        assert yvyfi.balanceOf(treasury) >= yvyfi_to_disperse
        treasury_yvyfi_before = yvyfi.balanceOf(treasury)
        treasury.toGovernance(yvyfi, yvyfi_to_disperse)

    if funding_method == FundingMethod.TRANSFER_YVYFI or FundingMethod.TRANSFER_YVYFI_FROM_TREASURY:
        assert yvyfi.balanceOf(safe.account) >= yvyfi_to_disperse

    # Converting here will leave some dust
    amounts = [
        Wei(
            yvyfi_to_disperse
            * (Fraction(contributor["received"]) / Fraction(total_votes))
        )
        for contributor in contributors_this_epoch
    ]

    # REMOVE_SHARE means we will remove the excluded
    # contributors and not distribute their share. Subtract their
    # amount from the yvyfi to disperse.
    yvyfi_removed_by_exclusion = 0
    if exclusion_type == ExclusionMethod.REMOVE_SHARE:
        for contributor_address in exclusion_list:
            contributor_to_exclude = next(x for x in contributors_this_epoch if x["address"] == contributor_address)
            index = contributors_this_epoch.index(contributor_to_exclude)
            del contributors_this_epoch[index]
            yvyfi_removed_by_exclusion += amounts[index]
            yvyfi_to_disperse -= amounts[index]
            yfi_allocated -= amounts[index] * (yvyfi.totalAssets() / yvyfi.totalSupply())
            del amounts[index]

    # Dust should be less than or equal to 1 Wei per contributor due to the previous floor
    dust = yvyfi_to_disperse - sum(amounts)
    assert dust <= len(contributors_this_epoch)

    # Some lucky folks can get some dust, woot
    for i in range(math.floor(dust)):
        amounts[i] += 1

    assert sum(amounts) == yvyfi_to_disperse
    assert (
        yfi_allocated
        == yvyfi_to_disperse * (yvyfi.totalAssets() / yvyfi.totalSupply())
        or
        yfi_allocated
        == yvyfi_to_disperse * yvyfi.pricePerShare() / yfi_decimal_multiplicand
    )

    yvyfi.approve(disperse, sum(amounts))
    recipients = [contributor["address"] for contributor in contributors_this_epoch]
    recipients_yvfi_before = [yvyfi.balanceOf(recipient) for recipient in recipients]

    disperse.disperseToken(yvyfi, recipients, amounts)
    history[-1].info()

    if funding_method == FundingMethod.DEPOSIT_YFI:
        # Make sure we sent all the new yvYFI and only used as much YFI as expected
        assert yvyfi_before == yvyfi.balanceOf(safe.account)
        assert yfi_before - yfi_allocated == yfi.balanceOf(safe.account)
    elif funding_method == FundingMethod.TRANSFER_YVYFI:
        # Make sure we didn't use YFI for some reason and only used as much yvYFI as expected
        assert yfi_before == yfi.balanceOf(safe.account)
        assert yvyfi_before - yvyfi_to_disperse == yvyfi.balanceOf(safe.account)
    elif funding_method == FundingMethod.TRANSFER_YVYFI_FROM_TREASURY:
        # Make sure we didn't use YFI and only used the yvYFI from the treasury
        assert yfi_before == yfi.balanceOf(safe.account)
        assert yvyfi_before + yvyfi_removed_by_exclusion == yvyfi.balanceOf(safe.account)
        assert treasury_yvyfi_before - (yvyfi_to_disperse + yvyfi_removed_by_exclusion) == yvyfi.balanceOf(treasury)

    # For each recipient, make sure their yvYFI amount increased by the expected amount
    for recipient, yvyfi_before, amount in zip(
        recipients, recipients_yvfi_before, amounts
    ):
        assert yvyfi.balanceOf(recipient) == yvyfi_before + amount

    # Print out a table
    pricePerShare = yvyfi.pricePerShare() / yfi_decimal_multiplicand
    l = [
        [
            contributor["name"],
            contributor["address"][:6],
            contributor["received"],
            amount / yfi_decimal_multiplicand,
            "${:0.2f}".format(amount / yfi_decimal_multiplicand * yfi_in_usd * pricePerShare),
        ]
        for contributor, amount in zip(contributors_this_epoch, amounts)
    ]

    l.append(
        [
            "TOTAL", 
            "------", 
            total_votes, 
            sum(amounts) / yfi_decimal_multiplicand, 
            "${:0.2f}".format(sum(amounts) / yfi_decimal_multiplicand * yfi_in_usd * pricePerShare)
        ]
    )
    
    table = tabulate(
        l,
        headers=["Name", "Address", "Received Votes", "Amount yvYFI", "Amount USD"],
        tablefmt="orgtbl",
    )
    print(
        f"{group.name} epoch #{epoch}\nDistributing ${reward_in_usd}\nYFI price ${yfi_in_usd}\nyvYFI price per share {pricePerShare}\n"
    )
    print(table)

    safe_tx = safe.multisend_from_receipts()
    safe.preview(safe_tx)
    safe.post_transaction(safe_tx)


def disperse_yearn_community_epoch_3():
    # Exclude Oni, redistribute his share
    # Transfer yvyfi from treasury
    disperse(CoordinapeGroup.COMMUNITY, 3, "ychad.eth", FundingMethod.TRANSFER_YVYFI_FROM_TREASURY, ["0x710295b5f326c2e47e6dd2e7f6b5b0f7c5ac2f24"], ExclusionMethod.REDISTRIBUTE_SHARE)


def disperse_strategist_2():
    disperse(CoordinapeGroup.YSTRATEGIST, 2, "brain.ychad.eth", FundingMethod.TRANSFER_YVYFI)


if __name__ == "__main__":
    network.connect("mainnet-fork")
    disperse_yearn_community_epoch_3()
