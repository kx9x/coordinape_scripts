from scripts.constants import *
from scripts.configuration import *
from scripts.coordinape_enums import CoordinapeGroup, ExclusionMethod, FundingMethod
import requests
import io
import csv

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