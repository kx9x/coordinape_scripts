from scripts.coordinape_enums import CoordinapeGroup
# For epoch 3 in the yearn community, we need to add the leftover USDC from epoch 2.
# Below we can see that 33,098 was awarded, meaning 40,000 - 33,098 = $6902 is leftover:
# https://etherscan.io/tx/0xf401d432dcaaea39e1b593379d3d63dcdc82f5f694d83b098bb6110eaa19bbde

LEFTOVER_DICT = {CoordinapeGroup.COMMUNITY: {2: 6902}}
DEFAULT_USD_REWARD_DICT = {
    CoordinapeGroup.COMMUNITY: {1: 40_000, 2: 40_000, 3: 40_000, 4: 60_000, 5: 60_000},
    CoordinapeGroup.YSTRATEGIST: {1: 40_000, 2: 40_000, 3: 40_000, 4: 70_000, 5: 44_000},
}
