import sys
import pathlib
import csv
sys.path.append(str(pathlib.Path(__file__).parent.parent.absolute()))
from brownie import *
from tabulate import tabulate
from scripts.constants import *
from scripts.configuration import *
from scripts.coordinape_enums import CoordinapeGroup, ExclusionMethod, FundingMethod
from scripts.coordinape_group_epoch import CoordinapeGroupEpoch
from scripts.contracts import Contracts
from scripts.disbursement import Disbursement


def make_table(coordinape_group_epoch, contributors_this_epoch, amounts, yfi_decimal_multiplicand, yfi_in_usd, price_per_share, total_votes):
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

    with open(f'output_{coordinape_group_epoch.group}_{coordinape_group_epoch.epoch}.csv', 'w+') as result_file:
        wr = csv.writer(result_file, dialect='excel')
        wr.writerows(l)

    table = tabulate(
        l,
        headers=["Name", "Address", "Received Votes", "Amount yvYFI", "Amount USD"],
        tablefmt="orgtbl",
    )

    return table

def disperseOneEpoch(
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
    disbursement = Disbursement(reward_in_usd, funding_method, contracts, EXPECTED_TOKEN_BUFFER)
    disbursement.prep_reward()

    amounts = disbursement.get_amounts(coordinape_group_epoch)
    return (amounts, disbursement, coordinape_group_epoch)


def disperse(
    group,
    epoch,
    safe_name=YCHAD_ETH,
    funding_method=FundingMethod.DEPOSIT_YFI,
    exclusion_list=[],
    exclusion_type=ExclusionMethod.REDISTRIBUTE_SHARE,
):
    contracts = Contracts(safe_name)
    if isinstance(epoch, int):
        amounts, disbursement, coordinape_group_epoch = disperseOneEpoch(group, epoch, safe_name, funding_method, exclusion_list, exclusion_type)
        reward_in_usd = disbursement.reward_in_usd
        rewarded_contributors_this_epoch = coordinape_group_epoch.get_rewarded_contributors_this_epoch()
        total_votes = coordinape_group_epoch.get_total_votes()
    else:
        reward_in_usd = 0
        rewarded_contributors_this_epoch = []
        coordinape_group_epoch = None
        address_to_amounts = {}
        total_votes = 0
        for e in epoch:
            amounts, disbursement, coordinape_group_epoch_i = disperseOneEpoch(group, e, safe_name, funding_method, exclusion_list, exclusion_type)
            rewarded_this_epoch = coordinape_group_epoch_i.get_rewarded_contributors_this_epoch()
            for i in range(len(amounts)):
                addr = rewarded_this_epoch[i]['address']
                if addr in address_to_amounts.keys():
                    address_to_amounts[addr] += amounts[i]
                else:
                    address_to_amounts[addr] = amounts[i]

            reward_in_usd += disbursement.reward_in_usd
            if not coordinape_group_epoch:
                coordinape_group_epoch = coordinape_group_epoch_i

            for rewarded_contributor in rewarded_this_epoch:
                total_votes += int(rewarded_contributor['received'])
                addresses = list(map(lambda x: x['address'], rewarded_contributors_this_epoch))
                if rewarded_contributor['address'] not in addresses:
                    rewarded_contributors_this_epoch.append(rewarded_contributor)
                else:
                    index = addresses.index(rewarded_contributor['address'])
                    rewarded_contributors_this_epoch[index]['received'] += ", " + rewarded_contributor['received']

        amounts = []
        for contributor in rewarded_contributors_this_epoch:
            addr = contributor["address"]
            amounts.append(address_to_amounts[addr])
            received_votes = list(map(int, contributor["received"].split(', ')))
            contributor["received"] +=  " (total: " + str(sum(received_votes)) + ")"

    if funding_method == FundingMethod.SEND_YFI:
        token = contracts.yfi
    else:
        token = contracts.yvyfi

    token.approve(contracts.disperse, sum(amounts))
    recipients = [contributor["address"] for contributor in rewarded_contributors_this_epoch]
    recipients_balance_before = [token.balanceOf(recipient) for recipient in recipients]

    contracts.disperse.disperseToken(token, recipients, amounts)
    history[-1].info()

    disbursement.check_asserts()

    # For each recipient, make sure their yvYFI amount increased by the expected amount
    for recipient, balance_before, amount in zip(
        recipients, recipients_balance_before, amounts
    ):
        assert token.balanceOf(recipient) == balance_before + amount

    # Print out a table
    if funding_method != FundingMethod.SEND_YFI:
        price_per_share = token.pricePerShare() / contracts.yfi_decimal_multiplicand
    else:
        price_per_share = 1

    table = make_table(coordinape_group_epoch, rewarded_contributors_this_epoch, amounts, contracts.yfi_decimal_multiplicand, disbursement.yfi_in_usd, price_per_share, total_votes)

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


def disperse_yearn_community_epoch_6():
    disperse(
        CoordinapeGroup.COMMUNITY,
        6,
        YCHAD_ETH,
        FundingMethod.DEPOSIT_YFI,
    )


def disperse_yearn_community_epoch_7():
    disperse(
        CoordinapeGroup.COMMUNITY,
        7,
        YCHAD_ETH,
        FundingMethod.DEPOSIT_YFI,
    )


def disperse_yearn_community_epoch_8():
    disperse(
        CoordinapeGroup.COMMUNITY,
        8,
        YCHAD_ETH,
        FundingMethod.DEPOSIT_YFI,
    )


def disperse_yearn_community_epoch_9():
    disperse(
        CoordinapeGroup.COMMUNITY,
        9,
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


def disperse_strategist_5():
    disperse(CoordinapeGroup.YSTRATEGIST, 5, BRAIN_YCHAD_ETH, FundingMethod.TRANSFER_YVYFI)


def disperse_strategist_6():
    disperse(CoordinapeGroup.YSTRATEGIST, 6, BRAIN_YCHAD_ETH, FundingMethod.TRANSFER_YVYFI)


def disperse_strategist_7():
    contracts = Contracts(BRAIN_YCHAD_ETH)
    contracts.yvyfi.setDepositLimit(contracts.yvyfi.depositLimit() * 3)
    disperse(CoordinapeGroup.YSTRATEGIST, 7, BRAIN_YCHAD_ETH, FundingMethod.DEPOSIT_ALL_YFI_TO_YVYFI)


def disperse_strategist_8():
    disperse(CoordinapeGroup.YSTRATEGIST, 8, BRAIN_YCHAD_ETH, FundingMethod.TRANSFER_YVYFI)


def disperse_yacademy_1_through_4():
    disperse(CoordinapeGroup.YACADEMY, [1,2,3,4], YCHAD_ETH, FundingMethod.SEND_YFI)

if __name__ == "__main__":
    network.connect("eth-main-fork")
    disperse_yacademy_1_through_4()
